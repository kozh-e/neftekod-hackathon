"""Unit tests for HT_Q21 outlier/spike detection (Q11, agents/ASSUMPTIONS.md)."""

from __future__ import annotations

from src.agents.contracts import SignalQuality
from src.agents.data_guard import assess_data
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
