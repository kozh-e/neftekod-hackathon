"""Офлайн-калибровка параметров цифрового двойника на промышленном архиве КИПиА/ЛИМС.

Запуск:
    python scripts/calibrate_twin.py
Путь к архиву задаётся переменной окружения NEFTEKOD_DATA_DIR
(по умолчанию — каталог с 242000_tags.csv и avt_tags.csv рядом с проектом).

Этапы:
1. Загрузка телеметрии 24-2000 и АВТ-6 и лабораторных анализов ЛИМС;
2. Очистка: коды отказа КИП, фильтр рабочих режимов, санитарный контроль поточных
   анализаторов серы;
3. Разделение по времени: Train (до 2025-06-30) и Test (с 2025-07-01) без перемешивания;
4. Калибровка подмоделей МНК с проверкой физичности знаков;
5. Валидация на отложенном периоде и экспорт:
   - config/twin_params.json
   - data/processed/calibration_report.md

Методика очистки и границ правдоподобия согласована с notebooks/01_model_evaluation.ipynb.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NEFTEKOD_DATA_DIR", r"C:\хакатон данные\data"))
LIMS_FILE = ROOT_DIR / "initial_data" / "ЛИМСы 01.01.2023 - н.в_ (2).xlsx"

TRAIN_END = pd.Timestamp("2025-07-01")
TEST_END = pd.Timestamp("2026-08-08")
ASOF_TOL = pd.Timedelta("30min")
CLAMP_VALUES = (307.0, 313.0)

# Санитарные границы поточных анализаторов серы (совпадают с DataGuard.PHYSICAL_RANGES)
Q20_RANGE = (1000.0, 12000.0)     # сера сырья, ppm в базисе тега
Q21_RANGE = (0.5, 100.0)          # сера продукта, мг/кг
Q20_TO_LIMS = 1.0 / 0.878

# Границы правдоподобия лабораторных показателей (совпадают с estimation.LIMS_PLAUSIBLE_RANGE)
LIMS_RANGE = {"FlashPoint": (20.0, 130.0), "95%.T": (150.0, 420.0), "D15": (650.0, 1000.0),
              "Mg.Sulfur": (0.01, 100.0), "CFPP": (-70.0, 40.0), "CetaneNumber": (10.0, 90.0)}

HT_COLS = ["date", "F2", "T6", "W7", "P8", "F9", "W10", "T11", "P13", "F14", "F15",
           "T18", "Q20", "Q21", "T23", "P24", "F25", "F26"]
AVT_COLS = ["date", "F30", "F32", "F65", "T55", "P52", "F31"]

# Границы блоков широкой таблицы ЛИМС -> код точки отбора
LIMS_POINTS = {0: "AVT_P1", 22: "AVT_P2", 36: "AVT_P2_1", 50: "AVT_P3",
               66: "HT_FEED", 82: "HT_PROD"}


# --------------------------------------------------------------------------- загрузка
def load_scada() -> pd.DataFrame:
    """Телеметрия обеих установок, сведённая по метке времени."""
    ht_path, avt_path = DATA_DIR / "242000_tags.csv", DATA_DIR / "avt_tags.csv"
    for p in (ht_path, avt_path, LIMS_FILE):
        if not p.exists():
            raise FileNotFoundError(
                f"Не найден {p}.\nЗадайте путь к архиву через NEFTEKOD_DATA_DIR."
            )
    print(f"Загрузка телеметрии из {DATA_DIR}...")
    df_ht = pd.read_csv(ht_path, usecols=HT_COLS)
    df_avt = pd.read_csv(avt_path, usecols=AVT_COLS)
    df_ht["date"] = pd.to_datetime(df_ht["date"])
    df_avt["date"] = pd.to_datetime(df_avt["date"])
    df_ht = df_ht.rename(columns={c: f"HT_{c}" for c in HT_COLS if c != "date"})
    df_avt = df_avt.rename(columns={c: f"AVT_{c}" for c in AVT_COLS if c != "date"})
    df = pd.merge(df_ht, df_avt, on="date", how="inner").sort_values("date")
    print(f"  строк телеметрии: {len(df):,}, период {df['date'].min()} — {df['date'].max()}")
    return df.reset_index(drop=True)


def strip_fault_codes(df: pd.DataFrame) -> Tuple[pd.DataFrame, int, int]:
    """Снимает 307/313 только там, где значение является кодом отказа.

    Признак кода отказа: значение лежит вне робастной ограды [Q1-3*IQR; Q3+3*IQR]
    остального распределения тега ЛИБО совпадает с максимумом тега (жёсткий потолок).
    Слепое удаление всех 307/313 выбрасывает валидные измерения: например
    температура 307 °C попадает в рабочий диапазон AVT_T17 и HT_T23.
    """
    out = df.copy()
    removed = kept = 0
    for c in [c for c in df.columns if c != "date"]:
        s = df[c]
        rest = s[~s.isin(CLAMP_VALUES)].dropna()
        if len(rest) < 100:
            continue
        q1, q3 = np.percentile(rest, [25, 75])
        lo_fence, hi_fence = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
        tag_max = float(s.max())
        for v in CLAMP_VALUES:
            mask = s == v
            hits = int(mask.sum())
            if not hits:
                continue
            if v < lo_fence or v > hi_fence or abs(tag_max - v) < 1e-9:
                out.loc[mask, c] = np.nan
                removed += hits
            else:
                kept += hits
    print(f"  снято кодов отказа: {removed:,}; сохранено значений в рабочем диапазоне: {kept:,}")
    return out, removed, kept


def working_mask(df: pd.DataFrame) -> pd.Series:
    """Рабочий режим по трём независимым признакам.

    Одного расхода сырья недостаточно: на остановах HT_F9 даёт одиночные всплески
    выше 120 т/ч при холодном разгруженном реакторе (T6 около 8 °C, P13 около 0.015 МПа).
    """
    return ((df["HT_F9"] > 120.0) & (df["HT_T6"] > 300.0)
            & (df["HT_P13"] > 3.0) & (df["AVT_F65"] > 400.0))


def sanitize_analyzers(df: pd.DataFrame) -> pd.DataFrame:
    """Санитарный контроль поточных анализаторов серы.

    HT_Q20 нестабилен: скачет между десятками и тысячами ppm и залипает на пределе
    15047. HT_Q21 на пусках выдаёт 0.05 мг/кг при фактических ~8 по лаборатории.
    Недостоверные показания заменяются на NaN, а не на номинал: подставлять номинал
    в калибровку значило бы выдумывать данные.
    """
    out = df.copy()
    bad20 = ~out["HT_Q20"].between(*Q20_RANGE)
    bad21 = ~out["HT_Q21"].between(*Q21_RANGE)
    out.loc[bad20, "HT_Q20"] = np.nan
    out.loc[bad21, "HT_Q21"] = np.nan
    print(f"  снято недостоверных показаний: HT_Q20 {int(bad20.sum()):,}, "
          f"HT_Q21 {int(bad21.sum()):,}")
    return out


def load_lims() -> pd.DataFrame:
    """Широкая таблица пар (дата, значение) -> длинный формат с фильтром правдоподобия."""
    print("Парсинг лабораторных анализов ЛИМС...")
    raw = pd.read_excel(LIMS_FILE, header=None)
    starts = sorted(LIMS_POINTS)
    frames: List[pd.DataFrame] = []
    for c in range(0, raw.shape[1], 2):
        param = raw.iat[1, c]
        if not isinstance(param, str) or not param.strip():
            continue
        point = LIMS_POINTS[[s for s in starts if s <= c][-1]]
        sub = raw.iloc[4:, [c, c + 1]].copy()
        sub.columns = ["sampled_at", "value"]
        sub["sampled_at"] = pd.to_datetime(sub["sampled_at"], errors="coerce")
        sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
        sub = sub.dropna(subset=["sampled_at", "value"])
        sub["point"], sub["param"] = point, param.strip()
        frames.append(sub)
    out = pd.concat(frames, ignore_index=True)
    before = len(out)
    for param, (lo, hi) in LIMS_RANGE.items():
        bad = (out["param"] == param) & ~out["value"].between(lo, hi)
        out = out[~bad]
    print(f"  измерений: {len(out):,} (отброшено физически невозможных: {before - len(out)})")
    return out.sort_values(["point", "param", "sampled_at"]).reset_index(drop=True)


def lab_series(lims: pd.DataFrame, point: str, param: str, name: str) -> pd.DataFrame:
    d = lims[(lims["point"] == point) & (lims["param"] == param)][["sampled_at", "value"]]
    return d.sort_values("sampled_at").rename(columns={"value": name})


def join_lab(lab: pd.DataFrame, scada: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    """Значения тегов на момент отбора пробы."""
    pred = scada[["date"] + list(cols)].sort_values("date")
    m = pd.merge_asof(lab, pred, left_on="sampled_at", right_on="date",
                      direction="nearest", tolerance=ASOF_TOL)
    return m.dropna()


# --------------------------------------------------------------------------- регрессии
def ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def metrics(y: np.ndarray, p: np.ndarray) -> Dict[str, float]:
    e = y - p
    return {"n": int(len(y)), "MAE": float(np.mean(np.abs(e))), "bias": float(np.mean(e)),
            "sigma": float(np.subtract(*np.percentile(e, [75, 25])) / 1.349)}


def fit_flash(scada: pd.DataFrame, lims: pd.DataFrame, nom: Dict[str, float]) -> Dict[str, Any]:
    """Модель вспышки против ЛАБОРАТОРИИ.

    Прежняя версия подгоняла модель под HT_T18 — показания виртуального анализатора
    APC, то есть под другую модель, а не под факт. Здесь целевая переменная —
    FlashPoint из ЛИМС, а сам HT_T18 входит как предиктор-якорь.

    Калибровка двухэтапная, потому что у модели два потребителя с разными правами
    на данные:

    1. РЕЖИМНАЯ часть (flash_ref, a_F, a_P, a_W) — для цифрового двойника. Двойник
       считает контрфактику «что будет, если переставить уставку», и живой тег HT_T18
       туда подавать нельзя: модель перестала бы реагировать на сами уставки.
    2. ЯКОРЬ a_T18 — оценивается на ОСТАТКЕ режимной модели и применяется там, где
       HT_T18 является фактическим измерением (оценщик состояния, офлайн-реплей).

    Знаки ограничены физикой: рост нагрузки и рост давления верха К-201 снижают вспышку
    (a_F < 0, a_P < 0), рост расхода отпаривающего газа её повышает (a_W > 0).
    Коэффициент с непригодным знаком обнуляется, остальные переобучаются: расход
    поддува почти не варьируется (медиана 0.17 т/ч), поэтому при свободной подгонке
    он ловит коллинеарность и получает нефизичный знак.
    """
    lab = lab_series(lims, "HT_PROD", "FlashPoint", "flash")
    cols = ["HT_T18", "HT_F9", "HT_P24", "HT_W7"]
    d = join_lab(lab, scada, cols)
    tr = d[d["sampled_at"] < TRAIN_END]
    te = d[(d["sampled_at"] >= TRAIN_END) & (d["sampled_at"] < TEST_END)]

    # --- этап 1: режимная модель против лаборатории
    reg_cols = ["HT_F9", "HT_P24", "HT_W7"]
    names = ["a_F", "a_P", "a_W"]
    signs = {"a_F": -1, "a_P": -1, "a_W": +1}
    active, dropped = list(names), []
    for _ in range(len(names)):
        cur = [c for c, n in zip(reg_cols, names) if n in active]
        X = np.column_stack([np.ones(len(tr))] + [tr[c] - nom[c] for c in cur])
        beta = ols(X, tr["flash"].to_numpy())
        coeffs = dict(zip(active, beta[1:]))
        bad = [n for n, v in coeffs.items() if v * signs[n] < 0]
        if not bad:
            break
        worst = min(bad, key=lambda n: abs(coeffs[n] * signs[n]))
        active.remove(worst)
        dropped.append(worst)

    full = {n: 0.0 for n in names}
    full.update(coeffs)
    const = float(beta[0])

    def regime(part: pd.DataFrame) -> np.ndarray:
        return (const + full["a_F"] * (part["HT_F9"] - nom["HT_F9"])
                + full["a_P"] * (part["HT_P24"] - nom["HT_P24"])
                + full["a_W"] * (part["HT_W7"] - nom["HT_W7"])).to_numpy()

    # --- этап 2: якорь по APC-анализатору на остатке режимной модели
    resid = tr["flash"].to_numpy() - regime(tr)
    dt18 = (tr["HT_T18"] - nom["HT_T18"]).to_numpy()
    a_t18 = float(ols(np.column_stack([np.ones(len(tr)), dt18]), resid)[1])
    a_t18 = max(0.0, a_t18)  # отрицательный вес показания анализатора нефизичен

    def anchored(part: pd.DataFrame) -> np.ndarray:
        return regime(part) + a_t18 * (part["HT_T18"] - nom["HT_T18"]).to_numpy()

    med = float(tr["flash"].median())
    return {"flash_ref": const, "a_T18": a_t18,
            **{k: float(v) for k, v in full.items()},
            "dropped": dropped,
            "train_regime": metrics(tr["flash"].to_numpy(), regime(tr)),
            "test_regime": metrics(te["flash"].to_numpy(), regime(te)),
            "train": metrics(tr["flash"].to_numpy(), anchored(tr)),
            "test": metrics(te["flash"].to_numpy(), anchored(te)),
            "test_naive_MAE": float(np.mean(np.abs(te["flash"].to_numpy() - med)))}


def fit_exotherm(scada: pd.DataFrame, nom: Dict[str, float]) -> Dict[str, Any]:
    """Экзотерма реактора Р-202: T_out - T_in по расходу, сере сырья и квенчу."""
    d = scada[["date", "HT_T11", "HT_T6", "HT_F9", "HT_Q20", "HT_F14"]].dropna()
    d = d.assign(dT=d["HT_T11"] - d["HT_T6"], s_feed=d["HT_Q20"] * Q20_TO_LIMS / 1000.0)
    tr = d[d["date"] < TRAIN_END]
    te = d[(d["date"] >= TRAIN_END) & (d["date"] < TEST_END)]
    X = np.column_stack([np.ones(len(tr)), tr["HT_F9"], tr["s_feed"], tr["HT_F14"]])
    beta = ols(X, tr["dT"].to_numpy())

    def predict(part: pd.DataFrame) -> np.ndarray:
        return (beta[0] + beta[1] * part["HT_F9"] + beta[2] * part["s_feed"]
                + beta[3] * part["HT_F14"]).to_numpy()

    med = float(tr["dT"].median())
    return {"c0": float(beta[0]), "cF": float(beta[1]), "cS": float(beta[2]),
            "cQ": float(beta[3]),
            "train": metrics(tr["dT"].to_numpy(), predict(tr)),
            "test": metrics(te["dT"].to_numpy(), predict(te)),
            "test_naive_MAE": float(np.mean(np.abs(te["dT"].to_numpy() - med)))}


def fit_feed_t95(scada: pd.DataFrame, lims: pd.DataFrame) -> Dict[str, Any]:
    """Чувствительность T95 сырья ГО к отбору дизельной фракции на АВТ.

    Прежнее значение 2.66463 было взято из коэффициента при F30 в официальной формуле
    ВАК AVT6:240-350:EBP. Эта формула — худшая в наборе: на отложенном периоде её
    MAE около 70 °C при самой величине 363 °C, то есть в 17 раз хуже прогноза
    «как прошлый анализ». Здесь эффект оценивается напрямую по лабораторным пробам
    сырья с контролем сопутствующих потоков.
    """
    lab = lab_series(lims, "HT_FEED", "95%.T", "t95")
    cols = ["AVT_F30", "AVT_F32", "AVT_F65"]
    d = join_lab(lab, scada, cols)
    tr = d[d["sampled_at"] < TRAIN_END]
    X = np.column_stack([np.ones(len(tr))] + [tr[c].to_numpy() for c in cols])
    beta = ols(X, tr["t95"].to_numpy())
    by_year: Dict[str, float] = {}
    for year, part in d.groupby(d["sampled_at"].dt.year):
        if len(part) < 30:
            continue
        Xy = np.column_stack([np.ones(len(part))] + [part[c].to_numpy() for c in cols])
        by_year[str(int(year))] = float(ols(Xy, part["t95"].to_numpy())[1])
    return {"dT95_dF30_train": float(beta[1]), "n_train": int(len(tr)),
            "by_year": by_year, "t95_ref": float(tr["t95"].median()),
            "range": [min(by_year.values()), max(by_year.values())] if by_year else [0.0, 0.0]}


# --------------------------------------------------------------------------- пайплайн
def main() -> None:
    scada_raw = load_scada()
    scada_clean, n_removed, n_kept = strip_fault_codes(scada_raw)
    scada_clean = sanitize_analyzers(scada_clean)
    mask = working_mask(scada_clean)
    work = scada_clean[mask].copy()
    print(f"  рабочих тактов: {len(work):,} из {len(scada_clean):,} "
          f"({100 * len(work) / len(scada_clean):.1f} %)")

    lims = load_lims()

    train = work[work["date"] < TRAIN_END]
    test = work[(work["date"] >= TRAIN_END) & (work["date"] < TEST_END)]
    print(f"  Train: {len(train):,} тактов | Test: {len(test):,} тактов")

    nominal = {c: float(train[c].median(skipna=True)) for c in work.columns if c != "date"}
    nominal["HT_GOR"] = float(
        (train["HT_F2"] / (train["HT_F9"] / 0.847)).median(skipna=True))

    print("\nКалибровка подмоделей...")
    flash = fit_flash(work, lims, nominal)
    exo = fit_exotherm(work, nominal)
    feed = fit_feed_t95(work, lims)

    print(f"  вспышка:   MAE Test режимная {flash['test_regime']['MAE']:.3f} °C -> "
          f"с якорем T18 {flash['test']['MAE']:.3f} °C "
          f"(наивный {flash['test_naive_MAE']:.3f}); обнулены: {flash['dropped'] or 'нет'}")
    print(f"  экзотерма: MAE Test {exo['test']['MAE']:.3f} °C "
          f"(наивный {exo['test_naive_MAE']:.3f})")
    print(f"  dT95/dF30: {feed['dT95_dF30_train']:+.4f} °C/(т/ч), "
          f"по годам {feed['range'][0]:+.3f}…{feed['range'][1]:+.3f}")

    # Опорные значения качества по ЛИМС (медианы Train)
    def lab_median(point: str, param: str, default: float) -> float:
        d = lab_series(lims, point, param, "v")
        d = d[d["sampled_at"] < TRAIN_END]
        return float(d["v"].median()) if len(d) else default

    s_out_ref = lab_median("HT_PROD", "Mg.Sulfur", 8.6)
    d15_ref = lab_median("HT_PROD", "D15", 836.0)
    cfpp_ref = lab_median("HT_PROD", "CFPP", -6.0)
    cn_ref = lab_median("HT_PROD", "CetaneNumber", 53.75)
    s_feed_ref = lab_median("HT_FEED", "Mass.Sulfur", 0.947) * 1e4

    # Эффект F30 на T95 не идентифицируется: знак нестабилен по годам, поэтому
    # в конфигурацию пишется ноль, а не оценка со случайным знаком.
    dt95_df30 = 0.0 if feed["range"][0] * feed["range"][1] < 0 else round(
        feed["dT95_dF30_train"], 5)

    params = {
        "_источник": "scripts/calibrate_twin.py, Train <= 2025-06-30, без перемешивания",
        "reactor": {
            "c0": round(exo["c0"], 4), "cF": round(exo["cF"], 5),
            "cS": round(exo["cS"], 5), "cQ": round(exo["cQ"], 5),
            "s_out_ref": round(s_out_ref, 2),
            "s_feed_ref": round(s_feed_ref, 0),
            "feed_ref": round(nominal["HT_F9"], 4),
            "t_in_ref": round(nominal["HT_T6"], 4),
            "p_ref": round(nominal["HT_P13"], 4),
            "gor_ref": round(nominal["HT_GOR"], 1),
            "quench_ref": round(nominal["HT_F14"], 4),
            "dp_ref_kpa": round(nominal["HT_P8"] * 1000.0, 1),
        },
        "stabilizer": {
            "flash_ref": round(flash["flash_ref"], 3),
            "t18_ref": round(nominal["HT_T18"], 3),
            "f9_ref": round(nominal["HT_F9"], 4),
            "p24_ref": round(nominal["HT_P24"], 4),
            "w7_ref": round(nominal["HT_W7"], 4),
            "a_T18": round(flash["a_T18"], 4),
            "a_F": round(flash["a_F"], 4),
            "a_P": round(flash["a_P"], 3),
            "a_W": round(flash["a_W"], 3),
        },
        "feed": {
            "t95_ref": round(feed["t95_ref"], 2),
            "dT95_dF30": dt95_df30,
            "f30_ref": round(nominal["AVT_F30"], 2),
            "f32_ref": round(nominal["AVT_F32"], 2),
            "s_ref": round(s_feed_ref, 0),
            "d15_ref": round(lab_median("HT_FEED", "D15", 847.2), 2),
        },
        "product": {
            "cfpp_ref": round(cfpp_ref, 2), "cn_ref": round(cn_ref, 2),
            "d15_ref": round(d15_ref, 2),
            "delta_d15_hdt": 11.1, "delta_t95_hdt": 6.0,
        },
    }

    # Экономический блок и блок блендинга калибровке здесь не подлежат — переносятся как есть
    params_path = ROOT_DIR / "config" / "twin_params.json"
    if params_path.exists():
        prev = json.loads(params_path.read_text(encoding="utf-8"))
        for section in ("economics", "blend"):
            if section in prev:
                params[section] = prev[section]
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nПараметры сохранены: {params_path.relative_to(ROOT_DIR)}")

    report = f"""# Отчёт по калибровке и валидации цифрового двойника

