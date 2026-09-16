"""Тесты Этапа 3: Роллаут-оптимизатор и экономическая модель (test_step7_rollout.py).

Проверяет:
1. cand_hold первый в списке с маржой 0.0;
2. Количество кандидатов <= 25; значения в границах [lo, hi], шаги <= max_move,
   детерминированные ID; отсутствие запрещенных тегов (HT_F14, AVT_F31, AVT_F19);
3. Свойства кандидатов берутся как max/min по траектории и установившемуся режиму;
4. Горизонт упреждения H * dt >= theta + 3 * tau_max;
5. Экономические отклики: рост температуры печи дает отрицательную маржу, рост подачи - положительный throughput;
6. Производительность: p95 propose() < 100 мс.
"""

from __future__ import annotations

import time
import numpy as np
import pytest

from src.agents.candidates import DEFAULT_MVS, generate_candidates
from src.agents.economics import MarginModel
from src.agents.optimization import RolloutOptimizationAgent
from src.twin.chain import FullChainTwin
from src.twin.params import TwinParams, load_params
from src.twin.tags import NOMINAL_OPERATING_POINT


@pytest.fixture
def twin_nominal() -> FullChainTwin:
    params = load_params()
    twin = FullChainTwin(params)
    twin.initialize(NOMINAL_OPERATING_POINT)
    return twin


def test_rollout_cand_hold_first(twin_nominal: FullChainTwin):
    """1. cand_hold первый в списке с маржой 0.0."""
    agent = RolloutOptimizationAgent()
    candidates, hold_traj = agent.propose(twin_nominal)

    assert len(candidates) > 0
    first = candidates[0]
    assert first.candidate_id == "cand_hold"
    assert first.is_hold is True
    assert first.delta_u == {}
    assert first.expected_margin == 0.0


def test_rollout_candidates_constraints_and_tags(twin_nominal: FullChainTwin):
    """2. Кандидатов <= 25, в пределах [lo, hi], |Δ| <= max_move, детерминированные id, нет запрещенных тегов."""
    agent = RolloutOptimizationAgent()
    candidates, _ = agent.propose(twin_nominal)

    assert len(candidates) <= 25
    forbidden_tags = {"HT_F14", "AVT_F31", "AVT_F19"}
    mv_map = {mv.name: mv for mv in DEFAULT_MVS}

    u0 = twin_nominal.u_current
    prev_ids = [c.candidate_id for c in candidates]

    for cand in candidates:
        # Проверка отсутствия запрещенных тегов
        for k in cand.delta_u.keys():
            assert k not in forbidden_tags, f"Запрещенный тег {k} обнаружен в кандидате {cand.candidate_id}"
            assert k in mv_map, f"Неизвестный параметр {k}"
            mv = mv_map[k]
            val = u0[k] + cand.delta_u[k]
            assert val >= mv.lo - 1e-4, f"{k} = {val} < lo {mv.lo}"
            assert val <= mv.hi + 1e-4, f"{k} = {val} > hi {mv.hi}"
            assert abs(cand.delta_u[k]) <= mv.max_move + 1e-4, f"Ход {cand.delta_u[k]} превышает max_move {mv.max_move}"

    # Детерминированность генерации
    candidates_repeat, _ = agent.propose(twin_nominal)
    repeat_ids = [c.candidate_id for c in candidates_repeat]
    assert prev_ids == repeat_ids


def test_rollout_properties_worst_case(twin_nominal: FullChainTwin):
    """3. expected_sulfur == max(traj S U ss S), expected_flash == min(...)"""
    agent = RolloutOptimizationAgent()
    candidates, _ = agent.propose(twin_nominal)

    for cand in candidates:
        s_vals = cand.trajectory["HT_S_PRODUCT"] + [cand.steady_state["HT_S_PRODUCT"]]
        assert cand.expected_sulfur == pytest.approx(max(s_vals), abs=0.01)

        f_vals = cand.trajectory["HT_FLASH"] + [cand.steady_state["HT_FLASH"]]
        assert cand.expected_flash == pytest.approx(min(f_vals), abs=0.01)

        t95_vals = cand.trajectory["HT_T95_PRODUCT"] + [cand.steady_state["HT_T95_PRODUCT"]]
        assert cand.expected_t95 == pytest.approx(max(t95_vals), abs=0.01)


def test_rollout_horizon_calculation():
    """4. H * dt >= theta + 3 * tau_max (с учетом [6, 48])."""
    params = load_params()
    agent = RolloutOptimizationAgent(params)
    h_steps = agent.horizon()

    theta_total = params.feed.theta_min + params.dynamics.theta_s
    tau_max = max(params.feed.tau_mix_min, params.dynamics.tau_s, params.dynamics.tau_flash)
    required_time = theta_total + 3.0 * tau_max

    assert 6 <= h_steps <= 48
    assert h_steps * params.dt_min >= min(48 * params.dt_min, required_time)


def test_rollout_economics_responses(twin_nominal: FullChainTwin):
    """5. Маржа: HT_TIN_SP +2 -> < 0; HT_FEED_SP +5 -> throughput > 0."""
    econ_model = MarginModel()
    u0 = twin_nominal.u_current
    ss_hold = twin_nominal.steady_state(u0)

    # Тест нагрева печи HT_TIN_SP +2
    u_tin = dict(u0)
    u_tin["HT_TIN_SP"] = u0["HT_TIN_SP"] + 2.0
    ss_tin = twin_nominal.steady_state(u_tin)
    m_tin = econ_model.evaluate(ss_tin, ss_hold, u_tin, u0)
    assert m_tin.furnace > 500.0  # затраты на печь выросли
    assert m_tin.total < 0.0      # суммарная маржа ухудшилась

    # Тест роста сырья HT_FEED_SP +5
    u_feed = dict(u0)
    u_feed["HT_FEED_SP"] = u0["HT_FEED_SP"] + 5.0
    ss_feed = twin_nominal.steady_state(u_feed)
    m_feed = econ_model.evaluate(ss_feed, ss_hold, u_feed, u0)
    assert m_feed.throughput > 0.0

    # Проверка методов спредов и операционных затрат
    spreads = econ_model.crack_spreads
    assert spreads["straight_to_godt"] > 10000.0
    assert spreads["crude_to_godt"] > 15000.0

    gross_margin = econ_model.calc_hourly_gross_margin(219.6)
    assert gross_margin > 1_000_000.0

    opex = econ_model.calc_hourly_operating_costs(219.6, 363.3, 3.922, 93309.0, 363.65)
    assert opex["total_opex_rub_h"] > 100_000.0
    assert opex["furnace_cost_rub_h"] > 0.0


def test_rollout_performance_p95(twin_nominal: FullChainTwin):
    """6. p95 propose() за 20 запусков < 100 мс."""
    agent = RolloutOptimizationAgent()
    # Прогревочный вызов
    agent.propose(twin_nominal)

    latencies = []
    for _ in range(20):
        t0 = time.perf_counter()
        agent.propose(twin_nominal)
        latencies.append(time.perf_counter() - t0)

    p95 = np.percentile(latencies, 95)
    print(f"\nRollout propose() p95 latency: {p95 * 1000:.2f} ms")
    assert p95 < 0.100, f"p95 latency {p95 * 1000:.2f} ms exceeds 100 ms limit"
