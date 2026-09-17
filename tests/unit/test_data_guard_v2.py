"""Unit tests for Data Quality Guard v2 and telemetry degradation ladder (P1.4)."""

from __future__ import annotations

import pytest
import math

from src.agents.contracts import AutomationLevel, SignalQuality
from src.agents.data_guard import assess_data
from src.twin.tags import NOMINAL_OPERATING_POINT


def test_data_guard_nominal_full():
    """При всех достоверных входных данных уровень автоматизации должен быть FULL."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["AVT_F31"] = 540.7  # Номинальный расход сырья печи П-3
    assessment = assess_data(tags)

    assert assessment.automation_level == AutomationLevel.FULL
    assert len(assessment.blocked_mvs) == 0
    assert len(assessment.unknown_specs) == 0


def test_data_guard_clamping_detection():
    """Обнаружение аппаратного клампинга (307.0 / 313.0) с присвоением BAD и флага CLAMPED."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["HT_T6"] = 307.0  # Клампинг датчика температуры
    tags["HT_T11"] = 313.0 # Клампинг датчика температуры

    assessment = assess_data(tags)
    m_t6 = assessment.measurements["HT_T6"]
    m_t11 = assessment.measurements["HT_T11"]

    assert m_t6.quality == SignalQuality.BAD
    assert "CLAMPED" in m_t6.flags
    assert m_t11.quality == SignalQuality.BAD
    assert "CLAMPED" in m_t11.flags


def test_data_guard_nan_detection():
    """Обнаружение нечисловых значений NaN/Inf с переводом в BAD и флагом NAN."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["HT_DP_KPA"] = float("nan")

    assessment = assess_data(tags)
    m_dp = assessment.measurements["HT_DP_KPA"]
    assert m_dp.quality == SignalQuality.BAD
    assert "NAN" in m_dp.flags


def test_data_guard_blocked_mvs_on_missing_critical():
    """При отсутствии критических тегов формируется список blocked_mvs и unknown_specs."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags.pop("AVT_T55", None)
    tags.pop("T55", None)
    tags.pop("HT_P8", None)

    assessment = assess_data(tags)
    assert "AVT_T55_SP" in assessment.blocked_mvs
    assert "HT_FEED_SP" in assessment.blocked_mvs
    assert "HT_GOR_SP" in assessment.blocked_mvs
    assert "FURNACE.COT_MAX" in assessment.unknown_specs
    assert "RX.DP_MAX" in assessment.unknown_specs


def test_data_guard_aging_ladder():
    """Проверка переходов по лестнице деградации автоматизации при старении ЛИМС."""
    base = dict(NOMINAL_OPERATING_POINT)
    base["AVT_F31"] = 540.7

    # 4 часа -> FULL
    tags_4 = dict(base, lims_age_hours=4.0)
    assert assess_data(tags_4).automation_level == AutomationLevel.FULL

    # 10 часов -> CAUTIOUS
    tags_10 = dict(base, lims_age_hours=10.0)
    assert assess_data(tags_10).automation_level == AutomationLevel.CAUTIOUS

    # 18 часов -> CORRECTIVE_ONLY
    tags_18 = dict(base, lims_age_hours=18.0)
    assert assess_data(tags_18).automation_level == AutomationLevel.CORRECTIVE_ONLY

    # 26 часов без ПАК -> REFUSAL_DATA
    tags_26_no_pak = dict(base, lims_age_hours=26.0)
    tags_26_no_pak.pop("HT_Q21", None)
    tags_26_no_pak.pop("Q21", None)
    assert assess_data(tags_26_no_pak).automation_level == AutomationLevel.REFUSAL_DATA
