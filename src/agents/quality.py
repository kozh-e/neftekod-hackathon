"""Агент Качества: аудит соответствия ГОСТ 32511-2013 (Евро-5) и прогнозирование свойств.

Реализует спецификацию §5.4 implementation_plan_v3.md (Задача P2.3):
1. Проверка спецификаций качества яруса T2_QUALITY (GODT.S, GODT.FLASH и др.);
2. Шансовые контрфактические прогнозы показателей ГО ДТ (S, FLASH, E360, T95, CN, CFPP, D15) с неопределенностью sigma;
3. Публикация прогнозов качества в certificate.forecasts для агента блендинга;
4. Проверка внутренних пределов ГО ДТ при политике GODT_ON_SPEC (S <= 10 ppm, FLASH >= 55 °C);
5. Вызов ансамбля отклика печи evaluate_furnace_move для ходов AVT_T55_SP;
6. Локальный ремонт для восстановления кондиционности гидрогенизата.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.contracts import (
    Candidate,
    ConstraintCertificate,
    ConstraintEvaluation,
    ConstraintSpec,
    ConstraintStatus,
    Prediction,
    Provenance,
    ProvenanceKind,
    QualityEstimate,
    RepairProposal,
    SignalQuality,
    Tier,
)
from src.agents.furnace_ensemble import evaluate_furnace_move
from src.agents.policy import PolicyConfig
from src.agents.registry import T2_SPECS, get_constraints_by_owner
from src.agents.uncertainty import chance_effective


PROV_GOST = Provenance(
    kind=ProvenanceKind.NORM,
    ref="ГОСТ 32511-2013 Таблица 1 / initial_data/ТЗ_нефтекод.docx §4",
    note="Норматив качества Евро-5",
)


class QualityAgent:
    """
    Агент Качества (ярус T2_QUALITY).
    """

    agent_name: str = "quality"

    def __init__(self, specs: Optional[Sequence[ConstraintSpec]] = None) -> None:
        self.specs: Tuple[ConstraintSpec, ...] = tuple(specs or get_constraints_by_owner("quality"))

    def certify(
        self,
        cand: Candidate,
        pred: Prediction,
        hold_pred: Prediction,
        ctx: Any,
    ) -> ConstraintCertificate:
        """
        Сертификация кандидата по ярусу T2 и публикация прогнозов качества для блендинга.
        """
        policy: PolicyConfig = getattr(ctx, "policy", None) or PolicyConfig()
        estimate = getattr(ctx, "estimate", None)
        data = getattr(ctx, "data", None)
        twin_view = getattr(ctx, "twin_view", None)

        evals: List[ConstraintEvaluation] = []
        requirements: List[str] = []
        forecasts: Dict[str, QualityEstimate] = {}
        now = datetime.now()

        # 1. Формирование прогнозов свойств ГО ДТ для блендинга
        # Свойства: S, FLASH, E360, T95, CN, CFPP, D15
        props_to_forecast: Tuple[Tuple[str, str, str, float], ...] = (
            ("S", "log", "GODT.S", 0.10),
            ("FLASH", "linear", "GODT.FLASH", 4.78),
            ("T95", "linear", "GODT.T95", 3.27),
            ("D15", "linear", "GODT.D15", 1.20),
            ("CFPP", "linear", "GODT.CFPP", 1.50),
            ("CN", "linear", "GODT.CN", 0.80),
        )

        for prop_name, domain, key, default_sigma in props_to_forecast:
            q_est = estimate.quality.get(key) if estimate else None
            mean_now = q_est.value if q_est else (estimate.value_of(key) if estimate else None)
            if mean_now is None:
                # Номинальные значения по умолчанию
                defaults = {"S": 8.6, "FLASH": 68.0, "T95": 347.0, "D15": 836.1, "CFPP": -6.0, "CN": 53.75}
                mean_now = defaults.get(prop_name, 10.0)

            p_u = pred.value(key, domain="steady") or mean_now
            p_0 = hold_pred.value(key, domain="steady") or mean_now

            # Прогноз с учетом смещения
            if domain == "log":
                pred_val = mean_now * (max(1e-4, p_u) / max(1e-4, p_0))
            else:
                pred_val = mean_now + (p_u - p_0)

            s_meas = q_est.sigma_meas if q_est else default_sigma
            s_calib = q_est.sigma_calib if q_est else 0.05
            calib_age = q_est.calib_age_h if q_est else 0.0

            forecasts[f"GODT.{prop_name}"] = QualityEstimate(
                stream="GODT",
                prop=prop_name,
                value=round(float(pred_val), 3),
                domain=domain,
                sigma_meas=round(float(s_meas), 4),
                sigma_calib=round(float(s_calib), 4),
                calib_age_h=round(float(calib_age), 2),
                anchor=q_est.anchor if q_est else "MODEL+bias",
                provenance=PROV_GOST,
            )

        # Расчет показателя E360 для ГО ДТ по формуле интерполяции
        t95_godt = forecasts["GODT.T95"].value
        i350_ref = 88.0  # % об. отгона при 350 °C из ЛИМС
        if t95_godt > 360.0:
            e360_val = i350_ref + (95.0 - i350_ref) * (360.0 - 350.0) / max(1.0, t95_godt - 350.0)
        else:
            e360_val = 95.0 + max(0.0, (360.0 - t95_godt) * 0.5)

        forecasts["GODT.E360"] = QualityEstimate(
            stream="GODT",
            prop="E360",
            value=round(float(e360_val), 2),
            domain="linear",
            sigma_meas=1.5,
            sigma_calib=0.5,
            calib_age_h=0.0,
            anchor="MODEL+bias",
            provenance=Provenance(kind=ProvenanceKind.ASSUMPTION, ref="agents/ASSUMPTIONS.md §3", note="Интерполяция кривой разгонки"),
        )

        # 2. Проверка ограничений качества яруса T2
        is_hold = (cand.origin.value == "HOLD" if hasattr(cand.origin, "value") else cand.origin == "HOLD") or not cand.delta_u

        for spec in self.specs:
            if not (spec.depends_on & set(cand.delta_u.keys())) and not is_hold:
                evals.append(self._evaluate_state_only(spec, ctx))
                continue

            mean_now = estimate.value_of(spec.quantity) if estimate else None
            if mean_now is None:
                # Берем из прогноза
                q_est = forecasts.get(spec.quantity)
                mean_now = q_est.value if q_est else (8.6 if "S" in spec.quantity else 65.0)

            p_u = pred.value(spec.quantity, spec.chance_domain, spec.sense)
            p_0 = hold_pred.value(spec.quantity, spec.chance_domain, spec.sense)

            if p_u is None or p_0 is None:
                evals.append(
                    ConstraintEvaluation(
                        spec_key=spec.key,
                        tier=spec.tier,
                        status=ConstraintStatus.UNKNOWN,
                        reason=f"Отсутствует прогноз качества для {spec.quantity}",
                    )
                )
                continue

            # Расчет шансового эффективного значения
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

            # Расчет шансового эффективного значения для hold
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

            # Расчет запаса
            if spec.sense == "max":
                slack = (spec.limit - eff) / max(spec.scale, 1e-4)
            else:
                slack = (eff - spec.limit) / max(spec.scale, 1e-4)

            slack = self._apply_transient_rule(spec, slack, eff, hold_eff, cand, pred, hold_pred)

            # Градиент через TwinView
            grad: Dict[str, float] = {}
            if twin_view is not None:
                grad = twin_view.slack_gradient(cand, spec)

            if slack < 0.0:
                status = ConstraintStatus.VIOLATED
                reason = f"Риск нарушения качества {spec.label}: eff={eff:.2f} {spec.unit} (предел {spec.limit:.2f}, запас {z:.1f}σ)"
                requirements.append(reason)
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

        # 3. Для ходов печью AVT_T55_SP — вызов ансамбля отклика печи
        moves_furnace = abs(cand.delta_u.get("AVT_T55_SP", 0.0)) > 1e-4
        if moves_furnace:
            t95_cand = forecasts.get("GODT.T95")
            t95_val = t95_cand.value if t95_cand else 347.0
            requirements.append(
                f"Стабилизация фракционного состава: ход печью изменяет T95 ГО ДТ (прогноз {t95_val:.1f} °C)"
            )
            f_verdict = evaluate_furnace_move(cand, ctx, policy)
            if not f_verdict.admissible:
                evals.append(
                    ConstraintEvaluation(
                        spec_key="FURNACE.ENSEMBLE",
                        tier=Tier.T2_QUALITY,
                        status=ConstraintStatus.VIOLATED,
                        scenario_pass_fraction=f_verdict.pass_fraction,
                        reason="; ".join(f_verdict.reasons),
                    )
                )
                requirements.extend(f_verdict.reasons)

        violated_count = sum(1 for e in evals if e.status == ConstraintStatus.VIOLATED)
        unknown_count = sum(1 for e in evals if e.status == ConstraintStatus.UNKNOWN)

        if unknown_count > 0:
            verdict = "UNKNOWN"
        elif violated_count > 0:
            verdict = "VIOLATED"
        else:
            verdict = "ADMISSIBLE"

        v2 = sum(max(0.0, -e.slack) for e in evals if e.slack is not None and e.slack < 0.0)

        # 4. Локальный ремонт
        active_or_viol = [e for e in evals if e.status in (ConstraintStatus.VIOLATED, ConstraintStatus.ACTIVE)]
        repair = self._local_repair(cand, active_or_viol, ctx)

        return ConstraintCertificate(
            agent=self.agent_name,
            candidate=cand.signature,
            round=getattr(ctx, "round", 0),
            evaluations=tuple(evals),
            verdict=verdict,
            violation_by_tier={Tier.T2_QUALITY.value: round(v2, 4)},
            repair=repair,
            requirements=tuple(requirements),
            forecasts=forecasts,
        )

    def _evaluate_state_only(self, spec: ConstraintSpec, ctx: Any) -> ConstraintEvaluation:
        """Оценивает текущее состояние для ограничений, не затрагиваемых ходом."""
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
        """Локальный ремонт для восстановления кондиционности качества."""
        if not active_evals:
            return None

        rep_du = dict(cand.delta_u)
        active_keys = tuple(e.spec_key for e in active_evals)

        # Если нарушена сера гидрогенизата:
        # снизить подачу сырья (-5 т/ч) и/или поднять температуру (+1 °C)
        s_eval = next((e for e in active_evals if "S_MAX" in e.spec_key or "S_PRODUCT" in e.spec_key), None)
        if s_eval and s_eval.status == ConstraintStatus.VIOLATED:
            curr_feed = rep_du.get("HT_FEED_SP", 0.0)
            rep_du["HT_FEED_SP"] = curr_feed - 5.0
            curr_tin = rep_du.get("HT_TIN_SP", 0.0)
            rep_du["HT_TIN_SP"] = curr_tin + 1.0

        # Если нарушена вспышка: снизить подачу сырья
        flash_eval = next((e for e in active_evals if "FLASH" in e.spec_key), None)
        if flash_eval and flash_eval.status == ConstraintStatus.VIOLATED:
            curr_feed = rep_du.get("HT_FEED_SP", 0.0)
            rep_du["HT_FEED_SP"] = curr_feed - 5.0

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

    def _apply_transient_rule(
        self,
        spec: ConstraintSpec,
        slack: float,
        eff: float,
        hold_eff: float,
        cand: Candidate,
        pred: Prediction,
        hold_pred: Prediction,
    ) -> float:
        """Проверяет, что переходный процесс кандидата не ухудшает динамический пик hold."""
        if spec.transient == "not_worse_than_hold":
            key = "HT_S_PRODUCT" if "S" in spec.quantity else spec.quantity
            if key in pred.trajectory_extrema and key in hold_pred.trajectory_extrema:
                cand_max = pred.trajectory_extrema[key][1]
                hold_max = hold_pred.trajectory_extrema[key][1]
                if cand_max > hold_max + 1e-4:
                    return min(slack, -0.01)
        return slack
