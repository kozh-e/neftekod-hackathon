"""Тесты производительности и сторожевого таймера бюджета времени такта (P5.2 / ADR-13, Q13).

Проверяет:
1. Мягкий бюджет времени такта (soft budget): p95 времени исполнения графа core_v3 <= 2.0 с (по 50 циклам);
2. Жесткий бюджет времени (hard budget): срабатывание сторожевого таймера (watchdog 10.0 с) при искусственной
   задержке -> регламентный отказ REFUSAL_TIMEOUT с текстом ТЗ §5 и пустым вектором Delta u.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
from unittest.mock import patch
import numpy as np
import pytest
from starlette.testclient import TestClient

from main import app, POLICY_STORE
from src.agents.graph import build_core_graph
from src.agents.policy import PolicyConfig
from src.agents.scenarios import scenario_1_normal_tags
from src.xai.card import TZ_REFUSAL_TIMEOUT


def test_soft_cycle_budget_p95():
    """
    Замер 50 циклов исполнения детерминированного переговорного графа core_v3.
    Требование ТЗ §6 / implementation_plan_v3.md §13 (P5.2):
    Мягкий бюджет p95 <= 2.0 с.
    """
    graph = build_core_graph()
    tags = scenario_1_normal_tags()

    # Прогревочный запуск (JIT / кэширование компилятора)
    warmup_res = graph.invoke({"tags": tags})
    assert warmup_res is not None

    n_cycles = 50
    latencies: List[float] = []

    for _ in range(n_cycles):
        t0 = time.perf_counter()
        res = graph.invoke({"tags": tags})
        lat = time.perf_counter() - t0
        latencies.append(lat)
        assert res.get("decision") is not None

    p50 = float(np.percentile(latencies, 50))
    p95 = float(np.percentile(latencies, 95))
    p99 = float(np.percentile(latencies, 99))

    print(f"\n[PERF] Core v3 latencies (n={n_cycles}): p50={p50:.3f}s, p95={p95:.3f}s, p99={p99:.3f}s")
    assert p95 <= 2.0, f"p95={p95:.3f}s превышает мягкий бюджет 2.0 с!"


def test_hard_budget_watchdog_timeout():
    """
    Проверка сторожевого таймера жесткого бюджета времени (10 с) в API /optimize.
    При возникновении непредвиденной задержки (например, зависании решателя/сети)
    система обязана прервать ожидание и выдать регламентный отказ REFUSAL_TIMEOUT.
    """
    client = TestClient(app)
    tags = scenario_1_normal_tags()

    # 1. Проверяем срабатывание сторожевого таймера с настраиваемым таймаутом (например, 0.1 с при задержке 0.3 с)
    custom_policy = PolicyConfig(hard_budget_s=0.1)

    def delayed_invoke(*args, **kwargs):
        time.sleep(0.3)
        return {"decision": None}

    orig_policy = POLICY_STORE.active_policy
    POLICY_STORE.set_active_policy(custom_policy)
    try:
        with patch("main.core_graph.invoke", side_effect=delayed_invoke):
            resp = client.post(
                "/api/v1/optimize",
                json={"tags": tags, "graph_mode": "core_v3"},
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "REFUSAL_TIMEOUT"
            assert data["recommended_delta_u"] == {}
            assert data["explanation"] == TZ_REFUSAL_TIMEOUT
            assert "Внимание" in data["markdown_report"] or "Превышен" in data["markdown_report"]
    finally:
        POLICY_STORE.set_active_policy(orig_policy)
