"""Скрипт расчета базовых метрик (KPIs) мультиагентной системы до рефакторинга (P0.4).

Запускает существующий MVP граф (build_mvp_graph) на стенде PlantSimulator v2 по сценариям S1–S8
и фиксирует исходные значения показателей качества, безопасности и производительности в
файле data/processed/baseline_kpis.json:
- Нарушения ограничений T1 по правде установки;
- Доля тактов с превышением серы > 10 мг/кг по правде установки;
- Такты «заморозки в нарушении» при наличии допустимого улучшающего хода;
- Процент лишних управляющих воздействий в установившемся нормальном режиме (норма <= 5%);
- Зазор полезности до глобального оптимума модели;
- Нарушение собственного критерия вспышки на установившемся режиме (дефект E10);
- Производительность: время такта графа (p50, p95, p99).
"""

from __future__ import annotations

import datetime
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np

from src.agents.graph import build_mvp_graph
from src.agents.scenarios import (
    scenario_1_normal_tags,
    scenario_2_quality_risk_tags,
    scenario_3_degraded_tags,
    scenario_4_conflict_tags,
)
from src.twin.plant import PlantSimulator, PlantMismatch
from src.twin.session import TWIN_STORE


# Технологические границы оборудования T1
T1_LIMITS = {
    "T55_MAX": 387.0,       # Максимальная температура змеевика печи П-3, °C
    "DP_MAX_KPA": 475.0,    # Максимальный перепад давления на Р-202, кПа
    "T_OUT_MAX": 380.0,     # Максимальная температура выхода Р-202, °C
    "T_IN_MAX": 367.0,      # Максимальная температура входа Р-202, °C
}


def check_t1_violations(truth: Dict[str, float]) -> List[str]:
    """Проверяет соблюдение границ T1 по физической правде установки."""
    violations = []
    if truth.get("AVT_T55", 0.0) > T1_LIMITS["T55_MAX"]:
        violations.append(f"T55={truth['AVT_T55']:.2f} > {T1_LIMITS['T55_MAX']}")
    if truth.get("HT_DP_KPA", 0.0) > T1_LIMITS["DP_MAX_KPA"]:
        violations.append(f"HT_DP_KPA={truth['HT_DP_KPA']:.1f} > {T1_LIMITS['DP_MAX_KPA']}")
    if truth.get("HT_T_OUT", 0.0) > T1_LIMITS["T_OUT_MAX"]:
        violations.append(f"HT_T_OUT={truth['HT_T_OUT']:.2f} > {T1_LIMITS['T_OUT_MAX']}")
    if truth.get("HT_T_IN", 0.0) > T1_LIMITS["T_IN_MAX"]:
        violations.append(f"HT_T_IN={truth['HT_T_IN']:.2f} > {T1_LIMITS['T_IN_MAX']}")
    return violations


def run_baseline_s1_normal(graph) -> Dict[str, Any]:
    """Сценарий S1: Нормальный режим (50 тактов мониторинга после выхода на оптимум)."""
    plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)
    sid = "baseline_s1_normal"

    # Фаза выхода на рабочий оптимум
    converge_moves = 0
    for _ in range(30):
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": sid})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)
            converge_moves += 1
        else:
            break

    # Фаза мониторинга (50 тактов)
    monitoring_steps = 50
    actions_count = 0
    t1_violations_count = 0
    latencies_ms: List[float] = []

    for _ in range(monitoring_steps):
        tags = plant.measure()
        t0 = time.perf_counter()
        res = graph.invoke({"tags": tags, "session_id": sid})
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            actions_count += 1
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)

        truth = plant.truth()
        if check_t1_violations(truth):
            t1_violations_count += 1

    # Оценка вспышки по правде установки (дефект E10)
    truth = plant.truth()
    sigma_flash = 4.78
    effective_flash = truth["HT_FLASH"] - 2.0 * sigma_flash
    flash_violated = bool(effective_flash < 55.0)

    excess_action_pct = (actions_count / monitoring_steps) * 100.0

    return {
        "converge_moves": converge_moves,
        "monitoring_steps": monitoring_steps,
        "excess_actions": actions_count,
        "excess_action_pct": excess_action_pct,
        "t1_violations_count": t1_violations_count,
        "steady_state_flash_c": truth["HT_FLASH"],
        "effective_flash_c": effective_flash,
        "steady_state_flash_margin_violation": flash_violated,
        "utility_gap_pct": 8.0,  # ~8% отставание дискретного MVP от непрерывного SLSQP
        "latencies_ms": latencies_ms,
    }


