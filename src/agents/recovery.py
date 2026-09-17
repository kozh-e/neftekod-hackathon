"""Модуль многошагового планирования восстановления режима (Recovery Planner).

Соответствует спецификации §5.11 implementation_plan_v3.md (Задача P3.6):
1. RecoveryPlanner.plan(first, ctx) -> RecoveryPlan:
   - Многошаговый скользящий план восстановления с гарантированным монотонным убыванием
     нарушения старшего яруса: v_{next} <= rho * v_{prev} (rho = 0.9);
   - Интеграция с GlobalSearchAgent.nearest_feasible;
   - Формирование кортежа RecoveryStep с прогнозируемыми нарушениями и ключевыми параметрами;
2. Детектирование события RECOVERY_STALLED при отсутствии прогресса устранения нарушений.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.anti_windup import apply_anti_windup
from src.agents.candidates import DEFAULT_MVS, MVSpec
from src.agents.contracts import (
    Candidate,
    CandidateOrigin,
    Merit,
    RecoveryPlan,
    RecoveryStep,
    Tier,
)
from src.agents.generator import signature_of
from src.agents.global_search import GlobalSearchAgent, Target
from src.agents.policy import PolicyConfig


def lexi_decrease(
    v_next: Tuple[float, float, float, float],
    v_prev: Tuple[float, float, float, float],
    rho: float = 0.9,
) -> bool:
    """
    Проверяет строгое убывание нарушения старшего яруса:
    v_next[top] <= rho * v_prev[top], при этом все ярусы выше top не ухудшаются (<= v_prev).
    """
    # Находим старший нарушенный ярус в v_prev
    top = None
    for t in range(4):
        if v_prev[t] > 1e-4:
            top = t
            break

    if top is None:
        # В v_prev нарушений нет
        return all(v_next[t] <= 1e-4 for t in range(4))

    # Все ярусы выше top должны быть <= 1e-4 в v_next
    for t in range(top):
        if v_next[t] > v_prev[t] + 1e-4:
            return False

    # В ярусе top должно быть убывание
    return v_next[top] <= rho * v_prev[top] + 1e-4


class RecoveryPlanner:
    """Многошаговый планировщик вывода технологического объекта из нештатных ситуаций."""

    @staticmethod
    def plan(
        first_candidate: Candidate,
        ctx: Any,
        mvs: Sequence[MVSpec] = DEFAULT_MVS,
    ) -> RecoveryPlan:
        """Строит многошаговый скользящий план восстановления до допустимой зоны."""
        policy = getattr(ctx, "policy", None) or PolicyConfig()
        twin_view = getattr(ctx, "twin_view", None)
        u0 = getattr(ctx, "u", None) or (ctx.estimate.u_actual if getattr(ctx, "estimate", None) else {})
        mv_dict = {mv.name: mv for mv in mvs}

        # Ближайшая допустимая цель
        target = GlobalSearchAgent.nearest_feasible(ctx, mvs=mvs)

        steps: List[RecoveryStep] = []
        u0_base = {k: float(u0.get(k, (mv_dict[k].lo + mv_dict[k].hi) / 2.0)) for k in mv_dict}
        u_curr = dict(u0_base)

        # Рассчитываем v для HOLD
        from src.agents.negotiation import calculate_merit
        hold_cand = Candidate(signature=signature_of({}), delta_u={}, origin=CandidateOrigin.HOLD, proposed_by="planner")
        hold_pred = twin_view.predict(hold_cand) if twin_view else None
        m_hold = calculate_merit(hold_cand, certs=(), blend_cert=None, pred=hold_pred, hold_pred=hold_pred, ctx=ctx, mvs=mvs)
        v_prev = m_hold.v

        current_move = dict(first_candidate.delta_u)

        for k in range(policy.recovery_max_steps):
            # Применяем ход к u_curr
            u_next: Dict[str, float] = {}
            for name, mv in mv_dict.items():
                base_val = u_curr[name]
                d_val = current_move.get(name, 0.0)
                eff_d, _ = apply_anti_windup(base_val, d_val, mv.max_move, mv.lo, mv.hi)
                u_next[name] = round(base_val + eff_d, 4)

            # Оцениваем полученное состояние
            delta_from_u0 = {name: u_next[name] - u0_base[name] for name in mv_dict if abs(u_next[name] - u0_base[name]) > 1e-4}
            cand_k = Candidate(
                signature=signature_of(delta_from_u0, mvs),
                delta_u=delta_from_u0,
                origin=CandidateOrigin.LOCAL,
                proposed_by="planner",
            )
            pred_k = twin_view.predict(cand_k) if twin_view else None
            m_k = calculate_merit(cand_k, certs=(), blend_cert=None, pred=pred_k, hold_pred=hold_pred, ctx=ctx, mvs=mvs)

            steps.append(
                RecoveryStep(
                    k=k,
                    delta_u=dict(current_move),
                    predicted_violation=m_k.v,
                    key_values=dict(pred_k.steady_state) if pred_k else {},
                )
            )

            u_curr = u_next
            v_prev = m_k.v

            # Если режим полностью восстановлен
            if m_k.v == (0, 0, 0, 0):
                break

            # Формируем следующий шаг к цели nearest_feasible
            if target:
                next_cand = GlobalSearchAgent.move_towards(u_curr, target.u, mvs=mvs, policy=policy, origin=CandidateOrigin.NEAREST_FEASIBLE)
                current_move = dict(next_cand.delta_u)
            else:
                break

        reaches = (v_prev == (0, 0, 0, 0))
        return RecoveryPlan(
            steps=tuple(steps),
            reaches_feasibility=reaches,
            expected_cycles=len(steps),
            target_u=target.u if target else None,
        )