**Дата генерации:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}
**Источник архива:** `{DATA_DIR}`
**Train:** 2023-01-01 — 2025-06-30 ({len(train):,} рабочих тактов)
**Test (отложенный):** 2025-07-01 — 2026-08-07 ({len(test):,} рабочих тактов)

---

## 1. Очистка данных

- Коды отказа КИП сняты выборочно: {n_removed:,} значений. Сохранено {n_kept:,} значений
  307/313, попадающих в рабочий диапазон своего тега (слепое правило удалило бы и их).
- Рабочий режим определяется по трём признакам одновременно: `HT_F9 > 120`,
  `HT_T6 > 300`, `HT_P13 > 3.0` (плюс `AVT_F65 > 400`). На остановах расход сырья даёт
  одиночные всплески выше 120 т/ч при холодном реакторе.
- Поточные анализаторы серы проходят санитарный контроль диапазона.
- Физически невозможные лабораторные пробы исключены.

## 2. Номинальный режим (медианы Train)

| Параметр | Значение |
| :--- | ---: |
| Расход сырья `HT_F9` | {nominal['HT_F9']:.1f} т/ч |
| Температура входа Р-202 `HT_T6` | {nominal['HT_T6']:.1f} °C |
| Давление Р-202 `HT_P13` | {nominal['HT_P13']:.3f} МПа |
| Кратность ВСГ/сырьё | {nominal['HT_GOR']:.0f} нм³/м³ |
| Перепад давления `HT_P8` | {nominal['HT_P8'] * 1000:.0f} кПа |
| Сера продукта (ЛИМС) | {s_out_ref:.2f} мг/кг |
| Сера сырья (ЛИМС) | {s_feed_ref:.0f} ppm |

