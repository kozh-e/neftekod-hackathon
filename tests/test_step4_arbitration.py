"""Сквозные тесты живого графа core_v3 по сценариям Шага 4 (test_step4_arbitration.py).

Юнит-тесты легаси ArbitrationNode/auditors.ReliabilityAgent/QualityAgent (Hard-Veto,
Deadband, лог-барьер риска) удалены вместе с самими легаси-модулями (аудит 2026-09-20) —
эквивалентная логика в v3 покрыта test_agents_reliability.py/test_agents_quality.py и
arbitration.py::decide(). Здесь остаются только сквозные прогоны живого build_core_graph().
"""

import uuid

from src.agents.graph import build_core_graph
from src.agents.scenarios import scenario_4_conflict_tags


def test_langgraph_step4_full_pipeline(quality_risk_tags):
    """Сквозной прогон графа: Guard -> Estimate -> Negotiation -> Arb -> Blending."""
    graph = build_core_graph()

    result = graph.invoke({"tags": quality_risk_tags})

    # Проверяем успешный арбитраж
    final_rec = result.get("final_recommendation")
    assert final_rec is not None
    assert final_rec.status.startswith("SUCCESS")
    assert len(final_rec.recommended_delta_u) > 0

    # Проверяем, что управление передано в Blending Agent и рецептура рассчитана
    recipe = result.get("recipe") or result.get("blending_certificate")
    assert recipe is not None
    assert getattr(recipe, "status", None) == "FEASIBLE" or getattr(recipe, "success", False)


def test_langgraph_step4_veto_safe_hold():
    """Сквозной прогон графа при опасных кандидатах -> ветирование и отказ от небезопасного хода."""
    graph = build_core_graph()

    # В core_v3 мы не инжектим кандидатов вручную, а даем сценарий конфликта
    result = graph.invoke({"tags": scenario_4_conflict_tags(), "session_id": f"test_veto_{uuid.uuid4().hex}"})

    final_rec = result.get("final_recommendation")
    assert final_rec is not None
    # Так как единственный выгодный кандидат ветируется, а остальные в пределах deadband,
    # должно быть DEADBAND_REJECT_LOW_MARGIN или SUCCESS_CORRECTIVE или REFUSAL_NO_SAFE_ACTION
    assert "DEADBAND" in final_rec.status or "SUCCESS" in final_rec.status or "HOLD" in final_rec.status or "REFUSAL_NO_SAFE_ACTION" in final_rec.status
    # Опасный кандидат не был принят
    selected = result.get("selected_candidate")
    if selected:
        assert selected.delta_u.get("AVT_T55_SP", 0.0) <= 0.0
