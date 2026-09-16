"""Узел Data Quality Guard: первичная валидация КИПиА и защита от недостоверных данных.

Реализует требования implementation_plan_v2.md (T6.1):
1. Программная компенсация дрейфа нуля расходомеров пара (F5, F26) и расходов (kind == "flow");
2. Нормализация тегов через canonical registry (normalize_tags);
3. Проверка аппаратного клампинга (307.0 / 313.0) и нечисловых значений (NaN, Inf);
   - Клампинг или NaN критичного тега (CRITICAL_TAGS) -> SAFE_HOLD_REFUSAL;
   - Клампинг некритичного тега (например, D10) -> фиксация в clamped_tags без блокировки;
4. Подстановка номинальных значений для отсутствующих критичных тегов с предупреждениями FILLED:*;
5. Контроль устаревания лабораторных анализов LIMS (> 24 ч) и доступности поточного анализатора серы HT_Q21;
6. Расчет индекса уверенности (Confidence Score): HIGH, MEDIUM, LOW.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Set

from src.agents.state import DataQuality, MasGraphState, RawTelemetry
from src.twin.tags import TAGS, fill_from_nominal, normalize_tags

# Множество констант аппаратного насыщения КИПиА
CLAMPING_VALUES: Set[float] = {307.0, 313.0}

# Критичные теги, отказ которых не позволяет безопасно управлять комплексом
CRITICAL_TAGS: Set[str] = {
    "AVT_P52",
    "AVT_T55",
    "HT_F9",
    "HT_T6",
    "HT_T11",
    "HT_P13",
    "HT_F2",
    "HT_P8",
}

# Теги, для которых применяется программная отсечка дрейфа нуля
STEAM_FLOW_TAGS = ("F5", "F26")


def node_data_quality_guard(state: MasGraphState) -> Dict[str, Any]:
    """Узел валидации входной телеметрии перед передачей в оптимизатор и цифровой двойник."""
    telemetry: Optional[RawTelemetry] = state.get("raw_telemetry")
    raw_dict: Dict[str, Any] = {}

    if telemetry is not None:
        raw_dict = telemetry.model_dump()
        # 1. Программная компенсация дрейфа нуля расходомеров пара
        for tag in STEAM_FLOW_TAGS:
            if tag in raw_dict:
                raw_val = raw_dict[tag]
                if isinstance(raw_val, (int, float)) and not math.isnan(raw_val) and not math.isinf(raw_val):
                    raw_dict[tag] = max(0.0, float(raw_val))
        telemetry = RawTelemetry.model_validate(raw_dict)

    if state.get("tags"):
        raw_dict.update(state["tags"])

    quality = DataQuality()
    norm_tags, norm_warnings = normalize_tags(raw_dict)

    # Отсечка отрицательных значений для всех потоков (flow)
    for tag_name, val in list(norm_tags.items()):
        spec = TAGS.get(tag_name)
        if spec and spec.kind == "flow" and isinstance(val, (int, float)) and not math.isnan(val):
            norm_tags[tag_name] = max(0.0, float(val))

    # 2. Проверка на аппаратный клампинг и нечисловые значения
    clamped_critical: List[str] = []
    invalid_critical: List[str] = []

    # Анализируем сырые и нормализованные теги
    for tag_name, val in raw_dict.items():
        if tag_name in ("timestamp", "lims_age_hours", "session_id"):
            continue

        if isinstance(val, (int, float)):
            is_bad_num = math.isnan(val) or math.isinf(val)
            is_clamped = float(val) in CLAMPING_VALUES

            if is_bad_num:
                quality.invalid_range_tags.append(tag_name)
            elif is_clamped:
                quality.clamped_tags.append(tag_name)

            # Проверка критичности тега
            # (проверяем как каноническое имя, так и с префиксом AVT_/HT_)
            canonical_candidates = {tag_name, f"AVT_{tag_name}", f"HT_{tag_name}"}
            if canonical_candidates & CRITICAL_TAGS:
                if is_bad_num:
                    invalid_critical.append(tag_name)
                elif is_clamped:
                    clamped_critical.append(tag_name)

    # 3. Подстановка номинальных значений для недостающих критичных тегов
    norm_tags, fill_warnings = fill_from_nominal(norm_tags, CRITICAL_TAGS)
    n_filled_critical = len([w for w in fill_warnings if w.startswith("FILLED:")])

    # 4. Контроль актуальности LIMS и поточности анализатора Q21
    lims_age = float(raw_dict.get("lims_age_hours", getattr(telemetry, "lims_age_hours", 0.0) if telemetry else 0.0))
    quality.lims_age_hours = lims_age

    q21_val = norm_tags.get("HT_Q21")
    q21_unavailable = (
        q21_val is None
        or not isinstance(q21_val, (int, float))
        or math.isnan(q21_val)
        or float(q21_val) in CLAMPING_VALUES
    )

    # 5. Принятие решения о блокировке и переходе в режим безопасного удержания
    refusal_reasons = []
    if clamped_critical:
        refusal_reasons.append(f"Аппаратный клампинг критичных датчиков: {', '.join(clamped_critical)}")
    if invalid_critical:
        refusal_reasons.append(f"Нечисловые значения критичных датчиков: {', '.join(invalid_critical)}")
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

    # 6. Расчет индекса уверенности системы (Confidence Score)
    conf_score = (
        1.0
        - 0.15 * n_filled_critical
        - (0.20 if q21_unavailable else 0.0)
        - min(0.30, lims_age / 80.0)
    )
    conf_score = max(0.0, min(1.0, conf_score))
    if conf_score >= 0.75:
        level = "HIGH"
    elif conf_score >= 0.50:
        level = "MEDIUM"
    else:
        level = "LOW"

    confidence = {
        "score": round(conf_score, 3),
        "level": level,
        "n_filled_critical": n_filled_critical,
        "q21_unavailable": q21_unavailable,
        "lims_age_hours": lims_age,
    }

    res: Dict[str, Any] = {
        "data_quality": quality,
        "tags": norm_tags,
        "twin_warnings": norm_warnings + fill_warnings,
        "confidence": confidence,
    }

    if telemetry is None:
        try:
            import datetime
            ts_val = str(raw_dict.get("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()))
            telemetry = RawTelemetry(
                timestamp=ts_val,
                P52=float(norm_tags.get("AVT_P52", 0.045)),
                D10=float(norm_tags.get("AVT_D10", 840.0)),
                F15=float(norm_tags.get("HT_F15", 3400.0)),
                T55=float(norm_tags.get("AVT_T55", 381.7)),
                F5=float(norm_tags.get("AVT_F5", 25.0)),
                F26=float(norm_tags.get("HT_F26", 219.6)),
                lims_age_hours=lims_age,
            )
        except Exception:
            pass

    if telemetry is not None:
        res["raw_telemetry"] = telemetry

    return res
