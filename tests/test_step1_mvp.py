"""Автоматические тесты для Шага 1 MVP: LangGraph, State, Data Guard и Anti-Windup."""

import math
import pytest

from src.agents.state import (
    RawTelemetry,
    DataQuality,
    ControlCandidate,
    merge_risk_penalties,
    merge_audit_dict,
)
from src.agents.data_guard import node_data_quality_guard
from src.agents.safe_hold import node_safe_hold, REFUSAL_VERBATIM_TEXT
from src.agents.anti_windup import apply_anti_windup, apply_anti_windup_dict
from src.agents.graph import build_mvp_graph, route_after_guard


def test_data_guard_clamping():
    """Тест 1: Обнаружение аппаратного клампинга (307.0 и 313.0) на датчиках P52 и D10."""
    # Залипание P52 на 307.0
    bad_telemetry_1 = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=307.0,
        D10=840.0,
        F15=450.0,
        T55=380.0
    )
    result_1 = node_data_quality_guard({"raw_telemetry": bad_telemetry_1})
    quality_1: DataQuality = result_1["data_quality"]

    assert not quality_1.is_valid
    assert quality_1.status_code == "SAFE_HOLD_REFUSAL"
    assert "P52" in quality_1.clamped_tags
    assert "P52" in quality_1.refusal_reason

    # Залипание D10 на 313.0
    bad_telemetry_2 = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=0.05,
        D10=313.0,
        F15=450.0,
        T55=380.0
    )
    result_2 = node_data_quality_guard({"raw_telemetry": bad_telemetry_2})
    quality_2: DataQuality = result_2["data_quality"]

    assert not quality_2.is_valid
    assert "D10" in quality_2.clamped_tags


def test_data_guard_lims_aging():
    """Тест 2: Проверка регламентного возраста анализов LIMS (> 24 часов)."""
    # Устаревший анализ (25.5 часов)
    telemetry_stale = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=0.05,
        D10=840.0,
        F15=450.0,
        T55=380.0,
        lims_age_hours=25.5
    )
    res_stale = node_data_quality_guard({"raw_telemetry": telemetry_stale})
    assert not res_stale["data_quality"].is_valid
    assert "24.0" in res_stale["data_quality"].refusal_reason

    # Свежий анализ (4.0 часа)
    telemetry_fresh = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=0.05,
        D10=840.0,
        F15=450.0,
        T55=380.0,
        lims_age_hours=4.0
    )
    res_fresh = node_data_quality_guard({"raw_telemetry": telemetry_fresh})
    assert res_fresh["data_quality"].is_valid
    assert res_fresh["data_quality"].status_code == "NORMAL"


def test_steam_zero_drift_correction():
    """Тест 3: Программная отсечка отрицательного дрейфа нуля расходомеров пара F5 и F26."""
    telemetry_drift = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=0.05,
        D10=840.0,
        F5=-15.4,
        F26=-169.0,
        lims_age_hours=2.0
    )
    res = node_data_quality_guard({"raw_telemetry": telemetry_drift})
    cleaned_telemetry = res["raw_telemetry"]

    assert cleaned_telemetry.F5 == 0.0
    assert cleaned_telemetry.F26 == 0.0
    assert res["data_quality"].is_valid


def test_data_guard_nan_inf():
    """Тест 4: Отсечка нечисловых значений (NaN, Inf)."""
    telemetry_nan = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=float("nan"),
        D10=840.0,
        lims_age_hours=1.0
    )
    res = node_data_quality_guard({"raw_telemetry": telemetry_nan})
    assert not res["data_quality"].is_valid
    assert "P52" in res["data_quality"].invalid_range_tags


