"""Тесты экономической модели MarginModel (test_economics.py, ранее test_step7_rollout.py).

Тесты легаси RolloutOptimizationAgent (генерация кандидатов, горизонт, производительность)
удалены вместе с самим модулем src/agents/optimization.py (аудит 2026-09-20, не используется
build_core_graph()) — эквивалентная генерация кандидатов в v3 покрыта generator.py и его
тестами. Здесь остаётся только тест самой MarginModel, который не зависит от оптимизатора.
"""

from __future__ import annotations

from src.agents.economics import MarginModel
from src.twin.chain import FullChainTwin
from src.twin.params import load_params
from src.twin.tags import NOMINAL_OPERATING_POINT
import pytest


@pytest.fixture
def twin_nominal() -> FullChainTwin:
    params = load_params()
    twin = FullChainTwin(params)
    twin.initialize(NOMINAL_OPERATING_POINT)
    return twin


def test_rollout_economics_responses(twin_nominal: FullChainTwin):
    """Маржа: HT_TIN_SP +2 -> < 0; HT_FEED_SP +5 -> throughput > 0."""
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
