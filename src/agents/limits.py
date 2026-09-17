"""Технологические пределы, нормативы и допущения оборудования и качества.

Сохраняет полную обратную совместимость для существующих тестов и модулей до G3,
реэкспортируя спецификации из единого реестра `src/agents/registry.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from src.agents.registry import (
    ALL_SPECS,
    REGISTRY_BY_KEY,
    get_constraint,
    get_constraints_by_tier,
    get_constraints_by_owner,
    get_specs_for_mv,
    validate_registry_provenance,
)


@dataclass(frozen=True)
class Limit:
    key: str
    lo: Optional[float]
    hi: Optional[float]
    unit: str
    source: Literal["NORM", "ASSUMPTION", "POLICY"]
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
BLEND_T95_MAX: float = 360.0
BLEND_CETANE_MIN: float = 51.5

# Статистика качества (ADR-12).
# Перемаркировано согласно implementation_plan_v3.md: z=2 -> POLICY (alpha_quality=0.0228)
QUALITY_Z: float = 2.0
SIGMA_S0_PPM: float = 0.83  # DATA: HT_Q21 + bias vs ЛИМС
SIGMA_FLASH_C: float = 4.78  # DATA: HT_T18 + bias vs ЛИМС
SIGMA_T95_C: float = 3.27  # DATA: ВАК 24-2000:GODT:T95 + bias
SULFUR_LEGACY_VETO: float = 9.5

GIVEAWAY_Z: float = 3.31

# =============================================================================
# Оборудование (ASSUMPTION / POLICY)
# =============================================================================
HT_DP_MAX_KPA = Limit("HT_DP_KPA", None, 454.5, "кПа", "ASSUMPTION", "=4.635 кгс/см2; история q99=236 → не активен")
HT_T_OUT_MAX = Limit("HT_T11", None, 390.0, "°C", "ASSUMPTION", "история q95=379")
HT_GOR_MIN = Limit("HT_GOR", 300.0, None, "нм3/м3", "ASSUMPTION", "история: <300 в 1.5%")
FEED_TO_AVT = Limit("HT_FEED_TO_AVT", 0.81, 1.26, "", "ASSUMPTION", "q05–q95 F9/(F30+F32): запас")
T55_MAX = Limit("AVT_T55", None, 386.4, "°C", "ASSUMPTION", "история: >386.4 в 0.8%")
# Границы уставки печи: 375/395 °C -> ASSUMPTION (перемаркировано из NORM в P1.1)
T55_SP_BOUNDS: tuple[float, float] = (375.0, 395.0)
P52_MAX = Limit("AVT_P52", None, 0.077, "кгс/см2", "ASSUMPTION", "тег в МПа, в архиве ≈0")
F31_MIN = Limit("AVT_F31", 362.5, None, "т/ч", "ASSUMPTION", "тег в т/ч")

# Legacy предел перепада Р-202
LEGACY_W10_MAX: float = 4.635
