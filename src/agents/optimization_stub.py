"""Узел оптимизатора (генерирует кандидатов технологического режима).

Формирует набор предложений по уставкам для передачи параллельным аудиторам
(ReliabilityAgent и QualityAgent) и последующего арбитража.
"""

from __future__ import annotations

from typing import Dict, Any, List
from src.agents.state import MasGraphState, ControlCandidate, FinalRecommendation


def node_optimization_stub(state: MasGraphState) -> Dict[str, Any]:
    """Генерирует предложения кандидатов при валидных данных КИПиА."""
    existing_candidates = state.get("candidates")
    if existing_candidates:
        return {"candidates": existing_candidates}

    # Базовый штатный кандидат с безопасными параметрами технологического режима
    baseline_candidate = ControlCandidate(
        candidate_id="cand_baseline_01",
        delta_u={"F15": 10.0, "T55": -0.5, "F19": 2.0},
        expected_margin=2500.0,
        expected_t55=381.5,
        expected_w10=2.85,
        expected_p52=0.042,
        expected_f31=520.0,
        expected_sulfur=8.5,
        expected_density=832.0,
        expected_cfpp=-6.0,
        expected_flash=65.0
    )

    final_rec = FinalRecommendation(
        status="OPTIMIZATION_NORMAL",
        explanation="Штатный режим: данные КИПиА и анализы LIMS валидны. Выработаны оптимизационные кандидаты.",
        recommended_delta_u=baseline_candidate.delta_u
    )

    return {
        "candidates": [baseline_candidate],
        "final_recommendation": final_rec
    }
