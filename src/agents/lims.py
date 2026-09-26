"""Возраст лабораторных анализов LIMS из состояния графа.

Вето Агента Качества в графе использует не компенсатор смещения ВАК, а stat_offset()
(src/agents/constraints.py) с константами ADR-12 из src/agents/limits.py.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def lims_age_from_state(state: Mapping[str, Any]) -> float:
    """Возраст анализов ЛИМС (ч) из состояния графа: raw_telemetry -> tags -> confidence."""
    raw = state.get("raw_telemetry")
    if raw is not None and hasattr(raw, "lims_age_hours"):
        return float(raw.lims_age_hours)
    tags: Optional[Mapping[str, Any]] = state.get("tags")
    if tags and "lims_age_hours" in tags:
        return float(tags["lims_age_hours"])
    confidence = state.get("confidence") or {}
    return float(confidence.get("lims_age_hours", 0.0))
