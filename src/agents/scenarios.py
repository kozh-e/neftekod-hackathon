"""Входные состояния 4 обязательных сценариев демонстрации ТЗ (§7.2) для реплея, тестов и пульта.

Значения берутся из текста ТЗ; где ТЗ числа не задает — из рабочих диапазонов архива с указанием источника.
"""

from __future__ import annotations

from typing import Dict

from src.agents.candidates import DEFAULT_MVS
from src.twin.tags import NOMINAL_OPERATING_POINT


def scenario_1_normal_tags() -> Dict[str, float]:
    """Сценарий 1 «Норма» (tz:988): сера 8.2 мг/кг, плотность 835 кг/м³."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags.update({"HT_Q21": 8.2, "LIMS_HT_S": 8.2, "PAK_D15": 835.0})
    return tags


def scenario_2_quality_risk_tags() -> Dict[str, float]:
    """Сценарий 2 «Риск качества» (tz:991): утяжеление сырья, прогноз серы 9.8–10.5 мг/кг."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags.update({"AVT_F30": 145.0, "HT_Q20": 9800.0, "HT_Q21": 9.8})
    return tags


def scenario_3_degraded_tags() -> Dict[str, float]:
    """Сценарий 3 «Деградация данных» (tz:994): клампинг D10 = 307, ЛИМС старше 24 ч, плотномер NaN."""
    tags = dict(NOMINAL_OPERATING_POINT)
    tags.update({"AVT_D10": 307.0, "PAK_D15": float("nan"), "lims_age_hours": 26.0})
    return tags


def scenario_4_conflict_tags() -> Dict[str, float]:
    """
    Сценарий 4 «Сквозной консенсус МАС» (tz:997): оптимизатор предлагает нагрев печи ради отбора дистиллята,
    надежность ветирует перегрев змеевика (T55 ≥ 387 °C), качество требует стабилизации фракционного состава.
    - AVT_T55 = 385.0 °C — верх рабочего диапазона печи П-3 (tz:825): шаг +2 °C дает 387 °C;
    - HT_F9 на верхней границе уставки сырья (q95 архива, candidates.py): загрузка гидроочистки исчерпана,
      и самым выгодным предложением оптимизатора становится нагрев печи;
    - сера 7.5 мг/кг — нижний квартиль ЛИМС (q25 = 7.4): конфликт только печной, без вето по сере.
    """
    feed_hi = next(mv.hi for mv in DEFAULT_MVS if mv.name == "HT_FEED_SP")
    tags = dict(NOMINAL_OPERATING_POINT)
    tags.update({"AVT_T55": 385.0, "HT_F9": feed_hi, "HT_Q21": 7.5, "LIMS_HT_S": 7.5})
    return tags
