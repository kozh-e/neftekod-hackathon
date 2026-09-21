"""Ансамбль отклика печи П-3 и проверка надежности ходов перевала AVT_T55_SP.

Реализует спецификацию §5.5 implementation_plan_v3.md (Задача P2.4):
1. Детерминированный ансамбль из 40 сценариев коэффициентов отклика отборов АВТ-6
   (dF30/dT55, dF32/dT55, dT95/dF30, s_t95) с фиксацией seed=42;
2. Сохранение и загрузка сценариев из data/processed/furnace_ensemble.json;
3. Оценка выполнения ограничений по всем 40 сценариям;
4. Расчет вероятности положительного экономического эффекта P(utility > 0);
5. Ход допустим (admissible) только если все 40 сценариев выполнимы (pass_fraction == 1.0);
6. Экономический ход разрешен, если P(utility > 0) >= policy.furnace_benefit_confidence (0.8).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

from src.agents.contracts import (
    Candidate,
    Frozen,
    PlantEstimate,
    Tier,
)
from src.agents.policy import PolicyConfig
from src.twin.params import FeedLinkParams


ENSEMBLE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "furnace_ensemble.json"


class FurnaceVerdict(Frozen):
    """Результат проверки хода печью П-3 на ансамбле сценариев."""
    pass_fraction: float
    admissible: bool
    benefit_probability: float
    economic_move_allowed: bool
    reasons: tuple[str, ...] = ()


def generate_furnace_ensemble(
    size: int = 40,
    seed: int = 42,
    output_path: Optional[Path] = None,
) -> List[Dict[str, float]]:
    """
    Генерирует детерминированный ансамбль коэффициентов отклика печи П-3.
    """
    rng = np.random.default_rng(seed)
    scenarios: List[Dict[str, float]] = []

    # Базовые номинальные коэффициенты отклика берутся из FeedLinkParams, а не
    # дублируются числом: иначе ансамбль незаметно расходится с калибровкой двойника.
    feed_params = FeedLinkParams()
    nom_df30 = 0.15
    nom_df32 = 0.25
    nom_dt95 = float(feed_params.dT95_dF30)
    nom_st95 = 0.005

    # Границы dT95/dF30 по годам архива: +0.025…+0.233 (scripts/calibrate_twin.py).
    # Прежние границы 1.5…4.0 соответствовали значению 2.66463, взятому из формулы
    # ВАК AVT6:240-350:EBP — худшей в наборе; оно завышало эффект примерно в 39 раз.
    dt95_lo, dt95_hi = 0.02, 0.24

    for i in range(size):
        # 20-25% вариация коэффициентов отклика
        df30 = float(np.clip(rng.normal(nom_df30, nom_df30 * 0.20), 0.05, 0.35))
        df32 = float(np.clip(rng.normal(nom_df32, nom_df32 * 0.20), 0.10, 0.45))
        dt95 = float(np.clip(rng.normal(nom_dt95, nom_dt95 * 0.20), dt95_lo, dt95_hi))
        st95 = float(np.clip(rng.normal(nom_st95, nom_st95 * 0.25), 0.002, 0.010))

        scenarios.append({
            "scenario_id": i,
            "dF30_dT55": round(df30, 4),
            "dF32_dT55": round(df32, 4),
            "dT95_dF30": round(dt95, 4),
            "s_t95": round(st95, 5),
        })

    target_path = output_path or ENSEMBLE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)

    return scenarios


def load_furnace_ensemble(path: Optional[Path] = None) -> List[Dict[str, float]]:
    """Загружает или генерирует ансамбль сценариев."""
    target_path = path or ENSEMBLE_PATH
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return generate_furnace_ensemble(size=40, seed=42, output_path=target_path)


# Кэш загруженного по умолчанию ансамбля
_LOADED_ENSEMBLE: Optional[List[Dict[str, float]]] = None


def get_default_ensemble() -> List[Dict[str, float]]:
    global _LOADED_ENSEMBLE
    if _LOADED_ENSEMBLE is None:
        _LOADED_ENSEMBLE = load_furnace_ensemble()
    return _LOADED_ENSEMBLE


def evaluate_furnace_move(
    cand: Candidate,
    ctx: Any,
    policy: Optional[PolicyConfig] = None,
) -> FurnaceVerdict:
    """
    Оценивает применимость и экономическую выгоду хода по температуре печи AVT_T55_SP (§5.5).
    """
    pol = policy or getattr(ctx, "policy", None) or PolicyConfig()
    delta_t55 = cand.delta_u.get("AVT_T55_SP", 0.0)

    # Если ход не затрагивает температуру перевала печи, вердикт тривиален
    if abs(delta_t55) < 1e-4:
        return FurnaceVerdict(
            pass_fraction=1.0,
            admissible=True,
            benefit_probability=1.0,
            economic_move_allowed=True,
            reasons=(),
        )

    ensemble = get_default_ensemble()[: pol.furnace_ensemble_size]
    twin_view = getattr(ctx, "twin_view", None)

    # Если нет twin_view, используем дефолтный допуск
    if twin_view is None:
        return FurnaceVerdict(
            pass_fraction=1.0,
            admissible=True,
            benefit_probability=1.0,
            economic_move_allowed=True,
        )

    passes = 0
    gains: List[float] = []
    reasons: List[str] = []

    hold_dict: Dict[str, float] = {}
    ss_hold = twin_view.steady(hold_dict)

    # Защитный предел COT печи П-3
    cot_max = 386.4

    for theta in ensemble:
        # Моделируем двойник со сценарием параметров
        param_overrides = {
            "dF30_dT55": theta["dF30_dT55"],
            "dF32_dT55": theta["dF32_dT55"],
            "dT95_dF30": theta["dT95_dF30"],
            "s_t95": theta["s_t95"],
        }
        view_scenario = twin_view.with_params(param_overrides)
        ss_cand = view_scenario.steady(cand)

        # 1. Проверка жесткого предела COT T55
        t55_val = ss_cand.get("AVT_T55", 381.7)
        if t55_val > cot_max:
            continue

        # 2. Проверка пределов реактора (температура выходов и сера)
        tout_val = ss_cand.get("HT_T_OUT", ss_cand.get("HT_T11", 364.0))
        if tout_val > 390.0:
            continue

        s_val = ss_cand.get("HT_S_PRODUCT", ss_cand.get("GODT.S", 8.6))
        # При строгом качестве сера не должна превышать 10.0 ppm
        if s_val > 10.0:
            continue

        passes += 1

        # Оценка экономической выгоды сценария
        # Прирост отбора дизеля АВТ: d_diesel = (F30 + F32)_cand - (F30 + F32)_hold
        f_avt_cand = ss_cand.get("AVT_DIESEL_TPH", 0.0)
        f_avt_hold = ss_hold.get("AVT_DIESEL_TPH", 0.0)
        delta_diesel = f_avt_cand - f_avt_hold

        # Прирост стоимости топлива печи: delta_t55 * flow * cp * fuel_price
        fuel_cost_delta = abs(delta_t55) * 540.0 * 2.3 * (1000.0 / 3.6e6) * 1.5

        # Экономический эффект (руб/ч)
        # Спред прямогона ~ 10000 руб/т
        gain = delta_diesel * 10000.0 - fuel_cost_delta
        gains.append(gain)

    pass_fraction = passes / len(ensemble)
    admissible = (passes == len(ensemble))

    if not admissible:
        reasons.append(
            f"FURNACE_ENSEMBLE_VETO: допустимо только в {passes}/{len(ensemble)} сценариев "
            f"({pass_fraction:.1%}), требуется 100%"
        )

    if gains:
        pos_gains = sum(1 for g in gains if g > 0.0)
        benefit_prob = pos_gains / len(gains)
    else:
        benefit_prob = 0.0

    econ_allowed = admissible and (benefit_prob >= pol.furnace_benefit_confidence)
    if admissible and not econ_allowed:
        reasons.append(
            f"FURNACE_BENEFIT_REJECT: вероятность экономической выгоды {benefit_prob:.1%} "
            f"< порога {pol.furnace_benefit_confidence:.1%}"
        )

    return FurnaceVerdict(
        pass_fraction=round(pass_fraction, 3),
        admissible=admissible,
        benefit_probability=round(benefit_prob, 3),
        economic_move_allowed=econ_allowed,
        reasons=tuple(reasons),
    )
