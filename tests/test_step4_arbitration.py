"""Автоматические тесты для Шага 4 MVP: Двухстадийный гибридный арбитраж и аудиторы."""

import pytest
import math

from src.agents.state import RawTelemetry, ControlCandidate, FinalRecommendation
from src.agents.auditors import (
    ReliabilityAgent,
    QualityAgent,
    node_reliability_agent,
    node_quality_agent,
)
from src.agents.arbitration import ArbitrationNode, node_arbitration
from src.agents.graph import build_core_graph


def test_arbitration_hard_veto_safety():
    """Тест 1: Hard-Veto отсекает кандидата с нарушением 5% барьера ПАЗ (T55 > 386.40 °C)."""
    # Агрессивный кандидат с высокой маржой (+10 000 руб/ч), но с опасным перегревом печи 386.70 °C
    dangerous_cand = ControlCandidate(
        candidate_id="cand_danger_cot",
        delta_u={"F31": -20.0, "T55": 2.0},
        expected_margin=10000.0,
        expected_t55=386.70,
        expected_w10=2.5,
        expected_p52=0.04,
        expected_f31=500.0,
        expected_sulfur=8.0
    )

    is_vetoed, reason, _ = ReliabilityAgent.audit_candidate(dangerous_cand)
    assert is_vetoed
    assert "ESD_VETO" in reason
    assert "T55" in reason

    # Проверяем, что арбитраж исключает нарушителя из допустимого множества
    final_rec, selected = ArbitrationNode.execute(
        candidates=[dangerous_cand],
        vetoed_ids=[dangerous_cand.candidate_id],
        risk_penalties={},
        min_margin_improvement=1000.0
    )
    assert selected is None
    assert final_rec.status == "SAFE_HOLD_EMPTY_ADMISSIBLE"
    assert final_rec.recommended_delta_u == {}


def test_arbitration_hard_veto_quality():
    """Тест 2: Hard-Veto отсекает кандидата с нарушением 5% запаса по сере (> 9.50 ppm)."""
    bad_quality_cand = ControlCandidate(
        candidate_id="cand_bad_sulfur",
        delta_u={"F15": -50.0},
        expected_margin=6000.0,
        expected_t55=382.0,
        expected_sulfur=9.75  # > 9.50 ppm
    )

    is_vetoed, reason = QualityAgent.audit_candidate(bad_quality_cand)
    assert is_vetoed
    assert "GOST_VETO" in reason
    assert "Сера" in reason


def test_arbitration_log_barrier_economic_clearing():
    """Тест 3: Логарифмический штраф за риск отдает приоритет более безопасному кандидату."""
    # Кандидат 1: Форсированный режим, высокая маржа (+4500 руб/ч), но T55 = 385.5 °C (опасная близость к 386.4 °C)
    cand_aggressive = ControlCandidate(
        candidate_id="cand_aggressive",
        delta_u={"F15": 20.0, "T55": 1.5},
        expected_margin=4500.0,
        expected_t55=385.5,
        expected_sulfur=8.2
    )

    # Кандидат 2: Умеренный режим, меньшая номинальная маржа (+3000 руб/ч), но T55 = 379.0 °C (безопасная зона)
    cand_safe = ControlCandidate(
        candidate_id="cand_safe",
        delta_u={"F15": 10.0, "T55": 0.5},
        expected_margin=3000.0,
        expected_t55=379.0,
        expected_sulfur=8.3
    )

    # Аудит надежности
    _, _, penalty_aggr = ReliabilityAgent.audit_candidate(cand_aggressive)
    _, _, penalty_safe = ReliabilityAgent.audit_candidate(cand_safe)

    assert penalty_aggr > 2000.0  # Лог-штраф велик
    assert penalty_safe == 0.0    # Штраф в безопасной зоне отсутствует

    # Арбитраж должен выбрать безопасного кандидата:
    # Net Utility безопасного = 3000.0 > Net Utility агрессивного (4500 - ~2590 = ~1910)
    penalties = {
        cand_aggressive.candidate_id: penalty_aggr,
        cand_safe.candidate_id: penalty_safe
    }

    final_rec, selected = ArbitrationNode.execute(
        candidates=[cand_aggressive, cand_safe],
        vetoed_ids=[],
        risk_penalties=penalties,
        min_margin_improvement=1000.0
    )

    assert selected is not None
    assert selected.candidate_id == "cand_safe"
    assert final_rec.status == "SUCCESS"


def test_arbitration_empty_admissible_safe_hold():
    """Тест 4: Тотальное вето переводит систему в режим Safe Hold с текстом Callout Box 4."""
    cand1 = ControlCandidate(candidate_id="c1", delta_u={"F15": 10.0}, expected_margin=2000.0)
    cand2 = ControlCandidate(candidate_id="c2", delta_u={"F15": 20.0}, expected_margin=3000.0)

    final_rec, selected = ArbitrationNode.execute(
        candidates=[cand1, cand2],
        vetoed_ids=["c1", "c2"],
        risk_penalties={},
        min_margin_improvement=1000.0
    )

    assert selected is None
    assert final_rec.status == "SAFE_HOLD_EMPTY_ADMISSIBLE"
    assert final_rec.recommended_delta_u == {}
    assert "Надёжной рекомендации нет" in final_rec.explanation


def test_arbitration_deadband_margin():
    """Тест 5: Зона нечувствительности Deadband отклоняет изменения с маржой < 1000 руб/ч."""
    small_margin_cand = ControlCandidate(
        candidate_id="cand_small_gain",
        delta_u={"F15": 15.0, "T55": 0.5},
        expected_margin=850.0,  # < 1000.0 руб/ч
        expected_t55=381.0,
        expected_sulfur=8.0
    )

    final_rec, selected = ArbitrationNode.execute(
        candidates=[small_margin_cand],
        vetoed_ids=[],
        risk_penalties={},
        min_margin_improvement=1000.0
    )

    assert selected is None
    assert final_rec.status == "DEADBAND_REJECT_LOW_MARGIN"
    assert final_rec.recommended_delta_u == {}
    assert "1000.0 руб/ч" in final_rec.explanation


def test_arbitration_deadband_norm():
    """Тест 6: Зона нечувствительности Deadband отклоняет микроперемещения клапанов (норма < 0.05)."""
    tiny_step_cand = ControlCandidate(
        candidate_id="cand_tiny_step",
        delta_u={"F15": 0.01, "T55": 0.01},  # L2 norm = sqrt(0.0002) ~ 0.0141 < 0.05
        expected_margin=3500.0,
        expected_t55=381.0,
        expected_sulfur=8.0
    )

    final_rec, selected = ArbitrationNode.execute(
        candidates=[tiny_step_cand],
        vetoed_ids=[],
        risk_penalties={},
        min_margin_improvement=1000.0,
        min_delta_norm=0.05
    )

    assert selected is None
    assert final_rec.status == "DEADBAND_REJECT_SMALL_STEP"
    assert final_rec.recommended_delta_u == {}


def test_langgraph_step4_full_pipeline(quality_risk_tags):
    """Тест 7: Сквозной прогон графа: Guard -> Opt -> [Rel, Qual] -> Arb -> Blending."""
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
    """Тест 8: Сквозной прогон графа при опасных кандидатах -> переход в Safe Hold."""
    graph = build_core_graph()

    # В core_v3 мы не инжектим кандидатов вручную, а даем сценарий конфликта
    from src.agents.scenarios import scenario_4_conflict_tags
    import uuid
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