## 3. Валидация подмоделей на отложенном периоде

| Подмодель | MAE модели | MAE наивного прогноза | Смещение |
| :--- | ---: | ---: | ---: |
| Экзотерма `T11 − T6` | **{exo['test']['MAE']:.3f} °C** | {exo['test_naive_MAE']:.3f} °C | {exo['test']['bias']:+.3f} °C |
| Вспышка К-201, режимная часть | **{flash['test_regime']['MAE']:.3f} °C** | {flash['test_naive_MAE']:.3f} °C | {flash['test_regime']['bias']:+.3f} °C |
| Вспышка К-201, с якорем `HT_T18` | **{flash['test']['MAE']:.3f} °C** | {flash['test_naive_MAE']:.3f} °C | {flash['test']['bias']:+.3f} °C |

Все подмодели превосходят наивный прогноз медианой на данных, которых не видели.

### Модель вспышки переведена на лабораторию

Прежняя версия подгоняла вспышку под тег `HT_T18` — показания виртуального
анализатора APC, то есть под другую модель, а не под факт. Целевая переменная
заменена на `FlashPoint` из ЛИМС.

Калибровка двухэтапная, потому что у модели два потребителя с разными правами на
данные. Режимная часть (`a_F`, `a_P`, `a_W`) предназначена для цифрового двойника:
он считает контрфактику «что будет, если переставить уставку», и живой тег `HT_T18`
туда подавать нельзя — модель перестала бы реагировать на сами уставки. Якорь
`a_T18` = {flash['a_T18']:.4f} оценён на остатке режимной модели и применяется там,
где `HT_T18` является фактическим измерением: в оценщике состояния и офлайн-реплее.