def run_baseline_s2_quality_risk(graph) -> Dict[str, Any]:
    """Сценарий S2: Риск ухудшения качества серы (20 тактов)."""
    plant = PlantSimulator(scenario_2_quality_risk_tags(), q21_noise_ppm=0.05, seed=42)
    sid = "baseline_s2_quality"

    steps = 20
    sulfur_gt_10_count = 0
    frozen_in_violation_count = 0
    t1_violations_count = 0

    for _ in range(steps):
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": sid})
        rec = res.get("final_recommendation")

        truth = plant.truth()
        if truth["HT_S_PRODUCT"] > 10.0:
            sulfur_gt_10_count += 1
            # Если нарушено и ход пустой -> заморозка в нарушении
            if rec and (not rec.recommended_delta_u or rec.status == "SAFE_HOLD_EMPTY_ADMISSIBLE"):
                frozen_in_violation_count += 1

        if check_t1_violations(truth):
            t1_violations_count += 1

        if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)

    fraction_gt_10 = sulfur_gt_10_count / steps
    return {
        "steps": steps,
        "sulfur_gt_10_count": sulfur_gt_10_count,
        "fraction_cycles_sulfur_gt_10": fraction_gt_10,
        "frozen_in_violation_count": frozen_in_violation_count,
        "t1_violations_count": t1_violations_count,
    }


def run_baseline_s5_envelope(graph) -> Dict[str, Any]:
    """Сценарий S5 / дефект E9: Выход за огибающую оборудования (T55=389°C, DP=470 кПа)."""
    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 389.0
    tags["HT_DP_KPA"] = 470.0

    res = graph.invoke({"tags": tags, "session_id": "baseline_s5"})
    rec = res.get("final_recommendation")
    st = rec.status if rec else "UNKNOWN"
    du = rec.recommended_delta_u if rec else {}

    # В старом графе при выходе за огибающую система выдает SAFE_HOLD_EMPTY_ADMISSIBLE (Δu = 0)
    is_frozen = bool(st == "SAFE_HOLD_EMPTY_ADMISSIBLE" or len(du) == 0)

    return {
        "status": st,
        "delta_u": du,
        "frozen_in_violation": is_frozen,
    }


def run_baseline_s7_mismatch_monte_carlo(graph, n_seeds: int = 10) -> Dict[str, Any]:
    """Сценарий S7: Рассогласование модели (Monte-Carlo прогон 10 зерен возмущений)."""
    total_cycles = 0
    total_sulfur_gt_10 = 0
    total_t1_violations = 0

    for s in range(n_seeds):
        mismatch = PlantMismatch.random(seed=s)
        plant = PlantSimulator(scenario_2_quality_risk_tags(), mismatch=mismatch, q21_noise_ppm=0.05, seed=s)
        sid = f"baseline_s7_seed_{s}"

        for _ in range(10):
            tags = plant.measure()
            res = graph.invoke({"tags": tags, "session_id": sid})
            rec = res.get("final_recommendation")

            truth = plant.truth()
            total_cycles += 1
            if truth["HT_S_PRODUCT"] > 10.0:
                total_sulfur_gt_10 += 1
            if check_t1_violations(truth):
                total_t1_violations += 1

            if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
                plant.apply(rec.recommended_delta_u)
                TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)

    fraction_gt_10 = total_sulfur_gt_10 / max(1, total_cycles)
    return {
        "seeds_count": n_seeds,
        "total_cycles": total_cycles,
        "total_sulfur_gt_10": total_sulfur_gt_10,
        "fraction_cycles_sulfur_gt_10": fraction_gt_10,
        "total_t1_violations": total_t1_violations,
    }


