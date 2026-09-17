"""Агент Баланса Сырья и Буферного Парка (SupplyAgent).

Реализует спецификацию §5.4 implementation_plan_v3.md (Задача P2.5):
1. Виртуальный интегратор запаса буферного резервуара АВТ -> 24-2000:
   dI/dt = F30 + F32 - F9;
2. Ограничение запаса на горизонте планирования H_plan = 8 ч:
   I(t + H_plan) in [I_min, I_max], где границы задаются policy.buffer_bounds_t;
3. Ограничение предельной загрузки ГО с учетом баланса:
   F9_ss <= F_avt + I_available / H_plan;
4. Формирование ConstraintCertificate со спецификациями BUFFER.INVENTORY_MIN,
   BUFFER.INVENTORY_MAX, BUFFER.LONG_RUN_RATIO;
5. Локальный ремонт кандидатов, нарушающих материальный баланс парка.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.contracts import (
    Candidate,
    ConstraintCertificate,
    ConstraintEvaluation,
    ConstraintSpec,
    ConstraintStatus,
    Prediction,
    RepairProposal,
    Tier,
)
from src.agents.policy import PolicyConfig
from src.agents.registry import get_constraints_by_owner, BUFFER_LONG_RUN_RATIO_SPEC


class SupplyAgent:
    """
    Агент Управления Запасами Сырья (ярус T3_OPERATIONAL).
    """

    agent_name: str = "supply"

    def __init__(self, specs: Optional[Sequence[ConstraintSpec]] = None) -> None:
        if specs is not None:
            self.specs = tuple(specs)
        else:
            base = list(get_constraints_by_owner("supply"))
            if not any(s.key == "BUFFER.LONG_RUN_RATIO" for s in base):
                base.append(BUFFER_LONG_RUN_RATIO_SPEC)
            self.specs = tuple(base)

    def certify(
        self,
        cand: Candidate,
        pred: Prediction,
        hold_pred: Prediction,
        ctx: Any,
    ) -> ConstraintCertificate:
        """
        Сертификация кандидата по ограничениям сырьевого буфера T3.
        """
        policy: PolicyConfig = getattr(ctx, "policy", None) or PolicyConfig()
        estimate = getattr(ctx, "estimate", None)
        twin_view = getattr(ctx, "twin_view", None)

        h_plan = policy.h_plan_h
        i_min, i_max = policy.buffer_bounds_t

        # Текущий накопленный виртуальный запас буфера сырья, т
        i_current = float(getattr(ctx, "buffer_inventory_t", 0.0))

        # Установившиеся потоки кандидата
        ss = pred.steady_state
        # F9_ss (расход сырья ГО ДТ)
        u_feed = float(cand.delta_u.get("HT_FEED_SP", 0.0)) + (estimate.u_actual.get("HT_FEED_SP", 219.6) if estimate else 219.6)
        f9_ss = ss.get("HT_F9", ss.get("HT_FEED_SP", u_feed))

        # F_avt (выработка дизельной фракции на АВТ: F30 + F32)
        f30 = ss.get("AVT_F30", estimate.disturbances.get("AVT_F30", 119.3) if estimate else 119.3)
        f32 = ss.get("AVT_F32", estimate.disturbances.get("AVT_F32", 100.3) if estimate else 100.3)
        f_avt = ss.get("AVT_DIESEL_TPH", f30 + f32)

        # Прогноз запаса буфера на горизонте планирования:
        # I(t + H) = I(t) + (F_avt - F9_ss) * H_plan
        delta_rate = f_avt - f9_ss
        i_future = i_current + delta_rate * h_plan

        # Максимально допустимый стационарный расход ГО ДТ:
        # F9_max = F_avt + (I_current - I_min) / H_plan
        i_avail = max(0.0, i_current - i_min)
        f9_max_allowed = f_avt + (i_avail / max(h_plan, 1e-3))
        long_run_ratio = f9_ss / max(f9_max_allowed, 1e-4)

        evals: List[ConstraintEvaluation] = []
        requirements: List[str] = []

        for spec in self.specs:
            if spec.key == "BUFFER.INVENTORY_MIN":
                slack = (i_future - i_min) / max(spec.scale, 1e-4)
                status = ConstraintStatus.VIOLATED if i_future < i_min else (
                    ConstraintStatus.ACTIVE if slack < 0.05 else ConstraintStatus.SATISFIED
                )
                reason = None
                if status == ConstraintStatus.VIOLATED:
                    reason = (
                        f"ДЕФИЦИТ СЫРЬЯ (T3): прогноз запаса буфера АВТ-ГО через {h_plan:.1f} ч составит "
                        f"{i_future:.1f} т < минимума {i_min:.1f} т (F_avt={f_avt:.1f}, F9={f9_ss:.1f} т/ч)"
                    )
                    requirements.append(reason)

                grad = {}
                if twin_view is not None:
                    # d(slack)/d(HT_FEED_SP) = -H_plan / scale
                    grad = {"HT_FEED_SP": round(-h_plan / max(spec.scale, 1e-4), 4)}

                evals.append(
                    ConstraintEvaluation(
                        spec_key=spec.key,
                        tier=spec.tier,
                        status=status,
                        mean=i_current,
                        effective=round(i_future, 2),
                        slack=round(slack, 4),
                        gradient=grad,
                        reason=reason,
                    )
                )

            elif spec.key == "BUFFER.INVENTORY_MAX":
                slack = (i_max - i_future) / max(spec.scale, 1e-4)
                status = ConstraintStatus.VIOLATED if i_future > i_max else (
                    ConstraintStatus.ACTIVE if slack < 0.05 else ConstraintStatus.SATISFIED
                )
                reason = None
                if status == ConstraintStatus.VIOLATED:
                    reason = (
                        f"ПЕРЕПОЛНЕНИЕ БУФЕРА (T3): прогноз запаса сырья АВТ-ГО через {h_plan:.1f} ч составит "
                        f"{i_future:.1f} т > максимума {i_max:.1f} т"
                    )
                    requirements.append(reason)

                grad = {}
                if twin_view is not None:
                    # d(slack)/d(HT_FEED_SP) = +H_plan / scale
                    grad = {"HT_FEED_SP": round(h_plan / max(spec.scale, 1e-4), 4)}

                evals.append(
                    ConstraintEvaluation(
                        spec_key=spec.key,
                        tier=spec.tier,
                        status=status,
                        mean=i_current,
                        effective=round(i_future, 2),
                        slack=round(slack, 4),
                        gradient=grad,
                        reason=reason,
                    )
                )

            elif spec.key == "BUFFER.LONG_RUN_RATIO":
                slack = (1.0 - long_run_ratio) / max(spec.scale, 1e-4)
                status = ConstraintStatus.VIOLATED if long_run_ratio > 1.0 else (
                    ConstraintStatus.ACTIVE if slack < 0.05 else ConstraintStatus.SATISFIED
                )
                reason = None
                if status == ConstraintStatus.VIOLATED:
                    reason = (
                        f"ПРЕВЫШЕНИЕ БАЛАНСА СЫРЬЯ (T3): установившаяся подача F9={f9_ss:.1f} т/ч превышает "
                        f"допустимый баланс сырья АВТ с запасом ({f9_max_allowed:.1f} т/ч, ratio={long_run_ratio:.2f})"
                    )
                    requirements.append(reason)

                grad = {"HT_FEED_SP": round(-1.0 / (max(f9_max_allowed, 1e-4) * spec.scale), 4)}

                evals.append(
                    ConstraintEvaluation(
                        spec_key=spec.key,
                        tier=spec.tier,
                        status=status,
                        mean=round(long_run_ratio, 3),
                        effective=round(long_run_ratio, 3),
                        slack=round(slack, 4),
                        gradient=grad,
                        reason=reason,
                    )
                )

        violated_count = sum(1 for e in evals if e.status == ConstraintStatus.VIOLATED)
        unknown_count = sum(1 for e in evals if e.status == ConstraintStatus.UNKNOWN)

        if unknown_count > 0:
            verdict = "UNKNOWN"
        elif violated_count > 0:
            verdict = "VIOLATED"
        else:
            verdict = "ADMISSIBLE"

        v3 = sum(max(0.0, -e.slack) for e in evals if e.slack is not None and e.slack < 0.0)

        # Локальный ремонт
        active_or_viol = [e for e in evals if e.status in (ConstraintStatus.VIOLATED, ConstraintStatus.ACTIVE)]
        repair = self._local_repair(cand, active_or_viol, f9_max_allowed, ctx)

        return ConstraintCertificate(
            agent=self.agent_name,
            candidate=cand.signature,
            round=getattr(ctx, "round", 0),
            evaluations=tuple(evals),
            verdict=verdict,
            violation_by_tier={Tier.T3_OPERATIONAL.value: round(v3, 4)},
            repair=repair,
            requirements=tuple(requirements),
        )

    def _local_repair(
        self,
        cand: Candidate,
        active_evals: Sequence[ConstraintEvaluation],
        f9_max_allowed: float,
        ctx: Any,
    ) -> Optional[RepairProposal]:
        """Локальный ремонт кандидата для балансировки подачи сырья."""
        if not active_evals:
            return None

        rep_du = dict(cand.delta_u)
        active_keys = tuple(e.spec_key for e in active_evals)

        estimate = getattr(ctx, "estimate", None)
        u0_feed = estimate.u_actual.get("HT_FEED_SP", 219.6) if estimate else 219.6

        # Если дефицит буфера или превышение коэффициента загрузки
        if any(e.spec_key in ("BUFFER.INVENTORY_MIN", "BUFFER.LONG_RUN_RATIO") and e.status == ConstraintStatus.VIOLATED for e in active_evals):
            # Корректируем уставку подачи до максимально допустимой
            target_feed = min(u0_feed + rep_du.get("HT_FEED_SP", 0.0), f9_max_allowed)
            rep_du["HT_FEED_SP"] = round(target_feed - u0_feed, 2)

        # Если переполнение буфера
        elif any(e.spec_key == "BUFFER.INVENTORY_MAX" and e.status == ConstraintStatus.VIOLATED for e in active_evals):
            curr_feed = rep_du.get("HT_FEED_SP", 0.0)
            rep_du["HT_FEED_SP"] = curr_feed + 5.0

        dist = math.sqrt(sum((rep_du.get(k, 0.0) - cand.delta_u.get(k, 0.0)) ** 2 for k in set(rep_du) | set(cand.delta_u)))

        return RepairProposal(
            by=self.agent_name,
            target=cand.signature,
            delta_u=rep_du,
            active_specs=active_keys,
            predicted_slacks={},
            distance=round(dist, 3),
            feasible_linear=True,
        )