def test_anti_windup_rate_and_saturation():
    """Тест 5: Корректность Velocity Form и Back-Calculation Anti-Windup."""
    # 5.1. Только скоростное ограничение (Rate Clipping)
    # Запрос +20.0, при max_rate = 5.0 -> должно ограничиться 5.0, windup = 0
    eff_delta, windup_err = apply_anti_windup(
        current_u=50.0,
        delta_u_calc=20.0,
        max_rate=5.0,
        min_u=0.0,
        max_u=100.0
    )
    assert eff_delta == pytest.approx(5.0)
    assert windup_err == pytest.approx(0.0)

    # 5.2. Насыщение привода (Actuator Saturation)
    # Текущее 95.0, max_rate = 10.0, запрос +10.0 -> кандидат 105.0, но max_u = 100.0
    # Значит eff_delta = 5.0, а windup_err = 105.0 - 100.0 = 5.0
    eff_delta, windup_err = apply_anti_windup(
        current_u=95.0,
        delta_u_calc=10.0,
        max_rate=10.0,
        min_u=0.0,
        max_u=100.0
    )
    assert eff_delta == pytest.approx(5.0)
    assert windup_err == pytest.approx(5.0)

    # 5.3. Векторный вариант apply_anti_windup_dict
    current_u = {"F15": 400.0, "T55": 385.0}
    delta_u = {"F15": 100.0, "T55": 5.0}
    limits = {
        "F15": {"max_rate": 50.0, "min_u": 0.0, "max_u": 1000.0},
        "T55": {"max_rate": 1.0, "min_u": 350.0, "max_u": 386.4}  # Запас ПАЗ 5%
    }
    eff_deltas, errors = apply_anti_windup_dict(current_u, delta_u, limits)
    assert eff_deltas["F15"] == pytest.approx(50.0)
    assert errors["F15"] == pytest.approx(0.0)
    assert eff_deltas["T55"] == pytest.approx(1.0)
    assert errors["T55"] == pytest.approx(0.0)


def test_langgraph_safe_hold_route():
    """Тест 6: Сквозной прогон графа со сбойной телеметрией -> переход в Safe Hold с текстом Callout Box 4."""
    graph = build_mvp_graph()
    
    # Сбойная телеметрия (клампинг P52)
    corrupted_telemetry = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=307.0,
        D10=840.0,
        F15=400.0,
        T55=380.0,
        lims_age_hours=1.0
    )

    result = graph.invoke({"raw_telemetry": corrupted_telemetry})
    
    final_rec = result.get("final_recommendation")
    assert final_rec is not None
    assert final_rec.status == "SAFE_HOLD"
    # Обязательная проверка дословного текста Callout Box 4 ТЗ
    assert final_rec.explanation == REFUSAL_VERBATIM_TEXT
    # Уставки не должны изменяться
    assert final_rec.recommended_delta_u == {}


def test_langgraph_normal_route():
    """Тест 7: Сквозной прогон графа со штатной телеметрией -> переход в ветку Optimization."""
    graph = build_mvp_graph()

    valid_telemetry = RawTelemetry(
        timestamp="2026-09-15T12:00:00",
        P52=0.045,
        D10=840.0,
        F15=400.0,
        T55=380.0,
        F5=25.0,
        F26=80.0,
        lims_age_hours=2.0
    )

    result = graph.invoke({"raw_telemetry": valid_telemetry})

    final_rec = result.get("final_recommendation")
    assert final_rec is not None
    assert final_rec.status in ("OPTIMIZATION_NORMAL", "SUCCESS")
    assert len(result.get("candidates", [])) > 0
    assert result["candidates"][0].candidate_id == "cand_baseline_01"


def test_state_reducers():
    """Тест 8: Проверка редьюсеров для исключения InvalidUpdateError при параллельном выполнении."""
    # 8.1. Редьюсер барьерных штрафов
    penalties_a = {"cand_1": 1200.0}
    penalties_b = {"cand_2": 3500.0}
    merged_penalties = merge_risk_penalties(penalties_a, penalties_b)
    assert merged_penalties == {"cand_1": 1200.0, "cand_2": 3500.0}

    # 8.2. Редьюсер отчетов аудиторов
    audit_a = {"veto_reasons": ["P52 limit exceeded"], "agent_reliability": "PASS"}
    audit_b = {"veto_reasons": ["Sulfur limit exceeded"], "agent_quality": "FAIL"}
    merged_audit = merge_audit_dict(audit_a, audit_b)
    assert merged_audit["veto_reasons"] == ["P52 limit exceeded", "Sulfur limit exceeded"]
    assert merged_audit["agent_reliability"] == "PASS"
    assert merged_audit["agent_quality"] == "FAIL"
