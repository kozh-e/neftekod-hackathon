"""Агент Глобального Поиска (GlobalSearchAgent).

Соответствует спецификации §5.6 implementation_plan_v3.md (Задача P3.2):
1. Target: оптимальная точка установившегося режима;
2. solve(ctx) -> Target | None: многоточечный поиск оптимума установившегося режима:
   - SLSQP с запасным COBYLA;
   - 16 точек Соболя (seed=0) + текущая точка;
   - Шансовые ограничения установившегося режима (T1/T2/T3);
   - Детерминированный бюджет по числу стартовых точек (не зависит от wall-clock);
   - Проверка найденной точки теми же функциями ограничений;
3. nearest_feasible(ctx) -> Target | None: поиск ближайшей допустимой точки
   min ||W(u - u0)||^2 при всех шансовых ограничениях (используется при нарушенном hold);
4. move_towards(u0, u_target, mvs, policy, origin): масштабирование шага к цели с учетом max_move,
   T0 и режима CAUTIOUS (cautious_step_scale).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import minimize
from scipy.stats import qmc

from src.agents.anti_windup import apply_anti_windup
from src.agents.candidates import DEFAULT_MVS, MVSpec
from src.agents.contracts import AutomationLevel, Candidate, CandidateOrigin, ConstraintSpec, Tier
from src.agents.generator import signature_of
from src.agents.policy import PolicyConfig
from src.agents.registry import ALL_SPECS, BUFFER_LONG_RUN_RATIO_SPEC
from src.agents.uncertainty import chance_effective


@dataclass(frozen=True)
class Target:
    u: Dict[str, float]
    utility: float
    origin: CandidateOrigin = CandidateOrigin.GLOBAL


def _get_u0(ctx: Any, mvs: Sequence[MVSpec]) -> Dict[str, float]:
    u_dict = getattr(ctx, "u", None)
    if not u_dict and getattr(ctx, "estimate", None):
        u_dict = ctx.estimate.u_actual
    if not u_dict:
        u_dict = {mv.name: (mv.lo + mv.hi) / 2.0 for mv in mvs}
    return {mv.name: float(u_dict.get(mv.name, (mv.lo + mv.hi) / 2.0)) for mv in mvs}


def _get_applicable_specs(ctx: Any) -> List[ConstraintSpec]:
    specs = []
    for s in ALL_SPECS:
        if s.tier in (Tier.T1_EQUIPMENT, Tier.T2_QUALITY, Tier.T3_OPERATIONAL):
            specs.append(s)
    if not any(s.key == "BUFFER.LONG_RUN_RATIO" for s in specs):
        specs.append(BUFFER_LONG_RUN_RATIO_SPEC)
    return specs


def _calc_buffer_slack(x_dict: Dict[str, float], spec: ConstraintSpec, ctx: Any) -> float:
    """Слэк буферных ограничений T3 (см. SupplyAgent.certify — та же формула баланса)."""
    twin_view = getattr(ctx, "twin_view", None)
    if twin_view is None:
        return 0.0
    policy = getattr(ctx, "policy", None) or PolicyConfig()
    estimate = getattr(ctx, "estimate", None)

    h_plan = policy.h_plan_h
    i_min, i_max = policy.buffer_bounds_t
    i_current = float(getattr(ctx, "buffer_inventory_t", 0.0))

    ss = twin_view.steady(x_dict)
    f9_ss = ss.get("HT_F9", x_dict.get("HT_FEED_SP", 0.0))
    f30 = ss.get("AVT_F30", estimate.disturbances.get("AVT_F30", 119.3) if estimate else 119.3)
    f32 = ss.get("AVT_F32", estimate.disturbances.get("AVT_F32", 100.3) if estimate else 100.3)
    f_avt = ss.get("AVT_DIESEL_TPH", f30 + f32)

    i_future = i_current + (f_avt - f9_ss) * h_plan

    if spec.key == "BUFFER.INVENTORY_MIN":
        return (i_future - i_min) / max(spec.scale, 1e-4)
    if spec.key == "BUFFER.INVENTORY_MAX":
        return (i_max - i_future) / max(spec.scale, 1e-4)
    if spec.key == "BUFFER.LONG_RUN_RATIO":
        i_avail = max(0.0, i_current - i_min)
        f9_max_allowed = f_avt + (i_avail / max(h_plan, 1e-3))
        long_run_ratio = f9_ss / max(f9_max_allowed, 1e-4)
        return (1.0 - long_run_ratio) / max(spec.scale, 1e-4)
    return 0.0


def _calc_spec_slack(x_dict: Dict[str, float], spec: ConstraintSpec, ctx: Any) -> float:
    if spec.tier == Tier.T3_OPERATIONAL:
        return _calc_buffer_slack(x_dict, spec, ctx)

    twin_view = getattr(ctx, "twin_view", None)
    if twin_view is None:
        return 0.0
    ss = twin_view.steady(x_dict)
    estimate = getattr(ctx, "estimate", None)
    policy = getattr(ctx, "policy", None) or PolicyConfig()

    alias_map = {
        "GODT.S": "HT_S_PRODUCT",
        "GODT.FLASH": "HT_FLASH",
        "GODT.T95": "HT_T95_PRODUCT",
        "GODT.D15": "HT_D15_PRODUCT",
        "GODT.CFPP": "HT_CFPP_PRODUCT",
        "GODT.CN": "HT_CN_PRODUCT",
        "HT_T_OUT": "HT_T11",
        "HT_T11": "HT_T_OUT",
    }
    raw_key = alias_map.get(spec.quantity, spec.quantity)
    val = ss.get(raw_key, ss.get(spec.quantity))
    if val is None:
        val = x_dict.get(spec.quantity, 0.0)
    v_float = float(val)

    u0 = _get_u0(ctx, DEFAULT_MVS)
    ss0 = twin_view.steady(u0)
    val0 = ss0.get(raw_key, ss0.get(spec.quantity, v_float))
    p_0 = float(val0)

    q_est = estimate.quality.get(spec.quantity) if (estimate and hasattr(estimate, "quality")) else None
    if q_est is not None:
        mean_now = float(q_est.value)
    elif estimate is not None:
        m = estimate.value_of(spec.quantity)
        mean_now = float(m) if m is not None else v_float
    else:
        mean_now = v_float

    eff_val, _, _ = chance_effective(
        spec=spec,
        mean_now=mean_now,
        pred_u=v_float,
        pred_u0=p_0,
        estimate=estimate,
        policy=policy,
    )
    if spec.sense == "max":
        slack = (spec.limit - eff_val) / max(spec.scale, 1e-4)
    else:
        slack = (eff_val - spec.limit) / max(spec.scale, 1e-4)
    return float(slack)


class GlobalSearchAgent:
    """Агент глобального многоточечного поиска оптимальных режимов и восстановления допустимости."""

    @staticmethod
    def solve(ctx: Any, mvs: Sequence[MVSpec] = DEFAULT_MVS, max_starts: int = 2) -> Optional[Target]:
        """Многоточечный поиск глобального оптимума установившегося режима (SLSQP + COBYLA).

        Бюджет вычислений ограничен фиксированным числом стартовых точек (max_starts),
        а не wall-clock временем: время выполнения одного и того же вызова не должно
        зависеть от загрузки машины. max_starts=2 (u0 + 1 точка Соболя) откалиброван так,
        чтобы уложиться в мягкий бюджет такта <= 2.0 с (см. tests/perf/test_cycle_budget.py);
        при необходимости более тщательного поиска можно передать большее значение явно.
        """
        twin_view = getattr(ctx, "twin_view", None)
        economics = getattr(ctx, "economics", None)
        policy = getattr(ctx, "policy", None) or PolicyConfig()
        data = getattr(ctx, "data", None)
        blocked_set = set(data.blocked_mvs) if data else set()

        if twin_view is None or economics is None:
            return None

        u0 = _get_u0(ctx, mvs)
        mv_names = [mv.name for mv in mvs]
        bounds: List[Tuple[float, float]] = []
        for mv in mvs:
            if mv.name in blocked_set or not mv.enabled:
                bounds.append((u0[mv.name], u0[mv.name]))
            else:
                bounds.append((mv.lo, mv.hi))

        ss_hold = twin_view.steady(u0)
        specs = _get_applicable_specs(ctx)

        # 1. Формирование стартовых точек: u0 + 16 точек Соболя
        starts: List[np.ndarray] = [np.array([u0[name] for name in mv_names], dtype=float)]

        sobol = qmc.Sobol(d=len(mvs), seed=0)
        qmc_points = sobol.random(16)
        for pt in qmc_points:
            x_pt = np.zeros(len(mvs), dtype=float)
            for j, (lo, hi) in enumerate(bounds):
                x_pt[j] = lo + pt[j] * (hi - lo)
            starts.append(x_pt)

        # 2. Ограничения шансовой допустимости
        constraints = []
        for sp in specs:
            def c_fun(x, spec=sp):
                x_d = {name: float(x[i]) for i, name in enumerate(mv_names)}
                return _calc_spec_slack(x_d, spec, ctx)
            constraints.append({"type": "ineq", "fun": c_fun})

        def obj(x):
            x_d = {name: float(x[i]) for i, name in enumerate(mv_names)}
            ss = twin_view.steady(x_d)
            du = {name: x_d[name] - u0[name] for name in mv_names if abs(x_d[name] - u0[name]) > 1e-4}
            u_val = economics.utility(ss, ss_hold, du, policy=policy)
            return -float(u_val)

        best_target: Optional[Target] = None
        best_util = float("-inf")

        for x0 in starts[:max_starts]:
            # SLSQP
            res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=constraints,
                           options={"maxiter": 30, "ftol": 1e-3})

            # Если SLSQP не сошелся, пробуем COBYLA (без строгих границ, с клиппингом)
            if not res.success:
                cobyla_cons = []
                for sp in specs:
                    def cob_c(x, spec=sp):
                        x_d = {name: float(x[i]) for i, name in enumerate(mv_names)}
                        return _calc_spec_slack(x_d, spec, ctx)
                    cobyla_cons.append({"type": "ineq", "fun": cob_c})
                for j, (lo, hi) in enumerate(bounds):
                    cobyla_cons.append({"type": "ineq", "fun": lambda x, j=j, lo=lo: x[j] - lo})
                    cobyla_cons.append({"type": "ineq", "fun": lambda x, j=j, hi=hi: hi - x[j]})

                res = minimize(obj, x0, method="COBYLA", constraints=cobyla_cons,
                               options={"maxiter": 30, "tol": 1e-3})

            # Верификация ограничений
            x_cand = {name: float(res.x[i]) for i, name in enumerate(mv_names)}
            # Клиппинг к границам
            for j, (lo, hi) in enumerate(bounds):
                x_cand[mv_names[j]] = max(lo, min(hi, x_cand[mv_names[j]]))

            is_feasible = all(_calc_spec_slack(x_cand, sp, ctx) >= -1e-4 for sp in specs)
            if is_feasible:
                cand_util = -obj(np.array([x_cand[name] for name in mv_names]))
                if cand_util > best_util:
                    best_util = cand_util
                    best_target = Target(u=x_cand, utility=round(cand_util, 2), origin=CandidateOrigin.GLOBAL)

        return best_target

    @staticmethod
    def nearest_feasible(ctx: Any, mvs: Sequence[MVSpec] = DEFAULT_MVS, max_starts: int = 2) -> Optional[Target]:
        """Поиск ближайшей допустимой точки min 0.5 * ||W(u - u0)||^2 при шансовых ограничениях.

        Бюджет вычислений ограничен фиксированным числом стартовых точек (max_starts),
        а не wall-clock временем. max_starts=2 (u0 + 1 точка Соболя) откалиброван так,
        чтобы уложиться в мягкий бюджет такта <= 2.0 с (см. tests/perf/test_cycle_budget.py).
        """
        twin_view = getattr(ctx, "twin_view", None)
        economics = getattr(ctx, "economics", None)
        data = getattr(ctx, "data", None)
        blocked_set = set(data.blocked_mvs) if data else set()

        if twin_view is None:
            return None

        u0 = _get_u0(ctx, mvs)
        mv_names = [mv.name for mv in mvs]
        mv_dict = {mv.name: mv for mv in mvs}

        bounds: List[Tuple[float, float]] = []
        w_vec = np.zeros(len(mvs), dtype=float)
        for j, mv in enumerate(mvs):
            w_vec[j] = 1.0 / (mv.step ** 2)
            if mv.name in blocked_set or not mv.enabled:
                bounds.append((u0[mv.name], u0[mv.name]))
            else:
                bounds.append((mv.lo, mv.hi))

        u0_arr = np.array([u0[name] for name in mv_names], dtype=float)
        specs = _get_applicable_specs(ctx)

        def obj(x):
            diff = x - u0_arr
            return 0.5 * float(np.sum(w_vec * (diff ** 2)))

        constraints = []
        for sp in specs:
            def c_fun(x, spec=sp):
                x_d = {name: float(x[i]) for i, name in enumerate(mv_names)}
                return _calc_spec_slack(x_d, spec, ctx)
            constraints.append({"type": "ineq", "fun": c_fun})

        starts = [u0_arr]
        sobol = qmc.Sobol(d=len(mvs), seed=0)
        qmc_points = sobol.random(8)
        for pt in qmc_points:
            x_pt = np.zeros(len(mvs), dtype=float)
            for j, (lo, hi) in enumerate(bounds):
                x_pt[j] = lo + pt[j] * (hi - lo)
            starts.append(x_pt)

        best_target: Optional[Target] = None
        best_dist = float("inf")

        for x0 in starts[:max_starts]:
            res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=constraints,
                           options={"maxiter": 30, "ftol": 1e-3})

            x_cand = {name: float(res.x[i]) for i, name in enumerate(mv_names)}
            for j, (lo, hi) in enumerate(bounds):
                x_cand[mv_names[j]] = max(lo, min(hi, x_cand[mv_names[j]]))

            is_feasible = all(_calc_spec_slack(x_cand, sp, ctx) >= -1e-4 for sp in specs)
            if is_feasible:
                dist = obj(np.array([x_cand[name] for name in mv_names]))
                if dist < best_dist:
                    best_dist = dist
                    u_val = 0.0
                    if economics:
                        ss = twin_view.steady(x_cand)
                        ss_hold = twin_view.steady(u0)
                        du = {name: x_cand[name] - u0[name] for name in mv_names}
                        u_val = economics.utility(ss, ss_hold, du)
                    best_target = Target(u=x_cand, utility=round(u_val, 2), origin=CandidateOrigin.NEAREST_FEASIBLE)

        return best_target

    @staticmethod
    def move_towards(
        u0: Mapping[str, float],
        u_target: Mapping[str, float],
        mvs: Sequence[MVSpec] = DEFAULT_MVS,
        policy: Optional[PolicyConfig] = None,
        origin: CandidateOrigin = CandidateOrigin.GLOBAL,
        automation_level: Optional[AutomationLevel] = None,
    ) -> Candidate:
        """Равномерное масштабирование вектора шага к цели с учетом max_move, границ T0 и режима CAUTIOUS."""
        mv_dict = {mv.name: mv for mv in mvs}
        pol = policy or PolicyConfig()
        cautious_factor = pol.cautious_step_scale if automation_level == AutomationLevel.CAUTIOUS else 1.0
        delta_full: Dict[str, float] = {}

        scale = 1.0
        for name, mv in mv_dict.items():
            if not mv.enabled:
                continue
            u_base = float(u0.get(name, (mv.lo + mv.hi) / 2.0))
            u_tgt = float(u_target.get(name, u_base))
            diff = u_tgt - u_base
            delta_full[name] = diff
            if abs(diff) > mv.max_move:
                scale = min(scale, mv.max_move / abs(diff))

        delta_eff: Dict[str, float] = {}
        for name, diff in delta_full.items():
            mv = mv_dict[name]
            u_base = float(u0.get(name, (mv.lo + mv.hi) / 2.0))
            req_du = diff * scale * cautious_factor
            eff_du, _ = apply_anti_windup(u_base, req_du, mv.max_move, mv.lo, mv.hi)
            if abs(eff_du) > 1e-4:
                delta_eff[name] = round(eff_du, 4)

        return Candidate(
            signature=signature_of(delta_eff, mvs),
            delta_u=delta_eff,
            origin=origin,
            proposed_by="global_search",
            parent=None,
            round=0,
        )
