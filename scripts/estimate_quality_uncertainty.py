"""Оценка неопределенности прогнозов качества и отклика печи АВТ по архивам (методика ТЗ).

Правила обработки (artifacts/tz_neftecode_full.md):
- ЛИМС — безусловная истина (§3.3, п. 1); синхронизация только по времени `date` (§3.3, п. 2);
- без заглядывания в будущее (Критерий 1): прогноз в момент отбора использует только прошлые данные,
  результат ЛИМС доступен через AVAIL_LAG_H после отбора (Callout 1: 2–8 ч, берется верхняя граница);
- клампинг 307/313 -> NaN (Callout 2); рабочий режим HT_F9 > 120 т/ч;
- bias update по последним доступным пробам ЛИМС (Callout 1): медиана 5 последних невязок;
- обучение <= 2025-06-30, отложенная проверка >= 2025-07-01.

Результат: робастные σ (IQR/1.349) для серы (HT_Q21), T95 (официальная ВАК 24-2000:GODT:T95),
вспышки (HT_T18), фактический риск у границы правила «прогноз + 2σ», отклик отборов дизеля
AVT_F30/AVT_F32 на температуру печи AVT_T55 и граница переочистки по минимаксу сожаления.

Запуск: python scripts/estimate_quality_uncertainty.py --data-dir "C:/хакатон данные"
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

SPLIT = pd.Timestamp("2025-07-01")
AVAIL_LAG_H = 8.0
BIAS_WINDOW = 5
Z_VETO = 2.0  # tz:598, «прогноз + 2·sigma_q»
T_SIGMA_H = 12.0  # закон роста σ(age) = σ0·sqrt(1 + age/12), src/agents/constraints.py
CLAMP = (307.0, 313.0)

# Колонки листа ЛИМС: установка «Гидроочистка», точка отбора 2 (дизельное топливо)
LIMS_COLS = {"S": (94, 95), "T95": (92, 93), "FLASH": (86, 87)}


def robust_sigma(x: pd.Series) -> float:
    return float((x.quantile(0.75) - x.quantile(0.25)) / 1.349)


def load_lims(path: Path, key: str) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None)
    d_col, v_col = LIMS_COLS[key]
    df = raw.iloc[4:, [d_col, v_col]].dropna()
    df.columns = ["t", "lab"]
    df["t"] = pd.to_datetime(df["t"], errors="coerce")
    df["lab"] = pd.to_numeric(df["lab"], errors="coerce")
    return df.dropna().sort_values("t").reset_index(drop=True)


def load_ht(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["date", "F2", "T6", "F9", "T18", "Q21"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("F2", "T6", "F9", "T18", "Q21"):
        df.loc[df[c].isin(CLAMP), c] = np.nan
    df.loc[(df.Q21 < 0) | (df.Q21 > 20), "Q21"] = np.nan  # шкала анализатора серы 0–20 ppm (tz:354)
    return df[df.F9 > 120].sort_values("date").reset_index(drop=True)


def load_avt(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["date", "T55", "F30", "F32", "F31", "F65"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("T55", "F30", "F32", "F31", "F65"):
        df.loc[df[c].isin(CLAMP), c] = np.nan
    return df[df.F65 > 400].dropna().sort_values("date").reset_index(drop=True)


def predict_with_bias(lab: pd.DataFrame, base: pd.Series) -> pd.DataFrame:
    """Ошибка прогноза «модель + медиана невязок последних доступных проб ЛИМС» и возраст использованных проб."""
    d = lab.assign(base=base.values).dropna(subset=["base"]).reset_index(drop=True)
    d["e_raw"] = d.lab - d.base
    rows = []
    for _, r in d.iterrows():
        avail = d[(d.t + pd.Timedelta(hours=AVAIL_LAG_H)) <= r.t]
        if len(avail) < BIAS_WINDOW:
            continue
        bias = float(avail.e_raw.iloc[-BIAS_WINDOW:].median())
        age_h = (r.t - avail.t.iloc[-1]).total_seconds() / 3600.0
        rows.append((r.t, r.lab, r.base + bias, r.e_raw - bias, age_h))
    return pd.DataFrame(rows, columns=["t", "lab", "pred", "err", "age_h"])


def sigma_block(pe: pd.DataFrame, limit: float, sense: str, band: float) -> Dict[str, float]:
    tr, te = pe[pe.t < SPLIT], pe[pe.t >= SPLIT]
    s_tr = robust_sigma(tr.err)
    sign = 1.0 if sense == "max" else -1.0
    ucb = pe.pred + sign * Z_VETO * s_tr
    near = pe[(sign * (limit - ucb) >= 0) & (sign * (limit - ucb) < band)]
    exceed = (near.lab > limit) if sense == "max" else (near.lab < limit)
    return {
        "sigma_train": round(s_tr, 3),
        "sigma_test": round(robust_sigma(te.err), 3),
        "n_train": int(len(tr)),
        "n_test": int(len(te)),
        "median_age_h": round(float(pe.age_h.median()), 1),
        "boundary_band_n": int(len(near)),
        "boundary_realized_violation_pct": round(float(exceed.mean() * 100.0), 2) if len(near) else float("nan"),
    }


def _slope_t55(h: pd.DataFrame, y: str) -> Tuple[float, float]:
    """МНК: отбор фракции ~ T55 + F65 + F31 (поправка на загрузку К-2 и расход через печь). Возвращает (наклон, СКО)."""
    x = np.column_stack([np.ones(len(h)), h.T55, h.F65, h.F31])
    beta, *_ = np.linalg.lstsq(x, h[y].values, rcond=None)
    resid = h[y].values - x @ beta
    cov = np.linalg.inv(x.T @ x) * (resid @ resid) / (len(h) - x.shape[1])
    return float(beta[1]), float(math.sqrt(cov[1, 1]))


def furnace_response(avt: pd.DataFrame) -> Dict[str, object]:
    """
    Отклик отборов дизеля AVT_F30/AVT_F32 (т/ч, реестр) на температуру печи AVT_T55 по часовым средним.
    Наблюдательные данные замкнутого контура: связь неустойчива между подпериодами. По решению команды
    принимается оценка по всему архиву (ASSUMPTION), диапазон по подпериодам публикуется для XAI.
    """
    h = avt.set_index("date").resample("1h").mean().dropna()
    out: Dict[str, object] = {"n_hours": int(len(h)), "f31_median_tph": round(float(h.F31.median()), 1)}
    for y in ("F30", "F32"):
        slope, se = _slope_t55(h, y)
        out[f"d{y}_dT55_tph_per_C"] = round(slope, 4)
        out[f"d{y}_dT55_se"] = round(se, 4)
        subperiods = {
            "train": h[h.index < SPLIT],
            "test": h[h.index >= SPLIT],
            **{str(yr): part for yr, part in h.groupby(h.index.year)},
        }
        slopes = {name: round(_slope_t55(part, y)[0], 3) for name, part in subperiods.items()}
        out[f"d{y}_dT55_by_subperiod"] = slopes
        out[f"d{y}_dT55_range"] = [min(slopes.values()), max(slopes.values())]
    return out


def minimax_giveaway_z(sigma0: float) -> Dict[str, float]:
    """
    Граница переочистки: z_min, минимизирующий худшее сожаление по цене брака L и состояниям анализатора.
    k — самый дешевый способ снизить серу в двойнике проекта; L ∈ [OPEX/т, спред ГО ДТ − прямогон].
    """
    import copy

    from src.agents.optimization import RolloutOptimizationAgent
    from src.twin.chain import FullChainTwin
    from src.twin.params import load_params
    from src.twin.tags import NOMINAL_OPERATING_POINT

    params = load_params(ROOT_DIR / "config" / "twin_params.json")
    twin = FullChainTwin(copy.deepcopy(params))
    twin.initialize(NOMINAL_OPERATING_POINT)
    cands, _ = RolloutOptimizationAgent(params=params).propose(twin)
    hold = next(c for c in cands if c.is_hold)
    costs = []
    for c in cands:
        ds = c.steady_state["HT_S_PRODUCT"] - hold.steady_state["HT_S_PRODUCT"]
        if ds < -1e-3 and abs(c.margin_breakdown.get("throughput", 0.0)) < 1e-6:
            costs.append(c.expected_margin / ds)
    k = min(costs)
    e = params.economics
    g = params.reactor.feed_ref * e.y_liq
    l_grid = np.geomspace(400000.0 / params.reactor.feed_ref, e.straight_to_godt_spread, 200)
    states = {"analyzer_ok": 0.0, "fail_2h": 2.0, "fail_8h": 8.0}

    def cost(s: float, loss: float, sig: float) -> float:
        return -k * s + loss * g * norm.sf(10.0, s, sig)

    def s_opt(loss: float, sig: float) -> float:
        return 10.0 - math.sqrt(-2.0 * math.log(math.sqrt(2.0 * math.pi) * k * sig / (loss * g))) * sig

    z_grid = np.linspace(Z_VETO + 0.05, 4.5, 400)
    worst = np.zeros_like(z_grid)
    for age in states.values():
        sig = sigma0 * math.sqrt(1.0 + age / T_SIGMA_H)
        reg = np.array([max(cost(10 - z * sig, loss, sig) - cost(s_opt(loss, sig), loss, sig) for loss in l_grid) for z in z_grid])
        worst = np.maximum(worst, reg)
    j = int(np.argmin(worst))
    return {
        "k_rub_h_per_ppm": round(float(k), 1),
        "loss_range_rub_t": [round(float(l_grid[0]), 0), round(float(l_grid[-1]), 0)],
        "z_min": round(float(z_grid[j]), 2),
        "alpha_min_pct": round(float(norm.sf(z_grid[j]) * 100.0), 3),
        "max_regret_rub_h": round(float(worst[j]), 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=str(ROOT_DIR / "initial_data"))
    args = ap.parse_args()
    data_dir = Path(args.data_dir)

    lims_path = data_dir / "ЛИМСы 01.01.2023 - н.в_ (2).xlsx"
    ht = load_ht(data_dir / "242000_tags.csv")
    avt = load_avt(data_dir / "avt_tags.csv")

    def at_sample(lab: pd.DataFrame, cols: list) -> pd.DataFrame:
        return pd.merge_asof(lab, ht[["date"] + cols].dropna(), left_on="t", right_on="date",
                             direction="backward", tolerance=pd.Timedelta("10min"))

    # 1. Сера: анализатор HT_Q21 + bias; значения ЛИМС вне диапазона метода 0.1–20 мг/кг исключены (tz:231)
    lab_s = load_lims(lims_path, "S")
    lab_s = lab_s[(lab_s.lab >= 0.1) & (lab_s.lab <= 20.0)].reset_index(drop=True)
    pe_s = predict_with_bias(lab_s, at_sample(lab_s, ["Q21"])["Q21"])
    sulfur = sigma_block(pe_s, 10.0, "max", 0.5)

    # 2. T95: официальная ВАК 24-2000:GODT:T95 (tz:329), авторегрессия на последней ДОСТУПНОЙ пробе ЛИМС
    lab_t = load_lims(lims_path, "T95")
    m = at_sample(lab_t, ["F9", "F2", "T6"])
    last_avail = [
        (lab_t[(lab_t.t + pd.Timedelta(hours=AVAIL_LAG_H)) <= t].lab.iloc[-1]
         if ((lab_t.t + pd.Timedelta(hours=AVAIL_LAG_H)) <= t).any() else np.nan)
        for t in m.t
    ]
    vak_t95 = 0.03814 * m.F9 - 9.201 - 0.00002 * m.F2 + 0.50 * m.T6 + 0.48321 * pd.Series(last_avail)
    pe_t = predict_with_bias(lab_t, vak_t95)
    t95 = sigma_block(pe_t, 360.0, "max", 5.0)
    t95["sigma0_backed_out"] = round(t95["sigma_train"] / math.sqrt(1.0 + t95["median_age_h"] / T_SIGMA_H), 3)

    # 3. Вспышка: ВАК HT_T18 + bias
    lab_f = load_lims(lims_path, "FLASH")
    pe_f = predict_with_bias(lab_f, at_sample(lab_f, ["T18"])["T18"])
    flash = sigma_block(pe_f, 55.0, "min", 5.0)

    furnace = furnace_response(avt)
    giveaway = minimax_giveaway_z(sulfur["sigma_train"])

    result = {"sulfur": sulfur, "t95": t95, "flash": flash, "furnace": furnace, "giveaway": giveaway}
    out_json = ROOT_DIR / "data" / "processed" / "quality_uncertainty.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
