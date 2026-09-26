"""Модуль совместного QP-ремонта кандидатов управления (Repair Engine).

Соответствует спецификации §5.7 implementation_plan_v3.md (Задача P3.3):
1. Квадратичная проекция недопустимого кандидата:
   min 0.5 * ||W(Delta u - Delta u_c)||^2
   при s_i + grad_s_i^T (Delta u - Delta u_c) >= m_i для нарушенных и активных ограничений,
   с границами T0 (lo <= u0 + Delta u <= hi, |Delta u| <= max_move);
2. Если совместное решение не найдено - режим «наименьшего нарушения» (минимизация
   взвешенных невязок по ярусам T1: 1e6, T2: 1e3, T3: 1);
3. Формирование RepairProposal со структурированными запасами и флагом feasible_linear.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import minimize

from src.agents.candidates import DEFAULT_MVS, MVSpec
from src.agents.contracts import (
    Candidate,
    ConstraintEvaluation,
    ConstraintStatus,
    RepairProposal,
    Tier,
)
from src.agents.policy import PolicyConfig

# Запас устойчивости по ярусам ограничений (в нормированных единицах scale)
MARGIN_BY_TIER: Dict[Tier, float] = {
    Tier.T0_BOUNDS: 0.0,
    Tier.T1_EQUIPMENT: 0.05,
    Tier.T2_QUALITY: 0.02,
    Tier.T3_OPERATIONAL: 0.0,
}

# Веса ярусов при несовместном ремонте («наименьшее нарушение»)
TIER_WEIGHTS: Dict[Tier, float] = {
    Tier.T0_BOUNDS: 1e9,
    Tier.T1_EQUIPMENT: 1e6,
    Tier.T2_QUALITY: 1e3,
    Tier.T3_OPERATIONAL: 1.0,
}

RHO_REGULARIZATION: float = 1e-3


def t0_bounds(
    u0: Mapping[str, float],
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
    blocked_mvs: Sequence[str] = (),
) -> List[Tuple[float, float]]:
    """Формирует границы приращений Delta u с учетом физических пределов и max_move."""
    blocked_set = set(blocked_mvs)
    bounds: List[Tuple[float, float]] = []
    for mv in mvs:
        if mv.name in blocked_set or not mv.enabled:
            bounds.append((0.0, 0.0))
            continue
        curr = float(u0.get(mv.name, (mv.lo + mv.hi) / 2.0))
        b_lo = max(mv.lo - curr, -mv.max_move)
        b_hi = min(mv.hi - curr, mv.max_move)
        if b_lo > b_hi:
            b_lo = b_hi = 0.0
        bounds.append((b_lo, b_hi))
    return bounds


def qp_repair(
    cand: Candidate,
    evals: Sequence[ConstraintEvaluation],
    ctx: Any,
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
) -> Optional[RepairProposal]:
    """
    Выполняет квадратичную проекцию кандидата на совместную линейную область допустимости
    или находит компромисс наименьшего нарушения.
    """
    active = [
        e for e in evals
        if e.status in (ConstraintStatus.VIOLATED, ConstraintStatus.ACTIVE)
        and e.gradient and any(abs(v) > 1e-6 for v in e.gradient.values())
        and e.slack is not None
    ]
    if not active:
        return None

    mv_names = [mv.name for mv in mvs]
    u0 = getattr(ctx, "u", None) or (ctx.estimate.u_actual if getattr(ctx, "estimate", None) else {})
    data = getattr(ctx, "data", None)
    blocked_mvs = data.blocked_mvs if data else ()

    bounds = t0_bounds(u0, mvs=mvs, blocked_mvs=blocked_mvs)
    w_vec = np.array([1.0 / (mv.step ** 2) for mv in mvs], dtype=float)

    # Вектор x0 - приращения исходного кандидата cand.delta_u
    x0 = np.array([float(cand.delta_u.get(name, 0.0)) for name in mv_names], dtype=float)

    # 1. Линейные ограничения: s_i + grad_i^T (x - x0) >= margin_i
    constraints = []
    for e in active:
        g_arr = np.array([float(e.gradient.get(name, 0.0)) for name in mv_names], dtype=float)
        margin = MARGIN_BY_TIER.get(e.tier, 0.0)
        s0 = float(e.slack)

        def con_fn(x, g=g_arr, s=s0, m=margin):
            return s + np.dot(g, x - x0) - m

        constraints.append({"type": "ineq", "fun": con_fn})

    def obj_qp(x):
        diff = x - x0
        return 0.5 * float(np.sum(w_vec * (diff ** 2)))

    # Первая попытка: строгая QP-проекция (SLSQP)
    res = minimize(obj_qp, x0, method="SLSQP", bounds=bounds, constraints=constraints,
                   options={"maxiter": 40, "ftol": 1e-4})

    feasible_linear = False
    x_sol = res.x

    if res.success:
        feasible_linear = True
    else:
        # Вторая попытка: наименьшее нарушение (Weighted Soft Penalties)
        def obj_soft(x):
            diff = x - x0
            penalty = 0.0
            for e in active:
                g_arr = np.array([float(e.gradient.get(name, 0.0)) for name in mv_names], dtype=float)
                margin = MARGIN_BY_TIER.get(e.tier, 0.0)
                slk = float(e.slack) + np.dot(g_arr, x - x0)
                viol = max(0.0, margin - slk)
                penalty += TIER_WEIGHTS.get(e.tier, 1.0) * viol
            return penalty + RHO_REGULARIZATION * 0.5 * float(np.sum(w_vec * (diff ** 2)))

        res_soft = minimize(obj_soft, x0, method="SLSQP", bounds=bounds,
                            options={"maxiter": 40, "ftol": 1e-4})
        if res_soft.success:
            x_sol = res_soft.x
            # Проверяем, удалось ли полностью устранить нарушения
            all_satisfied = True
            for e in active:
                g_arr = np.array([float(e.gradient.get(name, 0.0)) for name in mv_names], dtype=float)
                margin = MARGIN_BY_TIER.get(e.tier, 0.0)
                if float(e.slack) + np.dot(g_arr, x_sol - x0) < margin - 1e-3:
                    all_satisfied = False
                    break
            feasible_linear = all_satisfied

    # Клиппинг к границам
    delta_dict: Dict[str, float] = {}
    for j, name in enumerate(mv_names):
        lo_b, hi_b = bounds[j]
        val = max(lo_b, min(hi_b, float(x_sol[j])))
        if abs(val) > 1e-4:
            delta_dict[name] = round(val, 4)

    # Прогноз запасов после ремонта
    predicted_slacks: Dict[str, float] = {}
    x_final = np.array([delta_dict.get(name, 0.0) for name in mv_names], dtype=float)
    for e in active:
        g_arr = np.array([float(e.gradient.get(name, 0.0)) for name in mv_names], dtype=float)
        predicted_slacks[e.spec_key] = round(float(e.slack) + np.dot(g_arr, x_final - x0), 4)

    dist = float(np.sqrt(np.sum(w_vec * ((x_final - x0) ** 2))))

    return RepairProposal(
        by="coordinator",
        target=cand.signature,
        delta_u=delta_dict,
        active_specs=tuple(e.spec_key for e in active),
        predicted_slacks=predicted_slacks,
        distance=round(dist, 4),
        feasible_linear=feasible_linear,
    )
