"""Тесты контракта данных и тегов КИПиА (Этап 0, test_step7_tags)."""

import math
from pathlib import Path
import pytest

from src.twin.tags import (
    TAGS,
    AVT_RAW,
    HT_RAW,
    AMBIGUOUS_RAW,
    SemanticStatus,
    normalize_tags,
    fill_from_nominal,
    NOMINAL_OPERATING_POINT,
    DERIVED,
)


def test_ambiguous_tags_set():
    """Тест 1: Пересечение AVT_RAW и HT_RAW содержит ровно 8 неоднозначных имен."""
    assert AVT_RAW & HT_RAW == AMBIGUOUS_RAW
    assert len(AMBIGUOUS_RAW) == 8
    expected = {"T6", "F9", "T11", "F14", "T18", "F19", "F25", "F26"}
    assert AMBIGUOUS_RAW == expected


def test_legacy_aliases_normalization():
    """Тест 2: Legacy-алиасы F26, F15, T55, Sulfur нормализуются в канонические ключи."""
    raw = {
        "F26": 258.0,
        "F15": 3400.0,
        "T55": 381.0,
        "Sulfur": 8.4,
    }
    norm, warns = normalize_tags(raw)
    assert norm["HT_F26"] == 258.0
    assert norm["HT_F15"] == 3400.0
    assert norm["AVT_T55"] == 381.0
    assert norm["HT_Q21"] == 8.4


def test_ambiguous_raw_tag_handling():
    """Тест 3: Неоднозначный raw-тег дублируется в AVT_x и HT_x с предупреждением AMBIGUOUS_TAG."""
    raw = {"T6": 360.0}
    norm, warns = normalize_tags(raw)
    assert norm["AVT_T6"] == 360.0
    assert norm["HT_T6"] == 360.0
    assert any("AMBIGUOUS_TAG:T6" in w for w in warns)


def test_official_tag_semantics():
    """Тест 4: Официальная семантика КИПиА (W10 — бензин, P8 — dp, F14 — квенч, T18 — vak, F15 — UNIT_INCONSISTENT)."""
    assert "HT_W10" in TAGS
    assert "бензин" in TAGS["HT_W10"].description.lower()

    assert "HT_P8" in TAGS
    assert TAGS["HT_P8"].kind == "dp"

    assert "HT_F14" in TAGS
    assert "квенч" in TAGS["HT_F14"].description.lower()

    assert "HT_T18" in TAGS
    assert TAGS["HT_T18"].kind == "vak"

    assert "HT_F15" in TAGS
    assert TAGS["HT_F15"].status == SemanticStatus.UNIT_INCONSISTENT


def test_derived_tags_calculation(nominal_tags):
    """Тест 5: Расчет производных тегов HT_GOR (360 ± 1) и HT_BED_MEAN (363.65)."""
    norm, _ = normalize_tags(nominal_tags)
    assert "HT_GOR" in norm
    assert pytest.approx(360.0, abs=1.5) == norm["HT_GOR"]

    assert "HT_BED_MEAN" in norm
    # (363.3 + 364.0) / 2 = 363.65
    assert pytest.approx(363.65, abs=0.05) == norm["HT_BED_MEAN"]

    assert "HT_DP_KPA" in norm
    assert pytest.approx(177.0, abs=0.1) == norm["HT_DP_KPA"]

    assert "AVT_DIESEL_TPH" in norm
    # 128.3 + 81.6 = 209.9
    assert pytest.approx(209.9, abs=0.1) == norm["AVT_DIESEL_TPH"]


def test_descriptions_match_official_xlsx():
    """Тест 6: Описания 97 тегов совпадают с официальным файлом теги АВТ_24-2000.xlsx."""
    xlsx_path = Path("new_data/теги АВТ_24-2000.xlsx")
    if not xlsx_path.exists():
        pytest.skip("Файл new_data/теги АВТ_24-2000.xlsx недоступен")

    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)

    avt_sheet = wb["АВТ"]
    for r in range(2, avt_sheet.max_row + 1):
        raw = avt_sheet.cell(r, 1).value
        desc = avt_sheet.cell(r, 2).value
        if raw and desc:
            c_name = f"AVT_{raw.strip()}"
            assert c_name in TAGS
            assert TAGS[c_name].description == desc.strip()

    ht_sheet = wb["24-2000"]
    for r in range(2, ht_sheet.max_row + 1):
        raw = ht_sheet.cell(r, 1).value
        desc = ht_sheet.cell(r, 2).value
        if raw and desc:
            c_name = f"HT_{raw.strip()}"
            assert c_name in TAGS
            assert TAGS[c_name].description == desc.strip()


def test_fill_from_nominal():
    """Тест 7: fill_from_nominal подставляет пропущенные значения и выдает предупреждения."""
    partial = {"AVT_F30": 120.0}
    required = ["AVT_F30", "HT_F9", "HT_T6"]
    filled, warns = fill_from_nominal(partial, required)
    assert filled["AVT_F30"] == 120.0
    assert filled["HT_F9"] == NOMINAL_OPERATING_POINT["HT_F9"]
    assert filled["HT_T6"] == NOMINAL_OPERATING_POINT["HT_T6"]
    assert "FILLED:HT_F9" in warns
    assert "FILLED:HT_T6" in warns


def test_static_scan_no_ambiguous_raw_telemetry_keys():
    """Тест 8 (План §8.1, п.6): Статический скан: в src/ нет литералов из AMBIGUOUS_RAW в роли ключей телеметрии."""
    src_dir = Path("src")
    excluded_files = {"tags.py", "vak.py", "blending.py", "narrative.py"}

    for py_file in src_dir.rglob("*.py"):
        if py_file.name in excluded_files:
            continue
        content = py_file.read_text(encoding="utf-8")
        for amb in AMBIGUOUS_RAW:
            assert f'["{amb}"]' not in content, f"Обнаружен неоднозначный тег {amb} в {py_file}"
            assert f"['{amb}']" not in content, f"Обнаружен неоднозначный тег {amb} в {py_file}"
            assert f'.get("{amb}")' not in content, f"Обнаружен неоднозначный тег {amb} в {py_file}"
            assert f".get('{amb}')" not in content, f"Обнаружен неоднозначный тег {amb} в {py_file}"
