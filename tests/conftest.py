"""Общие фикстуры для тестового набора цифрового двойника и мультиагентной системы."""

import os
import pytest
from typing import Dict, Any

# Отключаем запись журнала решений на диск во время прогона тестов
os.environ["NEFTEKOD_DECISION_LOG"] = "off"


@pytest.fixture
def nominal_tags() -> Dict[str, float]:
    """Номинальный технологический режим (DATA, медианы рабочих периодов §7.1)."""
    return {
        "HT_F9": 219.6,
        "AVT_F30": 128.3,
        "AVT_F32": 81.6,
        "HT_T6": 363.3,
        "HT_T11": 364.0,
        "HT_T5": 370.3,
        "AVT_F65": 924.5,
        "HT_P13": 3.922,
        "HT_P8": 0.177,
        "AVT_T55": 381.7,
        "AVT_T33": 338.3,
        "AVT_T71": 304.4,
        "HT_F2": 93309.0,
        "HT_GOR": 360.0,
        "HT_Q20": 8116.0,
        "HT_Q21": 8.43,
        "HT_F14": 6.05,
        "HT_F25": 13199.0,
        "HT_T18": 68.3,
        "HT_P24": 0.585,
        "HT_W7": 0.173,
        "HT_T23": 235.5,
        "PAK_D15": 835.2,
        "AVT_D10": 847.2,
        "AVT_P52": 0.045,
        "LIMS_HT_S": 8.6,
        "LIMS_HT_FLASH": 68.0,
        "LIMS_HT_D15": 836.1,
        "LIMS_HT_T95": 347.0,
        "LIMS_HT_FEED_S": 9470.0,
        "lims_age_hours": 2.0,
    }


@pytest.fixture
def quality_risk_tags(nominal_tags) -> Dict[str, float]:
    """Телеметрия в режиме риска ухудшения качества серы (Q21=9.8, LIMS_HT_S=10.2, AVT_F30=140)."""
    tags = dict(nominal_tags)
    tags["HT_Q21"] = 9.8
    tags["LIMS_HT_S"] = 10.2
    tags["AVT_F30"] = 140.0
    return tags


@pytest.fixture
def degraded_tags(nominal_tags) -> Dict[str, Any]:
    """Телеметрия с деградацией КИП/LIMS (Q20=307, Q21=NaN, lims_age=26 ч)."""
    tags = dict(nominal_tags)
    tags["HT_Q20"] = 307.0
    tags["HT_Q21"] = float("nan")
    tags["lims_age_hours"] = 26.0
    return tags
