"""Скрипт реплея 4 технологических сценариев ТЗ через мультиагентную систему (scripts/replay_scenarios.py).

Сценарии:
1. Нормальный режим: стабильная работа без лишних управляющих воздействий (<= 5% тактов с рекомендацией);
2. Риск ухудшения качества: рост серы сырья -> своевременная выработка корректирующего действия (SUCCESS_CORRECTIVE);
3. Деградация КИПиА/LIMS: залипание датчиков или устаревание лабораторного анализа -> безопасное удержание (SAFE_HOLD);
4. Сквозной консенсус МАС (tz:997): оптимизатор предлагает нагрев печи П-3, надежность ветирует T55 ≥ 387 °C,
   качество требует стабилизации фракционного состава -> Парето-оптимальное решение в допустимой зоне.

Результаты логируются в JSONL и выводятся сводной таблицей.
"""

from __future__ import annotations

import copy
import datetime
import math
from pathlib import Path
import sys
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np

from src.agents.decision_log import append_decision
from src.agents.graph import build_mvp_graph
from src.agents.scenarios import scenario_1_normal_tags, scenario_4_conflict_tags
from src.twin.plant import PlantSimulator
from src.twin.session import TWIN_STORE
from src.twin.tags import NOMINAL_OPERATING_POINT


ROOT_DIR = Path(__file__).resolve().parent.parent


def run_scenario_1_normal(graph, n_steps: int = 50, max_converge: int = 30) -> Dict[str, Any]:
    """
    Сценарий 1 (tz:988): сера 8.2 мг/кг, плотность 835 кг/м³ — без лишних управляющих действий.
    Установка моделируется PlantSimulator (та же динамика, что у двойника), шум анализатора серы 0.05 ppm.
    Фаза 1: выход на экономический оптимум до первого такта без хода. Фаза 2: n_steps тактов мониторинга.
    """
    print("\n--- Запуск Сценария 1: Нормальный режим ---")
    session_id = f"replay_s1_normal_{int(datetime.datetime.now().timestamp())}"
    plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)

    def cycle(step: int, phase: str) -> bool:
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        moved = bool(rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u)
        if moved:
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(session_id, rec.recommended_delta_u)
        append_decision(res, {"scenario": 1, "phase": phase, "step": step, "tags": tags})
        return moved

    converge_moves = 0
    for step in range(max_converge):
        if not cycle(step, "converge"):
            break
        converge_moves += 1

    actions_count = sum(cycle(step, "monitor") for step in range(n_steps))
    action_pct = (actions_count / n_steps) * 100.0
    print(f"Ходов выхода на оптимум: {converge_moves}; тактов с управляющим воздействием после: {actions_count}/{n_steps} ({action_pct:.1f}%)")
    return {
        "scenario": "1. Норма",
        "converge_moves": converge_moves,
        "total_steps": n_steps,
        "actions": actions_count,
        "action_pct": action_pct,
        "success_rate_ok": action_pct <= 5.0,
    }


