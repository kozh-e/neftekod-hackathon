"""Коридоры шагов и технологические границы MV (R2).

Считывает ограничения скорости хода и T0 диапазоны из реестра, проверяет приращения.
Никаких захардкоженных чисел: все параметры читаются из src/agents/registry.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.registry import T0_SPECS
from src.console.contracts import Corridor, SP


def _get_spec(key: str):
    """Ищет спецификацию в T0_SPECS по ключу."""
    for spec in T0_SPECS:
        if spec.key == key:
            return spec
    return None


def corridor_for(sp: str) -> Corridor:
    """
    Возвращает коридор допустимого шага и диапазона для указанного SP.
    Значения считываются строго из спецификаций реестра T0_SPECS.
    """
    rate_spec = _get_spec(f"RATE.{sp}.MAX")
    min_spec = _get_spec(f"MV.{sp}.MIN")
    max_spec = _get_spec(f"MV.{sp}.MAX")

    max_step = rate_spec.limit if rate_spec is not None else None
    lo = min_spec.limit if min_spec is not None else None
    hi = max_spec.limit if max_spec is not None else None

    if rate_spec is not None:
        source = f"registry.py:{rate_spec.key}"
    elif min_spec is not None:
        source = f"registry.py:{min_spec.key}"
    else:
        source = None

    return Corridor(
        max_step_per_tick=max_step,
        lo=lo,
        hi=hi,
        source=source,
    )


def check_step(u_current: Dict[str, float], u_target: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    Проверяет приращение уставок на соответствие коридору шага и границам T0.
    Возвращает список выявленных нарушений.
    """
    violations: List[Dict[str, Any]] = []

    for sp_name, target_val in u_target.items():
        curr_val = u_current.get(sp_name, target_val)
        delta = abs(target_val - curr_val)
        corr = corridor_for(sp_name)

        # 1. Проверка скорости изменения (максимального шага)
        if corr.max_step_per_tick is not None:
            if delta > corr.max_step_per_tick + 1e-6:
                violations.append({
                    "sp": sp_name,
                    "requested_step": round(delta, 3),
                    "max_step": corr.max_step_per_tick,
                    "text": (
                        f"Шаг {sp_name} {delta:.2f} превышает допустимый "
                        f"{corr.max_step_per_tick} /такт ({corr.source})"
                    ),
                })
        else:
            # Если скорость не задана в реестре, любое ненулевое изменение запрещено
            if delta > 1e-3:
                violations.append({
                    "sp": sp_name,
                    "requested_step": round(delta, 3),
                    "max_step": None,
                    "text": f"Шаг {sp_name} не задан в паспорте, изменение только вручную в DCS",
                })

        # 2. Проверка выхода за технологические границы T0 (lo/hi)
        if corr.lo is not None and target_val < corr.lo - 1e-6:
            violations.append({
                "sp": sp_name,
                "requested_step": round(delta, 3),
                "max_step": corr.max_step_per_tick,
                "text": f"Уставка {sp_name} {target_val:.2f} ниже технологического минимума T0 ({corr.lo:.2f})",
            })
        if corr.hi is not None and target_val > corr.hi + 1e-6:
            violations.append({
                "sp": sp_name,
                "requested_step": round(delta, 3),
                "max_step": corr.max_step_per_tick,
                "text": f"Уставка {sp_name} {target_val:.2f} выше технологического максимума T0 ({corr.hi:.2f})",
            })

    return violations
