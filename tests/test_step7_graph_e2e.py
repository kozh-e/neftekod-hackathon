"""Сквозные тесты Этапа 6: Четыре демо-режима ТЗ и производительность графа (test_step7_graph_e2e.py).

Проверяет:
1. Демо 1: Норма без лишних действий (после выхода на установившийся режим <= 2 действий из 50 тактов);
2. Демо 2: Риск ухудшения качества (quality_risk_tags -> SUCCESS_CORRECTIVE, рецепт успешен,
   сера смеси <= 9.5 ppm, T95 товарного топлива <= 360 °C с запасом 2σ на ГО ДТ, ЦЧ >= 51.5);
3. Демо 3: Деградация КИП/LIMS (degraded_tags -> SAFE_HOLD с дословным текстом ТЗ; D10=307 -> не Safe Hold);
4. Демо 4: Полный цикл агентов (candidates, audit_reports, alternatives, confidence, разделы XAI);
5. Производительность: p95 graph.invoke < 150 мс.
"""

from __future__ import annotations

import time
import numpy as np
import pytest

from src.agents.graph import build_mvp_graph
from src.agents.safe_hold import REFUSAL_VERBATIM_TEXT
from src.twin.session import TWIN_STORE


@pytest.fixture
def graph():
    return build_mvp_graph()


def test_e2e_normal_operation_no_excessive_moves(graph, nominal_tags):
    """1. Норма без лишних действий: не более 2 управляющих воздействий за 50 тактов после выхода на базу."""
    session_id = f"test_e2e_norm_{int(time.time() * 1000)}"
    current_tags = dict(nominal_tags)

    # 1. Замкнутый выход на рабочий оптимум до первого hold (по плану v2 §1072)
    for _ in range(30):
        res = graph.invoke({"tags": current_tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS"):
            du = rec.recommended_delta_u
            for k, v in du.items():
                if k == "HT_TIN_SP":
                    current_tags["HT_T6"] += v
                    # Физическое снижение остаточной серы от нагрева реактора
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

    # 2. Симуляция 50 тактов со случайным технологическим шумом
    rng = np.random.default_rng(seed=0)
    success_count = 0

    for _ in range(50):
        noisy_tags = dict(current_tags)
        noisy_tags["HT_T6"] += float(rng.normal(0.0, 0.05))
        noisy_tags["HT_F9"] += float(rng.normal(0.0, 0.1))
        noisy_tags["HT_P13"] += float(rng.normal(0.0, 0.002))

        res = graph.invoke({"tags": noisy_tags, "session_id": session_id})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS"):
            success_count += 1
            if rec.recommended_delta_u:
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

    print(f"\nNormal operation 50-cycles actions count: {success_count}/50")
    assert success_count <= 2, f"Слишком много лишних действий в норме: {success_count} > 2"


def test_e2e_quality_risk_scenario(graph, quality_risk_tags):
    """2. Риск качества: quality_risk_tags -> SUCCESS_CORRECTIVE; рецепт успешен, свойства соблюдены."""
    res = graph.invoke({"tags": quality_risk_tags})

    rec = res.get("final_recommendation")
    assert rec is not None
    assert rec.status == "SUCCESS_CORRECTIVE"
    assert len(rec.recommended_delta_u) > 0

    recipe = res.get("blending_recipe")
    assert recipe is not None
    assert recipe.success is True

    # Проверка свойств товарного ДТ
    assert recipe.expected_sulfur <= 9.5
    assert recipe.expected_t95 is None or recipe.expected_t95 <= 360.0 + 1e-6
    assert recipe.expected_cetane is None or recipe.expected_cetane >= 51.5


def test_e2e_degraded_data_scenario(graph, degraded_tags, nominal_tags):
    """3. Деградация КИП/LIMS -> SAFE_HOLD с текстом ТЗ. Отдельно D10=307 -> не Safe Hold."""
    # 3.1. Аварийная деградация (LIMS > 24 ч, NaN, 307)
    res_degraded = graph.invoke({"tags": degraded_tags})
    rec_deg = res_degraded.get("final_recommendation")
    assert rec_deg is not None
    assert rec_deg.status == "SAFE_HOLD"
    assert rec_deg.explanation == REFUSAL_VERBATIM_TEXT

    # 3.2. Изолированное залипание некритичного тега D10=307
    d10_tags = dict(nominal_tags)
    d10_tags["AVT_D10"] = 307.0
    d10_tags["D10"] = 307.0
    res_d10 = graph.invoke({"tags": d10_tags})
    rec_d10 = res_d10.get("final_recommendation")
    assert rec_d10 is not None
    assert not rec_d10.status.startswith("SAFE_HOLD")


def test_e2e_full_cycle_artifacts(graph, quality_risk_tags):
    """4. Полный цикл: наличие candidates, audit_reports, alternatives, confidence, разделов XAI."""
    res = graph.invoke({"tags": quality_risk_tags})

    assert "candidates" in res and len(res["candidates"]) > 0
    assert "audit_reports" in res and len(res["audit_reports"]) > 0
    assert "alternatives" in res and len(res["alternatives"]) > 0
    assert "confidence" in res and "score" in res["confidence"]

    rec = res.get("final_recommendation")
    assert rec is not None
    report = rec.markdown_report
    assert report is not None

    # Проверка разделов XAI
    assert "Прогноз" in report
    assert "Почему не альтернативы" in report
    assert "Допущения модели" in report
    assert "Рецепт блендинга" in report

    # Детальная проверка корректности данных XAI
    assert "Hold:" in report or "Hold SS:" in report
    assert "GODT" in report
    assert "Kerosene" in report
    assert "Gasoil" in report


def test_e2e_performance_p95(graph, nominal_tags):
    """5. Производительность: p95 graph.invoke за 100 вызовов < 150 мс."""
    # Прогрев
    graph.invoke({"tags": nominal_tags})

    latencies = []
    for _ in range(100):
        t0 = time.perf_counter()
        graph.invoke({"tags": nominal_tags})
        latencies.append(time.perf_counter() - t0)

    p95 = np.percentile(latencies, 95)
    print(f"\nEnd-to-End graph.invoke p95 latency: {p95 * 1000:.2f} ms")
    assert p95 < 0.150, f"p95 graph.invoke {p95 * 1000:.2f} ms exceeds 150 ms threshold"