def run_scenario_2_quality_risk(graph, n_steps: int = 20) -> Dict[str, Any]:
    """Сценарий 2: Риск ухудшения качества серы."""
    print("\n--- Запуск Сценария 2: Риск ухудшения качества ---")
    session_id = f"replay_s2_quality_{int(datetime.datetime.now().timestamp())}"
    current_tags = dict(NOMINAL_OPERATING_POINT)
    # Постепенное утяжеление сырья и рост серы
    current_tags["AVT_F30"] = 145.0  # рост тяжелой фракции
    current_tags["HT_Q20"] = 9800.0  # сера сырья
    current_tags["HT_Q21"] = 9.8     # сера на выходе вблизи предела

    corrective_count = 0
    blend_ok_count = 0

    for step in range(n_steps):
        res = graph.invoke({"tags": current_tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        st = rec.status if rec else "UNKNOWN"

        if st == "SUCCESS_CORRECTIVE":
            corrective_count += 1

        recipe = res.get("blending_recipe")
        if recipe and getattr(recipe, "success", False):
            blend_ok_count += 1

        if rec and rec.recommended_delta_u:
            for k, v in rec.recommended_delta_u.items():
                if k == "HT_TIN_SP":
                    current_tags["HT_T6"] += v
                elif k == "HT_P_SP":
                    current_tags["HT_P13"] += v
                elif k == "HT_FEED_SP":
                    current_tags["HT_F9"] += v

        append_decision(res, {"scenario": 2, "step": step, "tags": current_tags})

    print(f"Тактов с SUCCESS_CORRECTIVE: {corrective_count}/{n_steps}")
    print(f"Успешных рецептур блендинга: {blend_ok_count}/{n_steps}")
    return {
        "scenario": "2. Риск качества",
        "total_steps": n_steps,
        "corrective_actions": corrective_count,
        "blending_success": blend_ok_count,
    }


def run_scenario_3_data_degradation(graph) -> Dict[str, Any]:
    """Сценарий 3: Деградация КИП и устаревание анализов LIMS."""
    print("\n--- Запуск Сценария 3: Деградация данных ---")
    session_id = f"replay_s3_deg_{int(datetime.datetime.now().timestamp())}"

    # 3.1. Устаревание LIMS > 24 часов
    stale_lims_tags = dict(NOMINAL_OPERATING_POINT)
    stale_lims_tags["lims_age_hours"] = 28.0
    res_stale = graph.invoke({"tags": stale_lims_tags, "session_id": session_id})
    rec_stale = res_stale.get("final_recommendation")
    stale_is_safe_hold = rec_stale.status == "SAFE_HOLD" if rec_stale else False

    # 3.2. Аппаратный клампинг критичного датчика (HT_T6 = 307.0)
    clamped_tags = dict(NOMINAL_OPERATING_POINT)
    clamped_tags["HT_T6"] = 307.0
    res_clamped = graph.invoke({"tags": clamped_tags, "session_id": session_id})
    rec_clamped = res_clamped.get("final_recommendation")
    clamped_is_safe_hold = rec_clamped.status == "SAFE_HOLD" if rec_clamped else False

    print(f"LIMS > 24ч -> Safe Hold: {stale_is_safe_hold}")
    print(f"Клампинг 307.0 -> Safe Hold: {clamped_is_safe_hold}")

    return {
        "scenario": "3. Деградация данных",
        "stale_lims_safe_hold": stale_is_safe_hold,
        "clamped_sensor_safe_hold": clamped_is_safe_hold,
    }


def run_scenario_4_conflict_constraints(graph) -> Dict[str, Any]:
    """Сценарий 4 (tz:997): нагрев печи ради отбора -> вето надежности -> требование качества -> компромисс."""
    print("\n--- Запуск Сценария 4: Сквозной консенсус МАС ---")
    session_id = f"replay_s4_conflict_{int(datetime.datetime.now().timestamp())}"
    res = graph.invoke({"tags": scenario_4_conflict_tags(), "session_id": session_id})
    append_decision(res, {"scenario": 4, "tags": scenario_4_conflict_tags()})

    rec = res.get("final_recommendation")
    selected = res.get("selected_candidate")
    pareto = res.get("pareto")
    candidates = res.get("candidates", [])
    top = max(candidates, key=lambda c: c.expected_margin) if candidates else None
    heating = [c for c in candidates if c.delta_u.get("AVT_T55_SP", 0.0) > 0.0]
    reports = res.get("audit_reports", [])
    heat_ids = {c.candidate_id for c in heating}
    rel_veto = [r for r in reports if r.agent == "reliability" and r.candidate_id in heat_ids and r.is_vetoed]
    quality_req = [q for r in reports if r.agent == "quality" and r.candidate_id in heat_ids for q in r.requirements]

    st = rec.status if rec else "UNKNOWN"
    hold = next((c for c in candidates if c.is_hold), None)
    decision_id = selected.candidate_id if selected is not None else (hold.candidate_id if hold is not None else None)
    print(f"Самое выгодное предложение оптимизатора: {top.candidate_id if top else None} ({top.expected_margin if top else 0:+.0f} руб/ч)")
    for r in rel_veto:
        print(f"Вето Агента Надежности: {r.violation_reason}")
    for q in quality_req:
        print(f"Требование Агента Качества: {q}")
    print(f"Статус арбитража: {st}; решение: {decision_id}")

    return {
        "scenario": "4. Сквозной консенсус МАС",
        "optimizer_proposes_heating": bool(top is not None and top.delta_u.get("AVT_T55_SP", 0.0) > 0.0),
        "reliability_veto_t55": any("T55=387" in (r.violation_reason or "") for r in rel_veto),
        "quality_requirement": bool(quality_req),
        "arbitration_status": st,
        "xai_card": bool(rec is not None and rec.markdown_report),
        "decision_on_pareto_front": bool(pareto is not None and pareto.is_on_front(decision_id)),
    }


def main():
    graph = build_mvp_graph()
    results = []

    res1 = run_scenario_1_normal(graph)
    results.append(res1)

    res2 = run_scenario_2_quality_risk(graph)
    results.append(res2)

    res3 = run_scenario_3_data_degradation(graph)
    results.append(res3)

    res4 = run_scenario_4_conflict_constraints(graph)
    results.append(res4)

    print("\n" + "=" * 60)
    print("ИТОГОВАЯ СВОДКА РЕПЛЕЯ ТЕХНОЛОГИЧЕСКИХ СЦЕНАРИЕВ:")
    print("=" * 60)
    for r in results:
        print(r)
    print("=" * 60)


if __name__ == "__main__":
    main()
