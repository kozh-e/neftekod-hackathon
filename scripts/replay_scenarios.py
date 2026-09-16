"""Скрипт реплея 4 технологических сценариев ТЗ через мультиагентную систему (scripts/replay_scenarios.py).

Сценарии:
1. Нормальный режим: стабильная работа без лишних управляющих воздействий (<= 5% тактов с рекомендацией);
2. Риск ухудшения качества: рост серы сырья -> своевременная выработка корректирующего действия (SUCCESS_CORRECTIVE);
3. Деградация КИПиА/LIMS: залипание датчиков или устаревание лабораторного анализа -> безопасное удержание (SAFE_HOLD);
4. Конфликт ограничений: высокий T_out вблизи ПАЗ при росте серы -> приоритет безопасности над экономикой.

Результаты логируются в JSONL и выводятся сводной таблицей.
"""

from __future__ import annotations

import copy
import datetime
import math
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np

from src.agents.decision_log import append_decision
from src.agents.graph import build_mvp_graph
from src.twin.session import TWIN_STORE
from src.twin.tags import NOMINAL_OPERATING_POINT


ROOT_DIR = Path(__file__).resolve().parent.parent


def run_scenario_1_normal(graph, n_steps: int = 50) -> Dict[str, Any]:
    """Сценарий 1: Нормальный режим без лишних действий."""
    print("\n--- Запуск Сценария 1: Нормальный режим ---")
    session_id = f"replay_s1_normal_{int(datetime.datetime.now().timestamp())}"
    current_tags = dict(NOMINAL_OPERATING_POINT)
    rng = np.random.default_rng(seed=42)

    # 1. Замкнутый выход на рабочий оптимум до первого hold
    for _ in range(30):
        res = graph.invoke({"tags": current_tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS"):
            du = rec.recommended_delta_u
            for k, v in du.items():
                if k == "HT_TIN_SP":
                    current_tags["HT_T6"] += v
                    # Физическое снижение остаточной серы от нагрева
                    current_tags["HT_Q21"] = max(4.0, current_tags.get("HT_Q21", 8.43) - 0.5 * v)
                    current_tags["LIMS_HT_S"] = current_tags["HT_Q21"]
                elif k == "HT_FEED_SP":
                    current_tags["HT_F9"] += v
                elif k == "HT_P_SP":
                    current_tags["HT_P13"] += v
                elif k == "HT_GOR_SP":
                    current_tags["HT_GOR"] += v
            TWIN_STORE.commit_applied_move(session_id, du)
        else:
            break

    # 2. Мониторинг в стационарном режиме с малыми флуктуациями
    actions_count = 0
    statuses = []

    for step in range(n_steps):
        step_tags = dict(current_tags)
        step_tags["HT_T6"] += float(rng.normal(0.0, 0.02))
        step_tags["HT_F9"] += float(rng.normal(0.0, 0.05))
        step_tags["HT_P13"] += float(rng.normal(0.0, 0.001))

        res = graph.invoke({"tags": step_tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        st = rec.status if rec else "UNKNOWN"
        statuses.append(st)

        if st.startswith("SUCCESS"):
            actions_count += 1
            if rec and rec.recommended_delta_u:
                du = rec.recommended_delta_u
                for k, v in du.items():
                    if k == "HT_TIN_SP":
                        current_tags["HT_T6"] += v
                        current_tags["HT_Q21"] = max(4.0, current_tags.get("HT_Q21", 8.43) - 0.5 * v)
                        current_tags["LIMS_HT_S"] = current_tags["HT_Q21"]
                    elif k == "HT_FEED_SP":
                        current_tags["HT_F9"] += v
                    elif k == "HT_P_SP":
                        current_tags["HT_P13"] += v
                    elif k == "HT_GOR_SP":
                        current_tags["HT_GOR"] += v
                TWIN_STORE.commit_applied_move(session_id, du)

        append_decision(res, {"scenario": 1, "step": step, "tags": step_tags})

    action_pct = (actions_count / n_steps) * 100.0
    print(f"Тактов с управляющим воздействием: {actions_count}/{n_steps} ({action_pct:.1f}%)")
    return {
        "scenario": "1. Норма",
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
    """Сценарий 4: Конфликт технологических ограничений (высокий T_out при высокой сере)."""
    print("\n--- Запуск Сценария 4: Конфликт ограничений ---")
    session_id = f"replay_s4_conflict_{int(datetime.datetime.now().timestamp())}"
    conflict_tags = dict(NOMINAL_OPERATING_POINT)
    conflict_tags["HT_T6"] = 385.0   # T_in вблизи предела
    conflict_tags["HT_T11"] = 388.0  # T_out в опасной близости от 390.0 °C
    conflict_tags["HT_Q21"] = 9.7    # Сера требует нагрева, но нагрев запрещен ПАЗ

    res = graph.invoke({"tags": conflict_tags, "session_id": session_id})
    rec = res.get("final_recommendation")
    st = rec.status if rec else "UNKNOWN"
    du = rec.recommended_delta_u if rec else {}

    # Система не должна повышать температуру печи в зону ПАЗ!
    t_in_move = du.get("HT_TIN_SP", 0.0)
    print(f"Статус арбитража при конфликте: {st}")
    print(f"Предложенный шаг по HT_TIN_SP: {t_in_move:+.2f} °C (защита ПАЗ: не допускать перегрева > 390°C)")

    return {
        "scenario": "4. Конфликт ограничений",
        "arbitration_status": st,
        "tin_safe": t_in_move <= 0.0,
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