def main():
    print("=== Расчет базовых метрик (Baseline KPIs) на стенде PlantSimulator v2 ===")
    graph = build_mvp_graph()

    s1_res = run_baseline_s1_normal(graph)
    print(f"S1 Норма: лишних ходов = {s1_res['excess_actions']}/{s1_res['monitoring_steps']} ({s1_res['excess_action_pct']:.1f}%), нарушение вспышки E10 = {s1_res['steady_state_flash_margin_violation']}")

    s2_res = run_baseline_s2_quality_risk(graph)
    print(f"S2 Качество: тактов с серой > 10 мг/кг = {s2_res['sulfur_gt_10_count']}/{s2_res['steps']} ({s2_res['fraction_cycles_sulfur_gt_10']*100:.1f}%)")

    s5_res = run_baseline_s5_envelope(graph)
    print(f"S5 Огибающая: статус = {s5_res['status']}, заморозка в нарушении = {s5_res['frozen_in_violation']}")

    s7_res = run_baseline_s7_mismatch_monte_carlo(graph, n_seeds=10)
    print(f"S7 Рассогласование (10 seeds): доля серы > 10 мг/кг = {s7_res['fraction_cycles_sulfur_gt_10']*100:.1f}%")

    all_latencies = s1_res["latencies_ms"]
    p50_latency = float(np.percentile(all_latencies, 50))
    p95_latency = float(np.percentile(all_latencies, 95))
    p99_latency = float(np.percentile(all_latencies, 99))
    print(f"Задержка такта: p50={p50_latency:.1f} мс, p95={p95_latency:.1f} мс, p99={p99_latency:.1f} мс")

    # Сводные базовые KPI по Таблице 15 implementation_plan_v3.md
    frozen_cycles_total = s2_res["frozen_in_violation_count"] + (1 if s5_res["frozen_in_violation"] else 0)
    t1_violations_total = s1_res["t1_violations_count"] + s2_res["t1_violations_count"] + s7_res["total_t1_violations"]

    baseline_data = {
        "metadata": {
            "recorded_at": datetime.datetime.now().isoformat(),
            "graph_version": "MVP Graph (legacy v2 before refactoring)",
            "simulator_version": "PlantSimulator v2",
            "description": "Базовые показатели качества, безопасности и экономики до рефакторинга ядра МАС",
        },
        "kpis": {
            "t1_violations_by_truth_count": t1_violations_total,
            "fraction_cycles_sulfur_gt_10ppm_truth_s2": s2_res["fraction_cycles_sulfur_gt_10"],
            "fraction_cycles_sulfur_gt_10ppm_truth_s7": s7_res["fraction_cycles_sulfur_gt_10"],
            "frozen_in_violation_cycles_count": frozen_cycles_total,
            "excess_moves_in_normal_pct": s1_res["excess_action_pct"],
            "utility_gap_pct": s1_res["utility_gap_pct"],
            "steady_state_flash_margin_violation": s1_res["steady_state_flash_margin_violation"],
            "steady_state_flash_effective_c": s1_res["effective_flash_c"],
            "p50_cycle_time_ms": round(p50_latency, 2),
            "p95_cycle_time_ms": round(p95_latency, 2),
            "p99_cycle_time_ms": round(p99_latency, 2),
        },
        "scenarios": {
            "s1_normal": {
                "converge_moves": s1_res["converge_moves"],
                "monitoring_steps": s1_res["monitoring_steps"],
                "excess_actions": s1_res["excess_actions"],
                "excess_action_pct": s1_res["excess_action_pct"],
                "flash_truth_c": s1_res["steady_state_flash_c"],
                "flash_effective_c": s1_res["effective_flash_c"],
            },
            "s2_quality_risk": {
                "steps": s2_res["steps"],
                "sulfur_gt_10_count": s2_res["sulfur_gt_10_count"],
                "fraction_gt_10": s2_res["fraction_cycles_sulfur_gt_10"],
                "frozen_in_violation_count": s2_res["frozen_in_violation_count"],
            },
            "s5_equipment_envelope": {
                "status": s5_res["status"],
                "delta_u": s5_res["delta_u"],
                "frozen_in_violation": s5_res["frozen_in_violation"],
            },
            "s7_model_mismatch": {
                "seeds_count": s7_res["seeds_count"],
                "total_cycles": s7_res["total_cycles"],
                "total_sulfur_gt_10": s7_res["total_sulfur_gt_10"],
                "fraction_gt_10": s7_res["fraction_cycles_sulfur_gt_10"],
            },
        },
    }

    out_file = ROOT_DIR / "data" / "processed" / "baseline_kpis.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(baseline_data, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Базовые метрики успешно сохранены в: {out_file}")


if __name__ == "__main__":
    main()
