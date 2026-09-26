"""Сквозные тесты живого графа core_v3 по сценариям Шага 4 (test_step4_arbitration.py).

Юнит-тесты легаси ArbitrationNode/auditors.ReliabilityAgent/QualityAgent (Hard-Veto,
Deadband, лог-барьер риска) удалены вместе с самими легаси-модулями (аудит 2026-09-20) —
эквивалентная логика в v3 покрыта test_agents_reliability.py/test_agents_quality.py и
arbitration.py::decide(). Здесь остаются только сквозные прогоны живого build_core_graph().
"""

from src.agents.graph import build_core_graph


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
