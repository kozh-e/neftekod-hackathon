"""Технологические пределы, нормативы и допущения оборудования и качества.

Содержит спецификации пределов согласно ГОСТ 32511-2013, ТЗ и историческим данным
с явным указанием источника (NORM / DATA / ASSUMPTION) для прозрачности XAI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class Limit:
    key: str
    lo: Optional[float]
    hi: Optional[float]
    unit: str
    source: Literal["NORM", "ASSUMPTION"]
    note: str


# =============================================================================
# Товарное ДТ (PDF + ГОСТ 32511-2013 Евро-5) - NORM
# =============================================================================
SULFUR_PRODUCT_MAX = Limit("S", None, 10.0, "мг/кг", "NORM", "PDF, ТЗ, ГОСТ 32511 (Евро-5)")
T95_PRODUCT_MAX = Limit("T95", None, 360.0, "°C", "NORM", "PDF, ГОСТ 32511")
CETANE_PRODUCT_MIN = Limit("CN", 51.0, None, "", "NORM", "PDF, ГОСТ 32511")
DENSITY_PRODUCT = Limit("D15", 820.0, 845.0, "кг/м3", "NORM", "ГОСТ 32511")
FLASH_PRODUCT_MIN = Limit("Flash", 55.0, None, "°C", "NORM", "ГОСТ 32511: «не ниже 55.0 °C»")
CFPP_BY_GRADE = {"C": -5.0, "E": -15.0, "F": -20.0}  # NORM

# Буферы в LP блендинга (свойства компонентов из анализа резервуаров) - ASSUMPTION
BLEND_SULFUR_MAX: float = 9.5
BLEND_DENSITY: tuple[float, float] = (821.25, 843.75)
BLEND_FLASH_MIN: float = 56.0
# T95 товарного топлива: норматив 360 °C; статистический запас 2σ добавляется к прогнозу T95 ГО ДТ
BLEND_T95_MAX: float = 360.0
BLEND_CETANE_MIN: float = 51.5

# Статистика качества (ADR-12). Значения DATA: scripts/estimate_quality_uncertainty.py (методика ТЗ:
# ЛИМС — истина, синхронизация по времени, bias update по доступным пробам, обучение <= 2025-06-30)
QUALITY_Z: float = 2.0  # NORM: промпт Агента Качества «прогноз + 2*sigma_q» (tz:598), ±2σ (tz:954)
SIGMA_S0_PPM: float = 0.83  # DATA: HT_Q21 + bias vs ЛИМС, робастная σ (тест 0.83)
SIGMA_FLASH_C: float = 4.78  # DATA: HT_T18 + bias vs ЛИМС, обучение (тест 3.07 — нестабильна)
SIGMA_T95_C: float = 3.27  # DATA: ВАК 24-2000:GODT:T95 + bias, σ(24 ч)=5.67 приведена к age=0 законом √(1+age/12)
SULFUR_LEGACY_VETO: float = 9.5  # скалярный путь старых кандидатов

# Граница переочистки серы (giveaway): переочистка, если Ŝ + GIVEAWAY_Z·σ_S(age) < 10.
# ASSUMPTION: минимакс сожаления по цене брака L ∈ [OPEX секции/т, спред ГО ДТ − прямогон], α_min ≈ 0.05%
GIVEAWAY_Z: float = 3.31

# =============================================================================
# Оборудование (ASSUMPTION, с указанием доли нарушений в истории)
# =============================================================================
HT_DP_MAX_KPA = Limit("HT_DP_KPA", None, 454.5, "кПа", "ASSUMPTION", "=4.635 кгс/см2; история q99=236 → не активен")
HT_T_OUT_MAX = Limit("HT_T11", None, 390.0, "°C", "ASSUMPTION", "история q95=379")
HT_GOR_MIN = Limit("HT_GOR", 300.0, None, "нм3/м3", "ASSUMPTION", "история: <300 в 1.5%")
FEED_TO_AVT = Limit("HT_FEED_TO_AVT", 0.81, 1.26, "", "ASSUMPTION", "q05–q95 F9/(F30+F32): запас")
T55_MAX = Limit("AVT_T55", None, 386.4, "°C", "ASSUMPTION", "история: >386.4 в 0.8%")
# Границы уставки печи для Агента Оптимизации (решение: широкие границы до ПАЗ, буфер держит вето надежности)
T55_SP_BOUNDS: tuple[float, float] = (375.0, 395.0)  # NORM: нижняя граница рабочего диапазона П-3 (tz:825), ПАЗ 395 °C (tz:611)
P52_MAX = Limit("AVT_P52", None, 0.077, "кгс/см2", "ASSUMPTION", "тег в МПа, в архиве ≈0 — датчик под вопросом")
F31_MIN = Limit("AVT_F31", 362.5, None, "т/ч", "ASSUMPTION", "было «м3/ч»; тег в т/ч")

# Legacy предел перепада Р-202 (помечен deprecated, только для старых кандидатов с expected_w10)
LEGACY_W10_MAX: float = 4.635
