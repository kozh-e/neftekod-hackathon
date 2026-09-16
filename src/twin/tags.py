"""Реестр тегов технологического комплекса ЭЛОУ-АВТ-6 и 24-2000.

Содержит спецификации 97 тегов КИПиА из официального реестра new_data/теги АВТ_24-2000.xlsx,
нормализацию тегов, разрешение коллизий имен и номинальную рабочую точку.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Tuple


class SemanticStatus(str, Enum):
    CONFIRMED = "CONFIRMED"                  # официальный реестр + правдоподобные значения
    UNIT_INCONSISTENT = "UNIT_INCONSISTENT"  # официальный смысл, единица не сходится с данными (HT_F15, HT_F17, AVT_P21..P23, AVT_P67)
    VIRTUAL = "VIRTUAL"                      # расчётная или модельная величина


@dataclass(frozen=True)
class TagSpec:
    name: str                                  # "HT_F9", "AVT_F30", "LIMS_HT_S", "PAK_D15", "HT_GOR", "HT_BED_MEAN"
    unit_code: Literal["AVT6", "24-2000", "PAK", "LIMS", "MODEL"]
    raw: str | None                            # "F9"
    description: str                           # verbatim из new_data/теги АВТ_24-2000.xlsx
    unit: str                                  # verbatim из колонки «Физическая величина»
    kind: Literal["flow", "temperature", "pressure", "dp", "level", "density", "analyzer", "vak", "other"]
    status: SemanticStatus
    model_range: tuple[float, float] | None = None  # q05–q95 рабочих периодов, ASSUMPTION


# Сырые имена тегов по официальному реестру
AVT_RAW_SPECS: list[tuple[str, str, str, str]] = [
    ("T1", "Температура верха К-1 (выход на FIRC0955)", "температура, °C", "temperature"),
    ("P2", "Давление бензиновых паров верха К-1", "давление, МПа", "pressure"),
    ("F3", "Расход бензина на орошение К-1", "расход, т/ч", "flow"),
    ("P4", "Давление низа К-1 в Е-6", "давление, МПа", "pressure"),
    ("F5", "Расход пара в К-1", "расход, т/ч", "flow"),
    ("T6", "Температура низа К-1", "температура, °C", "temperature"),
    ("F7", "Расход обессоленной нефти (3-й ход) в Т-4/2", "расход, т/ч", "flow"),
    ("F8", "Расход обессоленной нефти (1-й ход) в Т-6/1", "расход, т/ч", "flow"),
    ("F9", "Расход обессоленной нефти (2-й ход) в Т-10/2", "расход, т/ч", "flow"),
    ("D10", "Плотность нефти на подаче", "плотность, кг/м3", "density"),
    ("T11", "3-е ЦО после Т-46", "температура, °C", "temperature"),
    ("F12", "Расход 2-го ЦО в К-2", "расход, т/ч", "flow"),
    ("T13", "1-е ЦО К-2 после Т-30", "температура, °C", "temperature"),
    ("F14", "Расход 1-го ЦО в К-2", "расход, т/ч", "flow"),
    ("T15", "3-е ЦО К-2", "температура, °C", "temperature"),
    ("F16", "Расход бензина от Н-4 в Е-6", "расход, т/ч", "flow"),
    ("T17", "2-е ЦО из К-2", "температура, °C", "temperature"),
    ("T18", "1-е ЦО из К-2", "температура, °C", "temperature"),
    ("F19", "Расход острого орошения К-2", "расход, т/ч", "flow"),
    ("T20", "Температура верха К-2 (выход на FIRC0953)", "температура, °C", "temperature"),
    ("P21", "Давление верха К-2 (измерительное)", "давление, МПа", "pressure"),
    ("P22", "Давление верха К-2 (регистрирующее)", "давление, МПа", "pressure"),
    ("P23", "Давление верха К-2 (регулятор, ветка B)", "давление, МПа", "pressure"),
    ("T24", "Температура перетока К-2 в К-6", "температура, °C", "temperature"),
    ("F25", "Расход бензина после Т-26", "расход, т/ч", "flow"),
    ("F26", "Расход пара в К-6", "расход, т/ч", "flow"),
    ("F27", "Расход пара в К-7", "расход, т/ч", "flow"),
    ("F28", "Расход пара в К-9", "расход, т/ч", "flow"),
    ("F29", "Расход пара в К-2", "расход, т/ч", "flow"),
    ("F30", "Расход фр. 290-350 с установки", "расход, т/ч", "flow"),
    ("F31", "Расход нефтепродукта в П-3", "расход, т/ч", "flow"),
    ("F32", "Расход фр. 240-290 с установки", "расход, т/ч", "flow"),
    ("T33", "Температура низа К-2", "температура, °C", "temperature"),
    ("F34", "Расход фр. 150-250 с установки", "расход, т/ч", "flow"),
    ("F35", "Расход ВЦО в К-10", "расход, т/ч", "flow"),
    ("F36", "Расход СЦО в К-10", "расход, т/ч", "flow"),
    ("T37", "Температура ВЦО в К-10", "температура, °C", "temperature"),
    ("T38", "Температура доп. ВЦО К-10", "температура, °C", "temperature"),
    ("T39", "Температура в К-10 над секцией насадки 1а", "температура, °C", "temperature"),
    ("T40", "Температура ВЦО из К-10", "температура, °C", "temperature"),
    ("F41", "Расход доп. ВЦО в К-10", "расход, т/ч", "flow"),
    ("T42", "Температура фр. 420-500 из К-10 в Е-12", "температура, °C", "temperature"),
    ("L43", "Уровень в К-10", "уровень, %", "level"),
    ("P44", "Вакуум низа К-10", "давление, МПа", "pressure"),
    ("F45", "Расход холодного гудрона в К-10", "расход, т/ч", "flow"),
    ("F46", "Расход НЦО К-10 (фр. 350-560)", "расход, т/ч", "flow"),
    ("T47", "Температура фр. 350-560 после Т-40", "температура, °C", "temperature"),
    ("T48", "Температура низа К-10", "температура, °C", "temperature"),
    ("T49", "Температура верха К-10", "температура, °C", "temperature"),
    ("P50", "Вакуум верха К-10", "давление, МПа", "pressure"),
    ("P51", "Вакуум верха К-10 (пересчёт в мм рт.ст.)", "давление, мм рт.ст.", "pressure"),
    ("P52", "Перепад давления над 4-й пак. насадки К-10", "перепад, МПа", "dp"),
    ("F53", "Расход НЦО К-10 (регулятор)", "расход, т/ч", "flow"),
    ("F54", "Расход от Н-34/1,2 в К-10", "расход, т/ч", "flow"),
    ("T55", "Температура на выходе из П-3 (на FIRC2470)", "температура, °C", "temperature"),
    ("F56", "Расход фр. до 350 с установки (реальный)", "расход, т/ч", "flow"),
    ("F57", "Расход фр. до 350 с установки (виртуальный)", "расход, т/ч", "flow"),
    ("T58", "Температура фр. до 350 от Т-37", "температура, °C", "temperature"),
    ("F59", "Расход фр. 420-500 с установки (объемный)", "расход, м3/ч", "flow"),
    ("F60", "Расход фр. 350-560 с установки", "расход, т/ч", "flow"),
    ("T61", "Температура НЦО в К-10", "температура, °C", "temperature"),
    ("F62", "Расход НЦО К-10", "расход, т/ч", "flow"),
    ("F63", "Расход гудрона на битумную установку", "расход, т/ч", "flow"),
    ("F64", "Расход 3-го ЦО в К-2", "расход, т/ч", "flow"),
    ("F65", "Производительность К-2 по отбензиненной нефти", "расход, т/ч", "flow"),
    ("T66", "Температура перетока в К-9", "температура, °C", "temperature"),
    ("P67", "Давление верха К-2 (доп. точка)", "давление, МПа", "pressure"),
    ("F68", "Расход гудрона с установки (реальный)", "расход, т/ч", "flow"),
    ("F69", "Расход гудрона с установки (виртуальный)", "расход, т/ч", "flow"),
    ("W70", "Массовый расход фр. 290-350 с установки", "масс.расход, т/ч", "flow"),
    ("T71", "Температура фр. 290-350 из К-2", "температура, °C", "temperature"),
]

HT_RAW_SPECS: list[tuple[str, str, str, str]] = [
    ("F1", "Расход бензина с установки, объемный", "расход, м³/ч", "flow"),
    ("F2", "Газовая схема. Расход газа на линии от ЦК-201", "расход газа, нм³/ч", "flow"),
    ("P3", "Полисеп. Сепаратор С-201. Давление на входе", "давление, МПа", "pressure"),
    ("W4", "К-206. Массовый расход бензина в колонну", "масс.расход, т/ч", "flow"),
    ("T5", "Полисеп. Р-201. Температура ГСС на выходе", "температура, °C", "temperature"),
    ("T6", "Полисеп. Р-202. Температура ГСС на входе", "температура, °C", "temperature"),
    ("W7", "К-201. Расход газа поддува на входе, массовый", "масс.расход газа, т/ч", "flow"),
    ("P8", "Реактор Р-202. Перепад давления", "перепад, МПа", "dp"),
    ("F9", "Расход сырья на установку, массовый", "масс.расход, т/ч", "flow"),
    ("W10", "Расход бензина с установки, массовый", "масс.расход, т/ч", "flow"),
    ("T11", "Полисеп. Трубопровод на выходе из Р-202. Температура ГСС / продукта", "температура, °C", "temperature"),
    ("T12", "К-201. Температура верха колонны, бензин, УВГ, H2S", "температура, °C", "temperature"),
    ("P13", "Полисеп. Р-202. Давление на входе", "давление, МПа", "pressure"),
    ("F14", "Полисеп. Расход квенча в реактор Р-202", "расход, т/ч", "flow"),
    ("F15", "Расход сырья на установку, объемный", "расход, м³/ч", "flow"),
    ("T16", "Блок стабилизации. Температура в сепараторе С-205", "температура, °C", "temperature"),
    ("F17", "Расход гидроочищенного ДТ в цех №8, массовый", "масс.расход, т/ч", "flow"),
    ("T18", "ВАК. Температура вспышки ГОДТ, аналитический показатель", "°C", "vak"),
    ("F19", "К-201. Расход бензина в колонну", "расход, т/ч", "flow"),
    ("Q20", "Поточный анализатор серы в ДТ, Н-202/1,2", "сера, ppm", "analyzer"),
    ("Q21", "Поточный анализатор серы в г/о ДТ", "сера, ppm", "analyzer"),
    ("F22", "К-201. Расход газа поддува на входе, объемный", "расход газа, нм³/ч", "flow"),
    ("T23", "К-201. Температура низа колонны, стабилизированный гидрогенизат", "температура, °C", "temperature"),
    ("P24", "К-201. Давление на выходе колонны", "давление, МПа", "pressure"),
    ("F25", "Расход свежего ВСГ с КЦА на установку", "расход ВСГ, нм³/ч", "flow"),
    ("F26", "Расход гидроочищенного ДТ в цех №8, объемный", "расход, м³/ч", "flow"),
]

AVT_RAW: frozenset[str] = frozenset(t[0] for t in AVT_RAW_SPECS)
HT_RAW: frozenset[str] = frozenset(t[0] for t in HT_RAW_SPECS)
AMBIGUOUS_RAW: frozenset[str] = AVT_RAW & HT_RAW  # 8 тегов: T6, F9, T11, F14, T18, F19, F25, F26

# Теги с несходящимися единицами или несоответствиями физического смысла
UNIT_INCONSISTENT_TAGS: frozenset[str] = frozenset({
    "HT_F15", "HT_F17", "AVT_P21", "AVT_P22", "AVT_P23", "AVT_P67"
})

TAGS: dict[str, TagSpec] = {}

for raw_name, desc, unit, kind in AVT_RAW_SPECS:
    c_name = f"AVT_{raw_name}"
    st = SemanticStatus.UNIT_INCONSISTENT if c_name in UNIT_INCONSISTENT_TAGS else SemanticStatus.CONFIRMED
    TAGS[c_name] = TagSpec(
        name=c_name,
        unit_code="AVT6",
        raw=raw_name,
        description=desc,
        unit=unit,
        kind=kind,  # type: ignore[arg-type]
        status=st,
    )

for raw_name, desc, unit, kind in HT_RAW_SPECS:
    c_name = f"HT_{raw_name}"
    st = SemanticStatus.UNIT_INCONSISTENT if c_name in UNIT_INCONSISTENT_TAGS else SemanticStatus.CONFIRMED
    TAGS[c_name] = TagSpec(
        name=c_name,
        unit_code="24-2000",
        raw=raw_name,
        description=desc,
        unit=unit,
        kind=kind,  # type: ignore[arg-type]
        status=st,
    )

# Дополнительные спецификации для модельных и виртуальных тегов
TAGS["HT_BED_MEAN"] = TagSpec(
    name="HT_BED_MEAN",
    unit_code="24-2000",
    raw=None,
    description="Средняя температура слоя реактора Р-202 (T6+T11)/2",
    unit="°C",
    kind="temperature",
    status=SemanticStatus.VIRTUAL,
)
TAGS["HT_GOR"] = TagSpec(
    name="HT_GOR",
    unit_code="24-2000",
    raw=None,
    description="Отношение циркулирующий ВСГ / сырье F2/(F9/rho_feed)",
    unit="нм3/м3",
    kind="other",
    status=SemanticStatus.VIRTUAL,
)
TAGS["HT_DP_KPA"] = TagSpec(
    name="HT_DP_KPA",
    unit_code="24-2000",
    raw=None,
    description="Перепад давления реактора Р-202 в кПа (P8 * 1000)",
    unit="кПа",
    kind="dp",
    status=SemanticStatus.VIRTUAL,
)
TAGS["AVT_DIESEL_TPH"] = TagSpec(
    name="AVT_DIESEL_TPH",
    unit_code="AVT6",
    raw=None,
    description="Суммарная выработка дизельных фракций АВТ (F30+F32)",
    unit="т/ч",
    kind="flow",
    status=SemanticStatus.VIRTUAL,
)

LEGACY_ALIASES: dict[str, str] = {
    "F15": "HT_F15",
    "T55": "AVT_T55",
    "P52": "AVT_P52",
    "D10": "AVT_D10",
    "F5": "AVT_F5",
    "F26": "HT_F26",
    "Sulfur": "HT_Q21",
    "flash_diesel": "LIMS_HT_FLASH",
}

RHO_FEED_T_M3: float = 0.847

DERIVED: dict[str, Callable[[Mapping[str, float]], float]] = {
    "HT_BED_MEAN": lambda t: (t["HT_T6"] + t["HT_T11"]) / 2.0,
    "HT_GOR": lambda t: t["HT_F2"] / (t["HT_F9"] / RHO_FEED_T_M3),
    "HT_DP_KPA": lambda t: t["HT_P8"] * 1000.0,
    "AVT_DIESEL_TPH": lambda t: t["AVT_F30"] + t["AVT_F32"],
}

NOMINAL_OPERATING_POINT: dict[str, float] = {
    "HT_F9": 219.6,
    "AVT_F30": 128.3,
    "AVT_F32": 81.6,
    "HT_T6": 363.3,
    "HT_T11": 364.0,
    "HT_T5": 370.3,
    "AVT_F65": 924.5,
    "HT_P13": 3.922,
    "HT_P8": 0.177,
    "AVT_T55": 381.7,
    "AVT_T33": 338.3,
    "AVT_T71": 304.4,
    "HT_F2": 93309.0,
    "HT_GOR": 360.0,
    "HT_Q20": 8116.0,
    "HT_Q21": 8.43,
    "HT_F14": 6.05,
    "HT_F25": 13199.0,
    "HT_T18": 68.3,
    "HT_P24": 0.585,
    "HT_W7": 0.173,
    "HT_T23": 235.5,
    "PAK_D15": 835.2,
    "HT_BED_MEAN": 363.65,
    "HT_DP_KPA": 177.0,
    "AVT_DIESEL_TPH": 209.9,
    "AVT_D10": 847.2,
    "AVT_P52": 0.045,
    "LIMS_HT_S": 8.6,
    "LIMS_HT_FLASH": 68.0,
    "LIMS_HT_D15": 836.1,
    "LIMS_HT_T95": 347.0,
    "LIMS_HT_FEED_S": 9470.0,
}

SERVICE_KEYS: frozenset[str] = frozenset({
    "timestamp",
    "lims_age_hours",
    "session_id",
})


def normalize_tags(raw: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    """
    Преобразует произвольный словарь входных тегов в канонические ключи AVT_* / HT_*.

    Правила нормализации по порядку приоритета:
    1) Канонический ключ (AVT_*, HT_*, PAK_*, LIMS_*) -> сохранить как есть;
    2) LEGACY_ALIASES -> переименовать в канонический;
    3) Неоднозначный raw (в AMBIGUOUS_RAW) -> записать в AVT_x и HT_x, выдать предупреждение "AMBIGUOUS_TAG:x";
    4) Однозначный raw -> префикс по AVT_RAW / HT_RAW;
    5) Ключи ВАК ("AVT6:...", "24-2000:...") и LIMS ("LIMS:...") -> сохранить как есть;
    6) Служебные поля (timestamp, lims_age_hours) игнорируются;
    7) DERIVED рассчитываются, если все входные теги конечны.
    NaN и Inf сохраняются как math.nan для проверки в DataGuard.
    """
    normalized: dict[str, float] = {}
    warnings: list[str] = []

    def _to_float(val: Any) -> float:
        if val is None:
            return float("nan")
        try:
            num = float(val)
            return num
        except (ValueError, TypeError):
            return float("nan")

    for k, v in raw.items():
        if k in SERVICE_KEYS:
            continue

        val_f = _to_float(v)

        # 1) Уже канонические имена
        if k.startswith("AVT_") or k.startswith("HT_") or k.startswith("PAK_") or k.startswith("LIMS_"):
            normalized[k] = val_f
            continue

        # 5) ВАК и LIMS расширенные теги
        if k.startswith("AVT6:") or k.startswith("24-2000:") or k.startswith("LIMS:"):
            normalized[k] = val_f
            continue

        # 2) Проверка на legacy-алиасы (приоритет над сырыми)
        if k in LEGACY_ALIASES:
            canon = LEGACY_ALIASES[k]
            normalized[canon] = val_f
            continue

        # 3) Неоднозначный тег КИПиА
        if k in AMBIGUOUS_RAW:
            normalized[f"AVT_{k}"] = val_f
            normalized[f"HT_{k}"] = val_f
            warnings.append(f"AMBIGUOUS_TAG:{k}")
            continue

        # 4) Однозначный тег
        if k in AVT_RAW:
            normalized[f"AVT_{k}"] = val_f
            continue
        if k in HT_RAW:
            normalized[f"HT_{k}"] = val_f
            continue

        # Иначе сохраняем как есть
        normalized[k] = val_f

    # 7) Вычисление DERIVED
    for derived_key, calc_fn in DERIVED.items():
        try:
            val = calc_fn(normalized)
            if not (math.isnan(val) or math.isinf(val)):
                normalized[derived_key] = val
        except (KeyError, ZeroDivisionError, TypeError):
            pass

    return normalized, warnings


def fill_from_nominal(tags: dict[str, float], required: Iterable[str]) -> tuple[dict[str, float], list[str]]:
    """
    Подставляет номинальные значения для отсутствующих обязательных тегов.

    :param tags: Словарь тегов (уже нормализованный).
    :param required: Список необходимых тегов.
    :return: (заполненный словарь тегов, список предупреждений FILLED:<tag>).
    """
    filled = dict(tags)
    warnings: list[str] = []

    for req in required:
        val = filled.get(req)
        if val is None or math.isnan(val) or math.isinf(val):
            if req in NOMINAL_OPERATING_POINT:
                filled[req] = NOMINAL_OPERATING_POINT[req]
                warnings.append(f"FILLED:{req}")

    return filled, warnings
