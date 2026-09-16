"""Модуль двухстадийного гибридного арбитража (Two-Stage Hybrid Arbitration).

Реализует требования Шага 4 дорожной карты и implementation_plan_v2.md:
1. Стадия 1 (Hard-Veto Gate): Полное отсечение кандидатов, нарушающих границы ПАЗ и ГОСТ.
   Если hold нарушает ограничения, активируется режим SUCCESS_CORRECTIVE без deadband по марже (ADR-9).
   Если допустимых кандидатов нет -> переход в Safe Hold с дословным текстом ТЗ и списком нарушений.
2. Стадия 2 (Economic Clearing): Выбор оптимума по Net Utility (Маржа - Лог-барьеры риска).
3. Стадия 3 (Deadband Filter): Фильтр зоны нечувствительности по марже (1000 руб/ч) и
   нормированной норме вектора уставок (порог 0.05).
4. Формирование списка альтернатив для оператора (Explainable AI / XAI).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.candidates import move_scales
from src.agents.state import (
    ControlCandidate,
    FinalRecommendation,
    MasGraphState,
    SafetyAuditReport,
)


class ArbitrationNode:
    """Узел Двухстадийного Гибридного Арбитража (Topological Arbitrator)."""

    @staticmethod
    def calculate_norm(
        delta_u: Dict[str, float], scales: Optional[Dict[str, float]] = None
    ) -> float:
        """Норма вектора изменения технологических уставок (нормированная при наличии scales)."""
        if not delta_u:
            return 0.0
        if scales:
            return math.sqrt(
                sum((val / max(scales.get(k, 1.0), 1e-6)) ** 2 for k, val in delta_u.items())
            )
        return math.sqrt(sum(val**2 for val in delta_u.values()))

    @classmethod
    def execute(
        cls,
        candidates: List[ControlCandidate],
        vetoed_ids: List[str],
        risk_penalties: Dict[str, float],
        min_margin_improvement: float = 1000.0,
        min_delta_norm: float = 0.05,
        scales: Optional[Dict[str, float]] = None,
        audit_reports: Optional[List[SafetyAuditReport]] = None,
    ) -> Tuple[FinalRecommendation, Optional[ControlCandidate]]:
        """
        Выполняет двухстадийный арбитраж с проверкой зоны нечувствительности.
        """
        vetoed_set = set(vetoed_ids)

        # 1. Поиск кандидата удержания (hold) и определение нарушений текущего режима
        hold_cand = next((c for c in candidates if c.is_hold), None)
        hold_offspec: List[str] = []
        if hold_cand is not None and hold_cand.candidate_id in vetoed_set:
            if audit_reports:
                for rep in audit_reports:
                    if rep.candidate_id == hold_cand.candidate_id and rep.is_vetoed:
                        hold_offspec.extend(rep.violated_limits)
            if not hold_offspec:
                hold_offspec.append("Качество/ПАЗ")

        # Допустимые кандидаты управления (кроме ветированных и кроме hold)
        u_admissible = [c for c in candidates if c.candidate_id not in vetoed_set and not c.is_hold]

        # Если допустимых управляющих решений нет
        if not u_admissible:
            explanation = (
                "Надёжной рекомендации нет: доступные варианты "
                "либо нарушают ограничение по качеству, "
                "либо выходят за заданный модельный диапазон."
            )
            if hold_offspec:
                limits_str = ", ".join(sorted(set(hold_offspec)))
                explanation += f" Нарушенные пределы: {limits_str}."

            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation=explanation,
                recommended_delta_u={},
            )
            return final_rec, None

        # 2. Выбор оптимума по Net Utility
        best_candidate: Optional[ControlCandidate] = None
        best_net_utility = -float("inf")

        for cand in u_admissible:
            penalty = float(risk_penalties.get(cand.candidate_id, 0.0))
            net_utility = cand.expected_margin - penalty
            if net_utility > best_net_utility:
                best_net_utility = net_utility
                best_candidate = cand

        if best_candidate is None:
            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation="Ошибка выбора: допустимые кандидаты отсутствуют.",
                recommended_delta_u={},
            )
            return final_rec, None

        # 3. Корректирующий режим (ADR-9): если hold нарушает нормативы, deadband по марже не применяется
        if hold_offspec:
            changes_str = ", ".join([f"{k} на {v:+.2f}" for k, v in best_candidate.delta_u.items()])
            chosen_penalty = risk_penalties.get(best_candidate.candidate_id, 0.0)
            limits_str = ", ".join(sorted(set(hold_offspec)))
            explanation = (
                f"Корректирующее действие для устранения нарушений ({limits_str}): "
                f"рекомендуется изменить уставки: {changes_str}. "
                f"Ожидаемый экономический эффект: {best_candidate.expected_margin:.2f} руб/ч. "
                f"Штрафной риск ПАЗ: {chosen_penalty:.2f} руб/ч. "
                f"Чистая полезность: {best_net_utility:.2f} руб/ч."
            )
            final_rec = FinalRecommendation(
                status="SUCCESS_CORRECTIVE",
                explanation=explanation,
                recommended_delta_u=best_candidate.delta_u,
            )
            return final_rec, best_candidate

        # 4. Фильтры зоны нечувствительности (Deadband Filter) при штатной работе
        # 4.1. Порог экономической маржи (1000 руб/ч)
        if best_net_utility < min_margin_improvement:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_LOW_MARGIN",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) дает чистый "
                    f"прирост {best_net_utility:.2f} руб/ч, что ниже порога "
                    f"нечувствительности ({min_margin_improvement:.1f} руб/ч). "
                    "Режим оставлен без изменений для сбережения ресурса арматуры."
                ),
                recommended_delta_u={},
            )
            return final_rec, None

        # 4.2. Порог нормированного перемещения приводов (0.05)
        delta_norm = cls.calculate_norm(best_candidate.delta_u, scales=scales)
        if delta_norm < min_delta_norm:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_SMALL_STEP",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) требует "
                    f"изменения уставок с нормой вектора {delta_norm:.4f}, что ниже "
                    f"аппаратной точности позиционеров ({min_delta_norm:.2f}). "
                    "Режим оставлен без изменений."
                ),
                recommended_delta_u={},
            )
            return final_rec, None

        # 5. Успешная оптимизационная рекомендация
        changes_str = ", ".join([f"{k} на {v:+.2f}" for k, v in best_candidate.delta_u.items()])
        chosen_penalty = risk_penalties.get(best_candidate.candidate_id, 0.0)
        explanation = (
            f"Рекомендуется изменить уставки: {changes_str}. "
            f"Ожидаемый экономический эффект: {best_candidate.expected_margin:.2f} руб/ч. "
            f"Штрафной риск ПАЗ: {chosen_penalty:.2f} руб/ч. "
            f"Чистая полезность: {best_net_utility:.2f} руб/ч."
        )

        final_rec = FinalRecommendation(
            status="SUCCESS",
            explanation=explanation,
            recommended_delta_u=best_candidate.delta_u,
        )
        return final_rec, best_candidate

    @classmethod
    def build_alternatives(
        cls,
        candidates: List[ControlCandidate],
        vetoed_ids: List[str],
        risk_penalties: Dict[str, float],
        audit_reports: Optional[List[SafetyAuditReport]] = None,
        selected_candidate: Optional[ControlCandidate] = None,
    ) -> List[Dict[str, Any]]:
        """Формирует список до 3 лучших допустимых и до 3 отклоненных альтернатив."""
        vetoed_set = set(vetoed_ids)
        reasons_map: Dict[str, List[str]] = {}
        if audit_reports:
            for rep in audit_reports:
                if rep.is_vetoed and rep.violation_reason:
                    reasons_map.setdefault(rep.candidate_id, []).append(rep.violation_reason)

        admissible = [c for c in candidates if c.candidate_id not in vetoed_set and not c.is_hold]
        admissible_sorted = sorted(
            admissible,
            key=lambda c: c.expected_margin - float(risk_penalties.get(c.candidate_id, 0.0)),
            reverse=True,
        )

        vetoed = [c for c in candidates if c.candidate_id in vetoed_set]

        alternatives: List[Dict[str, Any]] = []

        # До 3 лучших допустимых
        for c in admissible_sorted[:3]:
            pen = float(risk_penalties.get(c.candidate_id, 0.0))
            is_sel = selected_candidate is not None and c.candidate_id == selected_candidate.candidate_id
            alternatives.append({
                "candidate_id": c.candidate_id,
                "status": "admissible",
                "delta_u": c.delta_u,
                "expected_margin": c.expected_margin,
                "net_utility": round(c.expected_margin - pen, 2),
                "risk_penalty": round(pen, 2),
                "is_selected": is_sel,
                "reasons": None,
            })

        # До 3 отклоненных
        for c in vetoed[:3]:
            r_list = reasons_map.get(c.candidate_id, ["Нарушение технологических ограничений"])
            alternatives.append({
                "candidate_id": c.candidate_id,
                "status": "vetoed",
                "delta_u": c.delta_u,
                "expected_margin": c.expected_margin,
                "net_utility": None,
                "risk_penalty": None,
                "is_selected": False,
                "reasons": "; ".join(r_list),
            })

        return alternatives


def node_arbitration(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел LangGraph: Двухстадийный гибридный арбитраж (Fan-In после аудиторов).
    """
    candidates = state.get("candidates", [])
    vetoed_ids = state.get("vetoed_candidates", [])
    risk_penalties = state.get("risk_penalties", {})
    audit_reports = state.get("audit_reports", [])

    from src.twin.params import load_params
    twin_p = state.get("twin_params") or load_params()
    min_margin = float(getattr(twin_p.economics, "min_margin_improvement", 1000.0))
    econ_state = state.get("economics")
    if isinstance(econ_state, dict):
        if "min_margin_improvement" in econ_state:
            min_margin = float(econ_state["min_margin_improvement"])
        elif "min_margin" in econ_state:
            min_margin = float(econ_state["min_margin"])
    elif hasattr(econ_state, "min_margin_improvement"):
        min_margin = float(econ_state.min_margin_improvement)


    final_rec, selected_cand = ArbitrationNode.execute(
        candidates=candidates,
        vetoed_ids=vetoed_ids,
        risk_penalties=risk_penalties,
        min_margin_improvement=min_margin,
        min_delta_norm=0.05,
        scales=move_scales(),
        audit_reports=audit_reports,
    )

    alternatives = ArbitrationNode.build_alternatives(
        candidates=candidates,
        vetoed_ids=vetoed_ids,
        risk_penalties=risk_penalties,
        audit_reports=audit_reports,
        selected_candidate=selected_cand,
    )

    if final_rec.status.startswith("SUCCESS") and selected_cand is not None:
        try:
            from src.xai.narrative import XAIGenerator

            base_dict = {}
            if state.get("tags"):
                base_dict.update(state["tags"])
            raw_telemetry = state.get("raw_telemetry")
            if raw_telemetry is not None:
                if hasattr(raw_telemetry, "model_dump"):
                    base_dict.update(raw_telemetry.model_dump())
                elif isinstance(raw_telemetry, dict):
                    base_dict.update(raw_telemetry)

            lims_age = float(
                base_dict.get(
                    "lims_age_hours",
                    state.get("confidence", {}).get("lims_age_hours", 0.0),
                )
            )

            final_rec.markdown_report = XAIGenerator.generate_explanation(
                best_candidate=selected_cand,
                base_state=base_dict,
                risk_penalties=risk_penalties,
                lims_age_hours=lims_age,
                hold_prediction=state.get("hold_prediction"),
                alternatives=alternatives,
                confidence=state.get("confidence"),
                blending_recipe=state.get("blending_recipe"),
            )
        except Exception:
            pass

    return {
        "final_recommendation": final_rec,
        "selected_candidate": selected_cand,
        "alternatives": alternatives,
    }