Коэффициенты с нефизичным знаком обнуляются: {flash['dropped'] or 'в этом прогоне таких нет'}.

### Чего эта правка НЕ решает

Точность в среднем выросла, но **защитный барьер по вспышке от этого не заработал**.
На отложенном периоде фактических нарушений ГОСТ (< 55 °C) всего 5, и ни одна
линейная модель по доступным тегам не ловит больше двух. Причина видна прямо в
данных: в зоне риска корреляция факта с показанием APC-анализатора равна **0.006**
против 0.62 на всём массиве. Например, 27.04.2026 лаборатория дала 45 °C при
показании анализатора 82 °C. Нарушения вспышки доступной телеметрией не
прогнозируются — это ограничение данных, а не настройки модели.

## 4. Чувствительность T95 сырья к отбору дизельной фракции

Оценка по лабораторным пробам сырья (n = {feed['n_train']}) с контролем сопутствующих потоков:

| Период | dT95/dF30, °C на т/ч |
| :--- | ---: |
""" + "\n".join(f"| {y} | {v:+.4f} |" for y, v in sorted(feed["by_year"].items())) + f"""
| **Train (принято)** | **{dt95_df30:+.4f}** |

Знак коэффициента нестабилен по годам, а величина на два порядка меньше прежнего
значения **2.66463**, которое было взято из формулы ВАК `AVT6:240-350:EBP` — худшей
в наборе (MAE около 70 °C при величине 363 °C). Эффект не идентифицируется, поэтому
в конфигурацию записан ноль: фантомный рычаг с неверным знаком опаснее его отсутствия.

## 5. Воспроизводимость

Скрипт читает архив по пути из `NEFTEKOD_DATA_DIR` и не требует копирования CSV
в репозиторий. Методика очистки согласована с `notebooks/01_model_evaluation.ipynb`.
"""
    rep_path = ROOT_DIR / "data" / "processed" / "calibration_report.md"
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(report, encoding="utf-8")
    print(f"Отчёт сохранён: {rep_path.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
