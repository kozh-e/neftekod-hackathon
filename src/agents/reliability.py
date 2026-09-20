"""Агент Надежности: аудит технологических ограничений оборудования и ПАЗ/ESD.

Реализует спецификацию §5.4 implementation_plan_v3.md (Задача P2.2):
1. Проверка спецификаций яруса T1 (FURNACE, RX, SUPPLY);
2. T55 по измеренному значению и динамической траектории FOPDT к уставке;
3. Перепад Р-202 (HT_DP_KPA) с коэффициентом засорения и лог-доменом;
4. Предусловия печи: AVT_F31 >= 362.5 т/ч и AVT_P52 <= 0.077 кгс/см² (fail-closed для ходов AVT_T55_SP);
5. Политика FURNACE.COT_POLICY_WARM: запрет нагрева в зоне предупреждения (> 380 °C);
6. Локальный ремонт (local_repair) для устранения нарушений.
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
    SignalQuality,
    Tier,
)
from src.agents.policy import PolicyConfig
from src.agents.registry import T1_SPECS, get_constraints_by_owner
from src.agents.uncertainty import chance_effective


class ReliabilityAgent:
    """
    Агент Надежности (ярус T1_EQUIPMENT).
    """

    agent_name: str = "reliability"

    def __init__(self, specs: Optional[Sequence[ConstraintSpec]] = None) -> None:
        self.specs: Tuple[ConstraintSpec, ...] = tuple(specs or get_constraints_by_owner("reliability"))

    def certify(
        self,
        cand: Candidate,
        pred: Prediction,
        hold_pred: Prediction,
        ctx: Any,
    ) -> ConstraintCertificate:
        """
        Сертификация кандидата по ярусу T1 согласно шаблону §5.4.
        """
        policy: PolicyConfig = getattr(ctx, "policy", None) or PolicyConfig()
        estimate = getattr(ctx, "estimate", None)
        data = getattr(ctx, "data", None)
        twin_view = getattr(ctx, "twin_view", None)

        evals: List[ConstraintEvaluation] = []
        requirements: List[str] = []
        is_hold = (cand.origin.value == "HOLD" if hasattr(cand.origin, "value") else cand.origin == "HOLD") or not cand.delta_u

        # Флаг изменения перевала печи
        moves_furnace = abs(cand.delta_u.get("AVT_T55_SP", 0.0)) > 1e-4

        for spec in self.specs:
            # 1. Если ход не затрагивает зависимые MV и это не hold
            if not (spec.depends_on & set(cand.delta_u.keys())) and not is_hold:
                # Оцениваем по текущему состоянию
                evals.append(self._evaluate_state_only(spec, ctx))
                continue

            # 2. Проверка доступности критических измерений (fail-closed)
            if spec.requires_measurement and data is not None:
                meas = data.measurements.get(spec.requires_measurement)
                if meas is None or meas.quality != SignalQuality.GOOD:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.UNKNOWN,
                            reason=f"Нет достоверного измерения {spec.requires_measurement} (fail-closed)",
                        )
                    )
                    continue

            # 3. Предусловия печи: AVT_F31 >= 362.5 и AVT_P52 <= 0.077 (или <= 0.10)
            if spec.key == "FURNACE.F31_MIN":
                f31_val = estimate.value_of("AVT_F31") if estimate else None
                if f31_val is None and data is not None and "AVT_F31" in data.measurements:
                    f31_val = data.measurements["AVT_F31"].value

                if f31_val is None:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.UNKNOWN,
                            reason="Предусловие печи: нет измерения расхода сырья AVT_F31 (fail-closed)",
                        )
                    )
                    continue

                slack_f31 = (f31_val - spec.limit) / max(spec.scale, 1e-4)
                if f31_val < spec.limit:
                    status_f31 = ConstraintStatus.VIOLATED if moves_furnace else ConstraintStatus.ACTIVE
                    reason_msg = (
                        f"ПРЕДУПРЕЖДЕНИЕ ПЕЧИ (T1/ADR): AVT_F31={f31_val:.1f} т/ч < минимума {spec.limit:.1f} т/ч. "
                        f"Ходы печью AVT_T55_SP строго запрещены!"
                    )
                    requirements.append(reason_msg)
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=status_f31,
                            mean=f31_val,
                            effective=f31_val,
                            slack=slack_f31,
                            reason=reason_msg if moves_furnace else None,
                        )
                    )
                else:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.SATISFIED,
                            mean=f31_val,
                            effective=f31_val,
                            slack=slack_f31,
                        )
                    )
                continue

            if spec.key == "COL.P52_MAX":
                p52_val = estimate.value_of("AVT_P52") if estimate else None
                if p52_val is None and data is not None and "AVT_P52" in data.measurements:
                    p52_val = data.measurements["AVT_P52"].value

                if p52_val is None:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.UNKNOWN,
                            reason="Предусловие печи: нет измерения перепада AVT_P52 (fail-closed)",
                        )
                    )
                    continue

                # Порог захлебывания вакуумной колонны: 0.077 кгс/см²
                slack_p52 = (spec.limit - p52_val) / max(spec.scale, 1e-4)
                if p52_val > spec.limit:
                    status_p52 = ConstraintStatus.VIOLATED if moves_furnace else ConstraintStatus.ACTIVE
                    reason_msg = (
                        f"ПРЕДУПРЕЖДЕНИЕ ПЕЧИ (T1/ADR): AVT_P52={p52_val:.3f} кгс/см² > предела захлебывания {spec.limit:.3f} кгс/см². "
                        f"Ходы печью AVT_T55_SP строго запрещены!"
                    )
                    requirements.append(reason_msg)
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=status_p52,
                            mean=p52_val,
                            effective=p52_val,
                            slack=slack_p52,
                            reason=reason_msg if moves_furnace else None,
                        )
                    )
                else:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.SATISFIED,
                            mean=p52_val,
                            effective=p52_val,
                            slack=slack_p52,
                        )
                    )
                continue

            # 4. Политика запрета нагрева печи в зоне предупреждения (> 380 °C)
            if spec.key == "FURNACE.COT_POLICY_WARM":
                t55_now = (estimate.value_of("AVT_T55") if estimate else None) or 381.7
                delta_t55 = cand.delta_u.get("AVT_T55_SP", 0.0)
                # Запрещен именно нагрев (> 0) внутри/в сторону зоны предупреждения (> 380 °C)
                if delta_t55 > 1e-4 and t55_now >= policy.heating_warning_zone_c:
                    reason_warm = (
                        f"POLICY_VETO: нагрев печи П-3 (ΔT55={delta_t55:+.2f}°C) запрещен в зоне предупреждения "
                        f"(T55={t55_now:.1f} >= {policy.heating_warning_zone_c:.1f}°C) (антипаттерн 3 ТЗ)"
                    )
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.VIOLATED,
                            mean=t55_now,
                            effective=t55_now + delta_t55,
                            slack=-delta_t55 / spec.scale,
                            reason=reason_warm,
                        )
                    )
                    requirements.append(reason_warm)
                else:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.SATISFIED,
                            mean=t55_now,
                            effective=t55_now + delta_t55,
                            slack=1.0,
                        )
                    )
                continue

            if spec.key == "FURNACE.COT_POLICY_HOT":
                t55_now = (estimate.value_of("AVT_T55") if estimate else None) or 381.7
                delta_t55 = cand.delta_u.get("AVT_T55_SP", 0.0)
                if (t55_now + delta_t55) > 385.0 and delta_t55 > 1e-4:
                    reason_hot = f"POLICY_VETO: температура печи П-3 ({t55_now + delta_t55:.1f}°C) превышает 385.0°C"
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.VIOLATED,
                            mean=t55_now,
                            effective=t55_now + delta_t55,
                            slack=(385.0 - (t55_now + delta_t55)) / spec.scale,
                            reason=reason_hot,
                        )
                    )
                    requirements.append(reason_hot)
                else:
                    evals.append(
                        ConstraintEvaluation(
                            spec_key=spec.key,
                            tier=spec.tier,
                            status=ConstraintStatus.SATISFIED,
                            mean=t55_now,
                            effective=t55_now + delta_t55,
                            slack=1.0,
                        )
                    )
                continue

            # 5. Стандартная оценка прогноза ограничения (steady state + trajectory)
            mean_now = estimate.value_of(spec.quantity) if estimate else None
            if spec.quantity == "HT_FEED_TO_AVT" and mean_now is None:
                f30 = estimate.disturbances.get("AVT_F30", 119.3) if estimate else 119.3
                f32 = estimate.disturbances.get("AVT_F32", 100.3) if estimate else 100.3
                feed = estimate.u_actual.get("HT_FEED_SP", 219.6) if estimate else 219.6
                mean_now = feed / max(f30 + f32, 1e-3)
            p_u = pred.value(spec.quantity, spec.chance_domain, spec.sense)
            p_0 = hold_pred.value(spec.quantity, spec.chance_domain, spec.sense)

            if mean_now is None or p_u is None or p_0 is None:
                # Если нет прогноза для зависимого ограничения -> UNKNOWN
                evals.append(
                    ConstraintEvaluation(
                        spec_key=spec.key,
                        tier=spec.tier,
                        status=ConstraintStatus.UNKNOWN,
                        reason=f"Отсутствует достоверный прогноз или текущее значение для {spec.quantity}",
                    )
                )
                continue

            # Расчет шансового эффективного значения
            if spec.chance:
                eff, sigma, z = chance_effective(
                    spec=spec,
                    mean_now=mean_now,
                    pred_u=p_u,
                    pred_u0=p_0,
                    estimate=estimate,
                    delta_u=cand.delta_u,
                    policy=policy,
                    sens_model=getattr(ctx, "sens_theta", None),
                )
                hold_eff, _, _ = chance_effective(
                    spec=spec,
                    mean_now=mean_now,
                    pred_u=p_0,
                    pred_u0=p_0,
                    estimate=estimate,
                    delta_u={},
                    policy=policy,
                    sens_model=getattr(ctx, "sens_theta", None),
                )
            else:
                # Прямой контрфактический прогноз
                eff = mean_now + (p_u - p_0)
                hold_eff = mean_now
                sigma = 0.0
                z = 0.0

            # Расчет нормированного запаса (slack)
            if spec.sense == "max":
                slack = (spec.limit - eff) / max(spec.scale, 1e-4)
            else:
                slack = (eff - spec.limit) / max(spec.scale, 1e-4)

            slack = self._apply_transient_rule(spec, slack, eff, hold_eff, cand)

            # Вычисление градиента запаса через TwinView
            grad: Dict[str, float] = {}
            if twin_view is not None:
                grad = twin_view.slack_gradient(cand, spec)

            # Определение статуса
            if slack < 0.0:
                status = ConstraintStatus.VIOLATED
                reason = f"Превышение предела {spec.label}: eff={eff:.2f} {spec.unit} (предел {spec.limit:.2f})"
            elif slack < 0.05:
                status = ConstraintStatus.ACTIVE
                reason = None
            else:
                status = ConstraintStatus.SATISFIED
                reason = None

            evals.append(
                ConstraintEvaluation(
                    spec_key=spec.key,
                    tier=spec.tier,
                    status=status,
                    mean=mean_now,
                    sigma=sigma,
                    z=z,
                    effective=eff,
                    slack=round(slack, 4),
                    gradient=grad,
                    reason=reason,
                )
            )

        # Вычисление общего вердикта
        violated_count = sum(1 for e in evals if e.status == ConstraintStatus.VIOLATED)
        unknown_count = sum(1 for e in evals if e.status == ConstraintStatus.UNKNOWN)

        if unknown_count > 0:
            verdict = "UNKNOWN"
        elif violated_count > 0:
            verdict = "VIOLATED"
        else:
            verdict = "ADMISSIBLE"

        # Суммарное нарушение по ярусу T1
        v1 = sum(max(0.0, -e.slack) for e in evals if e.slack is not None and e.slack < 0.0)

        # Локальный ремонт для нарушенных или активных ограничений
        active_or_viol = [e for e in evals if e.status in (ConstraintStatus.VIOLATED, ConstraintStatus.ACTIVE)]
        repair = self._local_repair(cand, active_or_viol, ctx)

        return ConstraintCertificate(
            agent=self.agent_name,
            candidate=cand.signature,
            round=getattr(ctx, "round", 0),
            evaluations=tuple(evals),
            verdict=verdict,
            violation_by_tier={Tier.T1_EQUIPMENT.value: round(v1, 4)},
            repair=repair,
            requirements=tuple(requirements),
        )

    def _apply_transient_rule(
        self,
        spec: ConstraintSpec,
        slack: float,
        eff: float,
        hold_eff: float,
        cand: Candidate,
    ) -> float:
        """
        Применяет правило переходного процесса (§5.4):
        - 'not_worse_than_hold': если текущее состояние / hold уже нарушает предел (slack_hold < 0),
          ход допустим, если он СТРОГО улучшает ограничение (eff < hold_eff) и не ухудшает его.

        Ранее здесь был отдельный хардкод-спецкейс для spec.key == "RX.DP_MAX" — убран
        (аудит 2026-09-20): RX.DP_MAX теперь сам объявлен в registry.py с
        transient="not_worse_than_hold" (плюс отдельное chance_domain="extrema" для пиковой
        проверки траектории — см. registry.py) и покрывается общей веткой ниже.

        ВАЖНО: сравнение строгое (eff < hold_eff), а не eff <= hold_eff. Для самого hold
        (delta_u={}) eff всегда численно равен hold_eff — нестрогое "<=" ошибочно засчитывало
        нарушение hold как ACTIVE (удовлетворено), маскируя реальное нарушение hold_merit.
        Раньше это было случайно исключено хардкод-условием "HT_FEED_SP < 0" (у hold delta_u
        пуст, get(...,0.0) < 0 всегда False) — при обобщении условия эта неявная защита
        потерялась и всплыла в test_audit_e9_equipment_envelope_observed_t55_and_dp.
        """
        if spec.transient == "not_worse_than_hold":
            if hold_eff > spec.limit and eff < hold_eff - 1e-9:
                return max(slack, 0.01)
        return slack

    def _evaluate_state_only(self, spec: ConstraintSpec, ctx: Any) -> ConstraintEvaluation:
        """Оценивает текущее состояние для ограничений, не затрагиваемых данным ходом."""
        # Предусловия печи и политики печи применимы только к ходам печи
        if spec.key in ("FURNACE.F31_MIN", "COL.P52_MAX", "FURNACE.COT_POLICY_WARM", "FURNACE.COT_POLICY_HOT"):
            return ConstraintEvaluation(
                spec_key=spec.key,
                tier=spec.tier,
                status=ConstraintStatus.NOT_APPLICABLE,
                slack=1.0,
            )

        estimate = getattr(ctx, "estimate", None)
        mean_now = estimate.value_of(spec.quantity) if estimate else None

        if mean_now is None:
            return ConstraintEvaluation(
                spec_key=spec.key,
                tier=spec.tier,
                status=ConstraintStatus.NOT_APPLICABLE,
            )

        if spec.sense == "max":
            slack = (spec.limit - mean_now) / max(spec.scale, 1e-4)
        else:
            slack = (mean_now - spec.limit) / max(spec.scale, 1e-4)

        status = ConstraintStatus.VIOLATED if slack < 0.0 else (
            ConstraintStatus.ACTIVE if slack < 0.05 else ConstraintStatus.SATISFIED
        )

        return ConstraintEvaluation(
            spec_key=spec.key,
            tier=spec.tier,
            status=status,
            mean=mean_now,
            effective=mean_now,
            slack=round(slack, 4),
        )

    def _local_repair(
        self,
        cand: Candidate,
        active_evals: Sequence[ConstraintEvaluation],
        ctx: Any,
    ) -> Optional[RepairProposal]:
        """
        Локальный ремонт кандидата для восстановления допустимости.
        """
        if not active_evals:
            return None

        rep_du = dict(cand.delta_u)
        active_keys = tuple(e.spec_key for e in active_evals)

        # 1. Если нарушены ограничения печи — обнуляем ход по печи
        if any("FURNACE" in e.spec_key or "COL.P52" in e.spec_key for e in active_evals):
            if "AVT_T55_SP" in rep_du:
                rep_du["AVT_T55_SP"] = 0.0

        # 2. Если нарушен перепад давления Р-202 — снижаем подачу сырья
        dp_eval = next((e for e in active_evals if e.spec_key == "RX.DP_MAX"), None)
        if dp_eval and dp_eval.status == ConstraintStatus.VIOLATED:
            curr_feed = rep_du.get("HT_FEED_SP", 0.0)
            rep_du["HT_FEED_SP"] = curr_feed - 5.0

        # 3. Если нарушена температура выхода Р-202 — снижаем температуру входа
        tout_eval = next((e for e in active_evals if e.spec_key == "RX.T_OUT_MAX"), None)
        if tout_eval and tout_eval.status == ConstraintStatus.VIOLATED:
            curr_tin = rep_du.get("HT_TIN_SP", 0.0)
            rep_du["HT_TIN_SP"] = curr_tin - 2.0

        # 4. Если нарушена кратность ВСГ — повышаем уставку GOR
        gor_eval = next((e for e in active_evals if e.spec_key == "RX.GOR_MIN"), None)
        if gor_eval and gor_eval.status == ConstraintStatus.VIOLATED:
            curr_gor = rep_du.get("HT_GOR_SP", 0.0)
            rep_du["HT_GOR_SP"] = curr_gor + 20.0

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
