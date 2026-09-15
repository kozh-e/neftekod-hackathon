"""Узел-заглушка оптимизатора для Шага 1 MVP.

Служит временным заполнителем для ветки "optimization" до реализации
полноценного оптимизатора (SLSQP / HiGHS LP) и агентов арбитража на Шагах 2-4.
Обеспечивает сквозную компиляцию и тестирование графа вычислений.
"""

from __future__ import annotations

from typing import Dict, Any
from src.agents.state import MasGraphState, ControlCandidate, FinalRecommendation


def node_optimization_stub(state: MasGraphState) -> Dict[str, Any]:
    """Формирует базовое предложение и рекомендацию при валидных данных КИПиА."""
    # Создаем базового кандидата изменения режима
    baseline_candidate = ControlCandidate(
        candidate_id="cand_baseline_01",
        delta_u={"F15": 10.0, "T55": -0.5},
        expected_margin=1500.0
    )

    final_rec = FinalRecommendation(
        status="OPTIMIZATION_NORMAL",
        explanation="Штатный режим: данные КИПиА и анализы LIMS валидны. Выработана базовая оптимизационная уставка.",
        recommended_delta_u=baseline_candidate.delta_u
    )

    return {
        "candidates": [baseline_candidate],
        "final_recommendation": final_rec
    }
