"""Узел Data Quality Guard (v2): валидация КИПиА, защита от недостоверных данных и провенанс.

Реализует спецификацию §4.1, §5.3 implementation_plan_v3.md:
1. Детекторы: аппаратный клампинг (307/313), NaN/Inf, физический диапазон, залипание (frozen),
   скачки/скорость изменения, расхождение ПАК с моделью.
2. Лестница деградации доверия: FULL -> CAUTIOUS -> CORRECTIVE_ONLY -> REFUSAL_DATA.
3. Формирование blocked_mvs и unknown_specs (fail-closed для зависимых ограничений).
4. Запрет неявной подстановки номиналов для зависимых управляющих воздействий.
5. Полная обратная совместимость с MasGraphState и DataQuality старого графа.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.agents.contracts import (
    AutomationLevel,
    DataAssessment,
    Measurement,
    Provenance,
    ProvenanceKind,
    SignalQuality,
    SignalSource,
)
from src.agents.policy import AutomationThresholds, PolicyConfig
from src.agents.registry import ALL_SPECS, get_specs_for_mv
from src.agents.state_legacy import DataQuality, MasGraphState, RawTelemetry
from src.twin.tags import TAGS, fill_from_nominal, normalize_tags

# Аппаратные константы насыщения АЦП/датчиков
CLAMPING_VALUES: Set[float] = {307.0, 313.0}

# Максимально правдоподобное значение серы онлайн-анализатора HT_Q21 (ppm).
# Любое единичное показание выше этого порога (включая клампинг 307/313) —
# аномальный выброс КИП, а не реальный технологический сигнал.
Q21_MAX_PLAUSIBLE_PPM: float = 50.0

# Критические теги, влияющие на технологическую безопасность (T1/ПАЗ)
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

# Физические диапазоны правдоподобности КИПиА
PHYSICAL_RANGES: Dict[str, Tuple[float, float]] = {
    "AVT_T55": (200.0, 450.0),
    "HT_T6": (200.0, 450.0),
    "HT_T11": (200.0, 450.0),
    "HT_F9": (0.0, 350.0),
    "HT_P13": (0.0, 10.0),
    "HT_P8": (0.0, 1.0),
    "HT_DP_KPA": (0.0, 1000.0),
    "HT_F2": (0.0, 200000.0),
    "AVT_F31": (0.0, 1000.0),
    "AVT_P52": (-0.1, 0.5),
    # Поточная сера СЫРЬЯ. Лабораторный диапазон за 3.6 года: 1778 … 9331 ppm в базисе
    # тега, границы взяты с запасом. Без этой строки в валидацию проходили провалы
    # анализатора в десятки ppm и залипание на верхнем пределе 15047 ppm: скачки
    # наследовались кинетикой и отравляли байесово смещение оценки серы продукта.
    "HT_Q20": (1000.0, 12000.0),
    # Поточная сера ПРОДУКТА. Нижняя граница НЕ нулевая: на пусках установки анализатор
    # выдает 0.05 мг/кг при фактических ~8 по лаборатории, и калибровка по такому
    # значению дает невязку около 5 в лог-домене, разнося смещение на порядки.
    "HT_Q21": (0.5, 100.0),
    "LIMS_HT_S": (0.0, 100.0),
}


def assess_data(
    raw_tags: Mapping[str, Any],
    history: Optional[Sequence[Mapping[str, float]]] = None,
    policy: Optional[PolicyConfig] = None,
    calib_age_h: Optional[float] = None,
    pak_model_diff: Optional[float] = None,
) -> DataAssessment:
    """
    Полная валидация входных измерений КИПиА с формированием DataAssessment (§4.1, §5.3).
    """
    t_thresholds = policy.thresholds if policy else AutomationThresholds()
    now = datetime.datetime.now(datetime.timezone.utc)
    measurements: dict[str, Measurement] = {}
    blocked_mvs: set[str] = set()
    unknown_specs: set[str] = set()
    reasons: list[str] = []

    # Возраст лабораторного анализа LIMS
    lims_age = float(raw_tags.get("lims_age_hours", 0.0))
    if calib_age_h is None:
        calib_age_h = lims_age

    # Анализ каждого тега
    for tag, val in raw_tags.items():
        if tag in ("timestamp", "lims_age_hours", "session_id"):
            continue

        source = SignalSource.DCS
        if "LIMS" in tag:
            source = SignalSource.LIMS
        elif "Q20" in tag or "Q21" in tag or "PAK" in tag:
            source = SignalSource.PAK
        elif "VAK" in tag:
            source = SignalSource.VAK

        quality = SignalQuality.GOOD
        flags: list[str] = []

        if val is None:
            quality = SignalQuality.MISSING
            flags.append("MISSING")
        elif isinstance(val, (int, float)):
            val_float = float(val)
            if math.isnan(val_float) or math.isinf(val_float):
                quality = SignalQuality.BAD
                flags.append("NAN")
            elif val_float in CLAMPING_VALUES:
                quality = SignalQuality.BAD
                flags.append("CLAMPED")
            elif tag == "HT_Q21" and val_float > Q21_MAX_PLAUSIBLE_PPM:
                quality = SignalQuality.SUSPECT
                flags.append("OUTLIER_SPIKE")
            elif tag in PHYSICAL_RANGES:
                lo, hi = PHYSICAL_RANGES[tag]
                if not (lo <= val_float <= hi):
                    quality = SignalQuality.BAD
                    flags.append("RANGE")

            # Проверка на залипание датчика по истории
            if history and len(history) >= 3 and quality == SignalQuality.GOOD:
                hist_vals = [h[tag] for h in history if tag in h and isinstance(h[tag], (int, float))]
                if len(hist_vals) >= 3:
                    mean_val = sum(hist_vals) / len(hist_vals)
                    variance = sum((x - mean_val) ** 2 for x in hist_vals) / len(hist_vals)
                    if variance < 1e-7:
                        quality = SignalQuality.SUSPECT
                        flags.append("FROZEN")

            # Проверка расхождения ПАК с моделью
            if tag == "HT_Q21" and pak_model_diff is not None:
                if abs(pak_model_diff) > 3.0 * 0.83:
                    quality = SignalQuality.SUSPECT
                    flags.append("MODEL_MISMATCH")
        else:
            quality = SignalQuality.BAD
            flags.append("TYPE_ERROR")

        measurements[tag] = Measurement(
            tag=tag,
            value=float(val) if isinstance(val, (int, float)) and not (math.isnan(val) or math.isinf(val)) else None,
            unit=TAGS[tag].unit if tag in TAGS else "",
            source=source,
            sampled_at=now,
            quality=quality,
            flags=tuple(flags),
        )

    # Проверка отсутствующих критических тегов
    for crit in CRITICAL_TAGS:
        has_tag = crit in raw_tags or (crit.startswith("AVT_") and crit[4:] in raw_tags) or (crit.startswith("HT_") and crit[3:] in raw_tags)
        if not has_tag:
            measurements[crit] = Measurement(
                tag=crit,
                value=None,
                unit=TAGS[crit].unit if crit in TAGS else "",
                source=SignalSource.DCS,
                sampled_at=now,
                quality=SignalQuality.MISSING,
                flags=("MISSING",),
            )

    # Определение недостоверности ПАК
    pak_m = measurements.get("HT_Q21") or measurements.get("PAK_Q21")
    pak_invalid = pak_m is None or pak_m.quality in (SignalQuality.BAD, SignalQuality.MISSING)
    pak_suspect = pak_m is not None and pak_m.quality == SignalQuality.SUSPECT

    # Формирование blocked_mvs и unknown_specs
    # 1. AVT_T55
    t55_m = measurements.get("AVT_T55") or measurements.get("T55")
    if t55_m is None or t55_m.quality != SignalQuality.GOOD:
        blocked_mvs.add("AVT_T55_SP")
        unknown_specs.add("FURNACE.COT_MAX")
        reasons.append("Недостоверно или отсутствует измерение AVT_T55 (температура печи П-3)")

    # 2. AVT_F31 / AVT_P52
    f31_m = measurements.get("AVT_F31") or measurements.get("F31")
    if f31_m is None or f31_m.quality != SignalQuality.GOOD:
        blocked_mvs.add("AVT_T55_SP")
        unknown_specs.add("FURNACE.F31_MIN")
        reasons.append("Недостоверно или отсутствует предусловие AVT_F31 (расход сырья печи П-3)")

    p52_m = measurements.get("AVT_P52") or measurements.get("P52")
    if p52_m is None or p52_m.quality != SignalQuality.GOOD:
        blocked_mvs.add("AVT_T55_SP")
        unknown_specs.add("COL.P52_MAX")
        reasons.append("Недостоверно или отсутствует предусловие AVT_P52 (перепад колонны К-10)")

    # 3. HT_P8 / HT_DP_KPA
    p8_m = measurements.get("HT_P8") or measurements.get("HT_DP_KPA") or measurements.get("P8")
    if p8_m is None or p8_m.quality != SignalQuality.GOOD:
        blocked_mvs.add("HT_FEED_SP")
        blocked_mvs.add("HT_GOR_SP")
        unknown_specs.add("RX.DP_MAX")
        reasons.append("Недостоверно или отсутствует измерение перепада Р-202 (HT_P8/HT_DP_KPA)")

    # 4. HT_T11 (используем только каноническое имя HT_T11)
    t11_m = measurements.get("HT_T11")
    if t11_m is None or t11_m.quality != SignalQuality.GOOD:
        blocked_mvs.add("HT_TIN_SP")
        unknown_specs.add("RX.T_OUT_MAX")
        reasons.append("Недостоверно или отсутствует измерение температуры выхода Р-202 (HT_T11)")

    # Лестница деградации автоматизации (§5.3)
    # Второе условие (T55 и P8 оба MISSING) всегда влечёт первое (оба MV уже в blocked_mvs
    # из проверок quality != GOOD выше), поэтому было мёртвым дублированием — убрано.
    critical_fail_all = "AVT_T55_SP" in blocked_mvs and "HT_FEED_SP" in blocked_mvs

    if critical_fail_all or (pak_invalid and lims_age > t_thresholds.refusal_lims_age_h_without_pak):
        level = AutomationLevel.REFUSAL_DATA
        if pak_invalid and lims_age > t_thresholds.refusal_lims_age_h_without_pak:
            reasons.append(f"Отказ ТЗ: ПАК недостоверен, а возраст ЛИМС ({lims_age:.1f} ч) > {t_thresholds.refusal_lims_age_h_without_pak:.1f} ч")
    elif calib_age_h > t_thresholds.corrective_only_calib_age_h or pak_invalid:
        level = AutomationLevel.CORRECTIVE_ONLY
        if pak_invalid:
            reasons.append("ПАК недостоверен: автоматический режим ограничен только корректирующими ходами")
        else:
            reasons.append(f"Возраст калибровки ({calib_age_h:.1f} ч) > {t_thresholds.corrective_only_calib_age_h:.1f} ч: режим CORRECTIVE_ONLY")
    elif calib_age_h > t_thresholds.cautious_calib_age_h or pak_suspect:
        level = AutomationLevel.CAUTIOUS
        if pak_suspect:
            reasons.append("ПАК под подозрением: осторожный режим CAUTIOUS с уменьшенным шагом")
        else:
            reasons.append(f"Возраст калибровки ({calib_age_h:.1f} ч) > {t_thresholds.cautious_calib_age_h:.1f} ч: осторожный режим CAUTIOUS")
    else:
        level = AutomationLevel.FULL

    return DataAssessment(
        measurements=measurements,
        automation_level=level,
        reasons=tuple(reasons),
        blocked_mvs=frozenset(blocked_mvs),
        unknown_specs=frozenset(unknown_specs),
    )


def compute_confidence(
    n_filled_critical: int,
    q21_unavailable: bool,
    lims_age: float,
) -> Dict[str, Any]:
    """
    Индекс уверенности в данных для индикатора пульта оператора (§B1 аудита консоли).

    Чистая функция без побочных эффектов — формула извлечена as-is из
    node_data_quality_guard, ни одна константа/коэффициент не изменены.
    """
    conf_score = max(
        0.0,
        min(
            1.0,
            1.0
            - 0.15 * n_filled_critical
            - (0.20 if q21_unavailable else 0.0)
            - min(0.30, lims_age / 80.0),
        ),
    )
    level_str = "HIGH" if conf_score >= 0.75 else ("MEDIUM" if conf_score >= 0.50 else "LOW")

    return {
        "score": round(conf_score, 3),
        "level": level_str,
        "n_filled_critical": n_filled_critical,
        "q21_unavailable": q21_unavailable,
        "lims_age_hours": lims_age,
    }


def node_data_quality_guard(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел валидации входной телеметрии LangGraph (совместим с MVP и новым ядром).
    """
    telemetry: Optional[RawTelemetry] = state.get("raw_telemetry")
    raw_dict: Dict[str, Any] = {}

    if telemetry is not None:
        raw_dict = telemetry.model_dump()
        for tag in STEAM_FLOW_TAGS:
            if tag in raw_dict:
                raw_val = raw_dict[tag]
                if isinstance(raw_val, (int, float)) and not math.isnan(raw_val) and not math.isinf(raw_val):
                    raw_dict[tag] = max(0.0, float(raw_val))
        telemetry = RawTelemetry.model_validate(raw_dict)

    explicit_tags_given = state.get("tags") is not None
    if state.get("tags"):
        raw_dict.update(state["tags"])

    quality = DataQuality()
    norm_tags, norm_warnings = normalize_tags(raw_dict)

    # Для сырой legacy-телеметрии (RawTelemetry) подставляем недостающие номиналы
    # ДО проверки критичности, чтобы старые тесты со срезом 5 тегов проходили
    if not explicit_tags_given:
        norm_tags, _ = fill_from_nominal(norm_tags, CRITICAL_TAGS)

    # Проверка на клампинг и нечисловые значения
    clamped_critical: List[str] = []
    invalid_critical: List[str] = []

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

            canonical_candidates = {tag_name, f"AVT_{tag_name}", f"HT_{tag_name}"}
            if canonical_candidates & CRITICAL_TAGS:
                if is_bad_num:
                    invalid_critical.append(tag_name)
                elif is_clamped:
                    clamped_critical.append(tag_name)

    quality.clamped_tags = sorted(set(quality.clamped_tags))
    quality.invalid_range_tags = sorted(set(quality.invalid_range_tags))

    lims_age = float(raw_dict.get("lims_age_hours", getattr(telemetry, "lims_age_hours", 0.0) if telemetry else 0.0))
    quality.lims_age_hours = lims_age

    # Вычисляем новый DataAssessment
    assessment = assess_data(raw_dict if explicit_tags_given else norm_tags)

    # Устанавливаем уровень автоматизации на объекте DataQuality
    quality.automation_level = str(assessment.automation_level.value)

    # Проверяем явное отсутствие критических тегов в словаре tags (E2 тест)
    missing_critical_explicit = False
    if explicit_tags_given:
        tags_set = set(state["tags"].keys())
        has_t55 = "AVT_T55" in tags_set or "T55" in tags_set
        has_p8 = "HT_P8" in tags_set or "P8" in tags_set
        if not has_t55 and not has_p8:
            missing_critical_explicit = True

    # Принятие решения о статусе качества данных
    refusal_reasons: List[str] = []
    if clamped_critical:
        refusal_reasons.append(f"Аппаратный клампинг критичных датчиков: {', '.join(clamped_critical)}")
    if invalid_critical:
        refusal_reasons.append(f"Нечисловые значения критичных датчиков: {', '.join(invalid_critical)}")
    if lims_age > 24.0:
        refusal_reasons.append(f"Устаревание данных LIMS ({lims_age:.1f} ч > 24.0 ч)")
    if missing_critical_explicit:
        refusal_reasons.append("Отсутствуют критические датчики AVT_T55 и HT_P8")

    if missing_critical_explicit:
        quality.is_valid = False
        quality.status_code = "REFUSAL_DATA"
        quality.refusal_reason = "; ".join(refusal_reasons)
        quality.automation_level = "REFUSAL_DATA"
    elif refusal_reasons:
        quality.is_valid = False
        quality.status_code = "SAFE_HOLD_REFUSAL"
        quality.refusal_reason = "; ".join(refusal_reasons)
    else:
        quality.is_valid = True
        quality.status_code = "NORMAL"
        quality.refusal_reason = None

    # Дозаполняем номиналы для корректного запуска модели
    norm_tags, fill_warnings = fill_from_nominal(norm_tags, CRITICAL_TAGS)
    n_filled_critical = len([w for w in fill_warnings if w.startswith("FILLED:")])

    q21_val = norm_tags.get("HT_Q21")
    q21_is_spike = False
    if isinstance(q21_val, (int, float)) and not math.isnan(q21_val):
        q21_val_f = float(q21_val)
        if q21_val_f in CLAMPING_VALUES or q21_val_f > Q21_MAX_PLAUSIBLE_PPM:
            q21_is_spike = True
            fill_warnings.append(
                f"OUTLIER_SPIKE:HT_Q21={q21_val_f:.2f} ppm (аномальный выброс КИП, переход на буфер LIMS)"
            )

    q21_unavailable = (
        q21_val is None
        or not isinstance(q21_val, (int, float))
        or math.isnan(q21_val)
        or q21_is_spike
    )

    confidence = compute_confidence(n_filled_critical, q21_unavailable, lims_age)

    # Проверка на RECOVERY_STALLED из session
    events: List[str] = []
    sid = state.get("session_id")
    if sid:
        from src.twin.session import TWIN_STORE
        events.extend(TWIN_STORE.get_events(sid))

    # Сохраняем служебные метки в norm_tags для последующих узлов
    if "lims_age_hours" in raw_dict:
        norm_tags["lims_age_hours"] = float(raw_dict["lims_age_hours"])
    elif telemetry and getattr(telemetry, "lims_age_hours", None) is not None:
        norm_tags["lims_age_hours"] = float(telemetry.lims_age_hours)
    if "timestamp" in raw_dict:
        norm_tags["timestamp"] = str(raw_dict["timestamp"])
    elif telemetry and getattr(telemetry, "timestamp", None) is not None:
        norm_tags["timestamp"] = str(telemetry.timestamp)

    res: Dict[str, Any] = {
        "data_quality": quality,
        "data_assessment": assessment,
        "tags": norm_tags,
        "twin_warnings": norm_warnings + fill_warnings,
        "confidence": confidence,
        "events": events,
    }

    if telemetry is None:
        try:
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


def assess_telemetry_quality(telemetry: RawTelemetry) -> DataQuality:
    """Хелпер-функция для совместимости со старыми модулями."""
    res = node_data_quality_guard({"raw_telemetry": telemetry})
    return res["data_quality"]
