"""Unit tests for constraint registry and provenance validation (P1.2)."""

from __future__ import annotations

import pytest

from src.agents.contracts import ConstraintTier
from src.agents.registry import REGISTRY, validate_registry_provenance


def test_registry_count_and_provenance():
    """Проверка наличия всех 34 ограничений и валидности их провенанса.

    RATE.HT_P_SP.MAX/RATE.HT_GOR_SP.MAX сознательно отсутствуют (нет ограничения скорости
    хода по паспорту оборудования для этих двух MV, см. registry.py) — это не пробел.
    """
    assert len(REGISTRY) == 34, f"Ожидалось 34 спецификации в REGISTRY, получено {len(REGISTRY)}"
    violations = validate_registry_provenance()
    assert len(violations) == 0, f"Обнаружены нарушения провенанса в реестре: {violations}"


def test_registry_tiers_represented():
    """Все уровни T0-T3 должны быть представлены в реестре."""
    tiers = {spec.tier for spec in REGISTRY.values()}
    assert ConstraintTier.T0_BOUNDS in tiers
    assert ConstraintTier.T1_EQUIPMENT in tiers
    assert ConstraintTier.T2_QUALITY in tiers
    assert ConstraintTier.T3_OPERATIONAL in tiers


def test_registry_critical_specs_exist():
    """Проверка ключевых ограничений технологического комплекса."""
    assert "FURNACE.COT_MAX" in REGISTRY
    assert "RX.DP_MAX" in REGISTRY
    assert "GODT.S_MAX" in REGISTRY
    assert "PRODUCT.FLASH_MIN" in REGISTRY

    cot_spec = REGISTRY["FURNACE.COT_MAX"]
    assert cot_spec.limit == 386.4
    assert cot_spec.tier == ConstraintTier.T1_EQUIPMENT
    assert cot_spec.provenance.kind.value == "ASSUMPTION"

    s_spec = REGISTRY["GODT.S_MAX"]
    assert s_spec.limit == 10.0
    assert s_spec.tier == ConstraintTier.T2_QUALITY
    assert s_spec.provenance.kind.value == "NORM"
