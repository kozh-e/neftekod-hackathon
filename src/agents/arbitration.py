"""Модуль двухстадийного гибридного арбитража (Two-Stage Hybrid Arbitration).

Реализует требования Шага 4 дорожной карты MVP и System_Design.md:
1. Стадия 1 (Hard-Veto Gate): Полное отсечение кандидатов, нарушающих границы ПАЗ и ГОСТ,
   до начала экономических торгов (устранение «Экономического каннибализма»).
   Если допустимых кандидатов нет -> переход в Safe Hold (Callout Box 4 ТЗ).
2. Стадия 2 (Economic Clearing): Выбор оптимума по Net Utility (Маржа - Лог-барьеры риска).
3. Стадия 3 (Deadband Filter): Фильтр зоны нечувствительности (порог чистой маржи 1000 руб/ч,
   порог нормы изменения уставок 0.05) для защиты арматуры от механического износа.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Any
from src.agents.state import (
    MasGraphState,
    ControlCandidate,
    FinalRecommendation,
)

# =============================================================================
# TODO (Post-MVP): Децентрализованный рыночный арбитраж (Walrasian Auction / ADMM)
# В текущей версии MVP реализован двухстадийный гибридный топологический арбитраж:
# жесткое вето по ПАЗ и ГОСТ (Стадия 1) + максимизация чистой полезности (Стадия 2)
# с барьерными штрафами риска.
# Для перехода к полностью децентрализованной архитектуре без центрального узла
# арбитража (>100 строк кода) планируется:
# 1. Формализация задачи как распределенной оптимизации ADMM (Alternating Direction
#    Method of Multipliers) между агентами АВТ-6, 24-2000 и Блендинга.
# 2. Введение двойственных переменных (цен за пропускную способность змеевиков,
#    запас по температуре вспышки и серу) с итеративным аукционом Вальраса:
#    p_{k+1} = p_k + gamma * (g(x) - g_max).
# 3. Достижение распределенного консенсуса через P2P-обмен сообщениями без единой
#    точки отказа (Single Point of Failure).
# =============================================================================


class ArbitrationNode:
    """Узел Двухстадийного Гибридного Арбитража (Topological Arbitrator)."""

    @staticmethod
    def calculate_norm(delta_u: Dict[str, float]) -> float:
        """Евклидова норма вектора изменения технологических уставок (L2 norm)."""
        if not delta_u:
            return 0.0
        return math.sqrt(sum(val**2 for val in delta_u.values()))

    @classmethod
    def execute(
        cls,
        candidates: List[ControlCandidate],
        vetoed_ids: List[str],
        risk_penalties: Dict[str, float],
        min_margin_improvement: float = 1000.0,
        min_delta_norm: float = 0.05,
    ) -> Tuple[FinalRecommendation, Optional[ControlCandidate]]:
        """
        Выполняет двухстадийный арбитраж с проверкой зоны нечувствительности.

        :param candidates: Список кандидатов от оптимизатора.
        :param vetoed_ids: Список ID кандидатов, заблокированных жестким вето ПАЗ/ГОСТ.
        :param risk_penalties: Словарь ID -> Штраф риска (руб/ч) за приближение к границам.
        :param min_margin_improvement: Порог нечувствительности по марже (1000.0 руб/ч).
        :param min_delta_norm: Порог нечувствительности по размеру шага приводов (0.05).
        :return: (final_recommendation, selected_candidate)
        """
        # =========================================================================
        # СТАДИЯ 1: Hard-Veto Gate (Отсев нарушителей ПАЗ и ГОСТ)
        # =========================================================================
        vetoed_set = set(vetoed_ids)
        u_admissible = [c for c in candidates if c.candidate_id not in vetoed_set]

        if not u_admissible:
            # Все кандидаты забракованы аудиторами. Запускаем протокол мотивированного отказа
            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation=(
                    "Надёжной рекомендации нет: доступные варианты "
                    "либо нарушают ограничение по качеству, "
                    "либо выходят за заданный модельный диапазон."
                ),
                recommended_delta_u={}
            )
            return final_rec, None

        # =========================================================================
        # СТАДИЯ 2: Economic Clearing (Выбор по максимуму Net Utility)
        # =========================================================================
        best_candidate: Optional[ControlCandidate] = None
        best_net_utility = -float("inf")

        for cand in u_admissible:
            # Штраф за приближение к границам ПАЗ (лог-барьер)
            penalty = float(risk_penalties.get(cand.candidate_id, 0.0))
            net_utility = cand.expected_margin - penalty

            if net_utility > best_net_utility:
                best_net_utility = net_utility
                best_candidate = cand

        if best_candidate is None:
            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation="Ошибка выбора: допустимые кандидаты отсутствуют.",
                recommended_delta_u={}
            )
            return final_rec, None

        # =========================================================================
        # СТАДИЯ 3: Deadband Filter (Фильтр зоны нечувствительности / антидребезг)
        # =========================================================================
        # 1. Проверка минимального экономического выигрыша (порог 1000 руб/ч)
        if best_net_utility < min_margin_improvement:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_LOW_MARGIN",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) дает чистый "
                    f"прирост {best_net_utility:.2f} руб/ч, что ниже порога "
                    f"нечувствительности ({min_margin_improvement:.1f} руб/ч). "
                    "Режим оставлен без изменений для сбережения ресурса арматуры."
                ),
                recommended_delta_u={}
            )
            return final_rec, None

        # 2. Проверка точности позиционирования приводов (порог 0.05)
        delta_norm = cls.calculate_norm(best_candidate.delta_u)
        if delta_norm < min_delta_norm:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_SMALL_STEP",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) требует "
                    f"изменения уставок с нормой вектора {delta_norm:.4f}, что ниже "
                    f"аппаратной точности позиционеров ({min_delta_norm:.2f}). "
                    "Режим оставлен без изменений."
                ),
                recommended_delta_u={}
            )
            return final_rec, None

        # =========================================================================
        # ФИНАЛ: Формирование безопасной и экономически эффективной рекомендации
        # =========================================================================
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
            recommended_delta_u=best_candidate.delta_u
        )
        return final_rec, best_candidate


def node_arbitration(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел LangGraph: Двухстадийный гибридный арбитраж (Fan-In после аудиторов).
    """
    candidates = state.get("candidates", [])
    vetoed_ids = state.get("vetoed_candidates", [])
    risk_penalties = state.get("risk_penalties", {})

    final_rec, selected_cand = ArbitrationNode.execute(
        candidates=candidates,
        vetoed_ids=vetoed_ids,
        risk_penalties=risk_penalties,
        min_margin_improvement=1000.0,
        min_delta_norm=0.05,
    )

    if final_rec.status == "SUCCESS" and selected_cand is not None:
        try:
            from src.xai.narrative import XAIGenerator
            raw_telemetry = state.get("raw_telemetry")
            base_dict = {}
            lims_age = 0.0
            if raw_telemetry is not None:
                if hasattr(raw_telemetry, "model_dump"):
                    base_dict = raw_telemetry.model_dump()
                elif isinstance(raw_telemetry, dict):
                    base_dict = raw_telemetry
                lims_age = float(base_dict.get("lims_age_hours", 0.0))
            
            final_rec.markdown_report = XAIGenerator.generate_explanation(
                best_candidate=selected_cand,
                base_state=base_dict,
                risk_penalties=risk_penalties,
                lims_age_hours=lims_age
            )
        except Exception:
            pass

    return {
        "final_recommendation": final_rec,
        "selected_candidate": selected_cand
    }
