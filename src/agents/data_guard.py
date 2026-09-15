"""Узел Data Quality Guard: первичная валидация КИПиА и защита от мусорных данных.

Реализует требования Сценария 3 ТЗ и System_Design.md (Шаг 1.1):
1. Аппаратный клампинг датчиков (значения 307.0 / 313.0 при обрыве токовой петли / насыщении АЦП).
2. Фильтрация нечисловых значений (NaN, Inf) и физически недопустимых величин.
3. Программная отсечка отрицательного дрейфа нуля расходомеров пара (F5, F26): F_clean = max(0.0, F_raw).
4. Контроль устаревания лабораторных анализов LIMS (> 24 часов).
"""

from __future__ import annotations

import math
from typing import Dict, Any, Set
from src.agents.state import MasGraphState, DataQuality, RawTelemetry

# Множество констант аппаратного насыщения КИПиА (Callout Box 2 ТЗ)
CLAMPING_VALUES: Set[float] = {307.0, 313.0}

# Теги, для которых применяется программная отсечка дрейфа нуля
STEAM_FLOW_TAGS = ("F5", "F26")


def node_data_quality_guard(state: MasGraphState) -> Dict[str, Any]:
    """Узел валидации входной телеметрии перед передачей в оптимизатор и симулятор."""
    telemetry: RawTelemetry = state["raw_telemetry"]
    quality = DataQuality()

    # 1. Программная компенсация дрейфа нуля расходомеров пара
    cleaned_telemetry_dict = telemetry.model_dump()
    for tag in STEAM_FLOW_TAGS:
        if hasattr(telemetry, tag):
            raw_val = getattr(telemetry, tag)
            if isinstance(raw_val, (int, float)) and not math.isnan(raw_val) and not math.isinf(raw_val):
                cleaned_val = max(0.0, float(raw_val))
                cleaned_telemetry_dict[tag] = cleaned_val

    telemetry = RawTelemetry.model_validate(cleaned_telemetry_dict)

    # 2. Проверка на аппаратный клампинг (307.0 и 313.0) и нечисловые значения (NaN / Inf)
    telemetry_data = telemetry.model_dump()
    for tag_name, val in telemetry_data.items():
        if tag_name in ("timestamp", "lims_age_hours"):
            continue

        if isinstance(val, (int, float)):
            if math.isnan(val) or math.isinf(val):
                quality.invalid_range_tags.append(tag_name)
            elif float(val) in CLAMPING_VALUES:
                quality.clamped_tags.append(tag_name)

    # 3. Контроль актуальности лабораторных паспортов качества LIMS
    lims_age = getattr(telemetry, "lims_age_hours", 0.0)
    quality.lims_age_hours = lims_age

    # 4. Принятие решения о блокировке и переходе в режим безопасного удержания
    refusal_reasons = []
    if quality.clamped_tags:
        refusal_reasons.append(f"Аппаратный клампинг датчиков: {', '.join(quality.clamped_tags)}")
    if quality.invalid_range_tags:
        refusal_reasons.append(f"Нечисловые или недопустимые значения датчиков: {', '.join(quality.invalid_range_tags)}")
    if lims_age > 24.0:
        refusal_reasons.append(f"Устаревание данных LIMS ({lims_age:.1f} ч > 24.0 ч)")

    if refusal_reasons:
        quality.is_valid = False
        quality.status_code = "SAFE_HOLD_REFUSAL"
        quality.refusal_reason = "; ".join(refusal_reasons)
    else:
        quality.is_valid = True
        quality.status_code = "NORMAL"
        quality.refusal_reason = None

    return {
        "raw_telemetry": telemetry,
        "data_quality": quality
    }
