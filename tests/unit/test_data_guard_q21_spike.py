"""Unit tests for HT_Q21 outlier/spike detection (Q11, agents/ASSUMPTIONS.md)."""

from __future__ import annotations

from src.agents.contracts import SignalQuality
from src.agents.data_guard import assess_data, node_data_quality_guard
from src.twin.tags import NOMINAL_OPERATING_POINT


def test_assess_data_q21_known_clamp_value_flagged_bad():
    """HT_Q21 = 307.0 остаётся BAD/CLAMPED (аппаратный клампинг)."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["HT_Q21"] = 307.0

    assessment = assess_data(tags)
    m_q21 = assessment.measurements["HT_Q21"]

    assert m_q21.quality == SignalQuality.BAD
    assert "CLAMPED" in m_q21.flags


def test_assess_data_q21_over_threshold_flagged_outlier_spike():
    """HT_Q21 > 50 ppm (но не клампинг) помечается SUSPECT/OUTLIER_SPIKE."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["HT_Q21"] = 75.5

    assessment = assess_data(tags)
    m_q21 = assessment.measurements["HT_Q21"]

    assert m_q21.quality == SignalQuality.SUSPECT
    assert "OUTLIER_SPIKE" in m_q21.flags


def test_node_data_quality_guard_q21_spike_does_not_block_plant():
    """Выброс HT_Q21 не переводит установку в REFUSAL_DATA, но снижает доверие к сере."""
    state = {
        "raw_telemetry": None,
        "tags": {
            "HT_Q21": 307.0,
            "AVT_P52": 0.045,
            "AVT_T55": 381.7,
            "HT_F9": 220.0,
            "HT_T6": 363.0,
            "HT_T11": 364.0,
            "HT_P13": 3.92,
            "HT_F2": 90000.0,
            "HT_P8": 0.18,
        },
    }

    res = node_data_quality_guard(state)
    dq = res["data_quality"]

    assert dq.is_valid is True
    assert dq.status_code == "NORMAL"

    warnings = res["twin_warnings"]
    assert any("OUTLIER_SPIKE:HT_Q21=307.00" in w for w in warnings)
    assert res["confidence"]["q21_unavailable"] is True


def test_node_data_quality_guard_q21_extreme_spike_flagged():
    """HT_Q21 = 75.5 ppm (> 50 ppm) распознаётся как выброс КИП и снижает доверие."""
    state = {
        "raw_telemetry": None,
        "tags": {
            "HT_Q21": 75.5,
            "AVT_P52": 0.045,
            "AVT_T55": 381.7,
            "HT_F9": 220.0,
            "HT_T6": 363.0,
            "HT_T11": 364.0,
            "HT_P13": 3.92,
            "HT_F2": 90000.0,
            "HT_P8": 0.18,
        },
    }

    res = node_data_quality_guard(state)
    warnings = res["twin_warnings"]

    assert any("OUTLIER_SPIKE:HT_Q21=75.50" in w for w in warnings)
    assert res["confidence"]["q21_unavailable"] is True


def test_node_data_quality_guard_q21_plausible_value_not_flagged():
    """Правдоподобное значение HT_Q21 (<= 50 ppm) не считается выбросом."""
    state = {
        "raw_telemetry": None,
        "tags": {
            "HT_Q21": 8.43,
            "AVT_P52": 0.045,
            "AVT_T55": 381.7,
            "HT_F9": 220.0,
            "HT_T6": 363.0,
            "HT_T11": 364.0,
            "HT_P13": 3.92,
            "HT_F2": 90000.0,
            "HT_P8": 0.18,
        },
    }

    res = node_data_quality_guard(state)
    warnings = res["twin_warnings"]

    assert not any("OUTLIER_SPIKE" in w for w in warnings)
    assert res["confidence"]["q21_unavailable"] is False
