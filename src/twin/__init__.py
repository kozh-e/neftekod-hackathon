"""Пакет цифрового двойника технологического комплекса (Шаг 2 MVP)."""

from src.twin.vak import VakCalculator
from src.twin.fopdt import (
    DiscreteMIMOFOPDTTwin,
    DEFAULT_TAU,
    DEFAULT_DELAYS,
    DEFAULT_GAIN_MATRIX,
    STATE_VARIABLES,
    CONTROL_INPUTS,
)

__all__ = [
    "VakCalculator",
    "DiscreteMIMOFOPDTTwin",
    "DEFAULT_TAU",
    "DEFAULT_DELAYS",
    "DEFAULT_GAIN_MATRIX",
    "STATE_VARIABLES",
    "CONTROL_INPUTS",
]
