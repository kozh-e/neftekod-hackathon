"""Агент Глобального Поиска (GlobalSearchAgent).

Соответствует спецификации §5.6 implementation_plan_v3.md (Задача P3.2):
1. Target: оптимальная точка установившегося режима;
2. solve(ctx) -> Target | None: многоточечный поиск оптимума установившегося режима:
   - SLSQP с запасным COBYLA;
   - 16 точек Соболя (seed=0) + текущая точка + цель прошлого такта (тёплый старт);
   - Шансовые ограничения установившегося режима;
   - Ограничение по бюджету времени < 400 мс;
   - Проверка найденной точки теми же функциями ограничений;
3. nearest_feasible(ctx) -> Target | None: поиск ближайшей допустимой точки
   min ||W(u - u0)||^2 при всех шансовых ограничениях (используется при нарушенном hold);
4. move_towards(u0, u_target, mvs, policy, origin): масштабирование шага к цели с учетом max_move и T0.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import minimize
from scipy.stats import qmc

from src.agents.anti_windup import apply_anti_windup
from src.agents.candidates import DEFAULT_MVS, MVSpec
from src.agents.contracts import Candidate, CandidateOrigin, ConstraintSpec, Tier
from src.agents.generator import signature_of
from src.agents.policy import PolicyConfig
from src.agents.registry import ALL_SPECS, T1_SPECS, T2_SPECS, T3_SPECS
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
        if s.tier in (Tier.T1_EQUIPMENT, Tier.T2_QUALITY):
            specs.append(s)
    return specs


def _calc_spec_slack(x_dict: Dict[str, float], spec: ConstraintSpec, ctx: Any) -> float:
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
    def solve(ctx: Any, mvs: Sequence[MVSpec] = DEFAULT_MVS, budget_s: float = 0.40) -> Optional[Target]:
        """Многоточечный поиск глобального оптимума установившегося режима (SLSQP + COBYLA)."""
        t_start = time.perf_counter()
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

        # 1. Формирование стартовых точек: u0 + last_target + 16 точек Соболя
        starts: List[np.ndarray] = [np.array([u0[name] for name in mv_names], dtype=float)]
        last_target = getattr(ctx, "last_target", None)
        if last_target is not None and hasattr(last_target, "u"):
            starts.append(np.array([last_target.u.get(name, u0[name]) for name in mv_names], dtype=float))

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

        for x0 in starts:
            if time.perf_counter() - t_start > budget_s - 0.05:
                break

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
    def nearest_feasible(ctx: Any, mvs: Sequence[MVSpec] = DEFAULT_MVS, budget_s: float = 0.35) -> Optional[Target]:
        """Поиск ближайшей допустимой точки min 0.5 * ||W(u - u0)||^2 при шансовых ограничениях."""
        t_start = time.perf_counter()
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

        for x0 in starts:
            if time.perf_counter() - t_start > budget_s - 0.05:
                break

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
    ) -> Candidate:
        """Равномерное масштабирование вектора шага к цели с учетом max_move и границ T0."""
        mv_dict = {mv.name: mv for mv in mvs}
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
            req_du = diff * scale
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
