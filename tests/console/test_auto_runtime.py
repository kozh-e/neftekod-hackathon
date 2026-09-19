"""Тесты рантайма сессий и режима АВТОМАТ (R3 / R6)."""

import pytest

from src.console.auto import auto_step, check_auto_exit, set_mode
from src.console.corridor import corridor_for
from src.console.demo import load_scenario
from src.console.runtime import ConsoleSession


def test_auto_never_exceeds_corridor():
    session = load_scenario("test_auto_corr", "S1", warmup_ticks=10)
    session.seconds_per_tick = 0.0
    set_mode(session, "AUTO")

    u_prev = dict(session.u_current)
    for _ in range(10):
        session.tick_single(warmup=False)
        u_now = dict(session.u_current)
        for sp, val in u_now.items():
            delta = abs(val - u_prev[sp])
            corr = corridor_for(sp)
            if corr.max_step_per_tick is not None:
                assert delta <= corr.max_step_per_tick + 1e-4, f"{sp} шаг {delta} > {corr.max_step_per_tick}"
            else:
                assert delta < 1e-4, f"{sp} без коридора изменился на {delta}"
        u_prev = u_now


def test_auto_exit_refusal_data():
    session = load_scenario("test_exit_ref", "S1", warmup_ticks=5)
    session.seconds_per_tick = 0.0
    set_mode(session, "AUTO")
    assert session.mode == "AUTO"

    # Инжектируем отказ ПАК серы + устаревание LIMS
    session.plant.set_fault("HT_Q21", "nan")
    session.plant.tags["lims_age_hours"] = 26.0

    # За 1-2 такта автомат должен отключиться
    session.tick_single(warmup=False)
    assert session.mode == "ADVISORY"
    assert session.last_auto_exit is not None
    assert session.last_auto_exit.reason_code == "REFUSAL_DATA"
    assert session.banner is not None


def test_auto_exit_lims_stale():
    session = load_scenario("test_exit_lims", "S1", warmup_ticks=5)
    session.seconds_per_tick = 0.0
    set_mode(session, "AUTO")

    session.plant.tags["lims_age_hours"] = 25.0
    session.tick_single(warmup=False)
    assert session.mode == "ADVISORY"
    assert session.last_auto_exit is not None
    assert session.last_auto_exit.reason_code in ("LIMS_STALE", "REFUSAL_DATA")


def test_auto_exit_near_limit_s4():
    # S4 стартует с T55 = 385.0 °C (в пределах 2.0 °C от 386.4 °C)
    session = load_scenario("test_s4_near", "S4", warmup_ticks=1)
    session.seconds_per_tick = 0.0

    # Попытка включить автомат в S4 должна вернуть отказ автовыхода
    exit_check = check_auto_exit(session)
    assert exit_check is not None
    assert exit_check[0] == "NEAR_LIMIT"


def test_auto_skip_step():
    session = load_scenario("test_skip", "S1", warmup_ticks=5)
    session.seconds_per_tick = 0.0
    set_mode(session, "AUTO")

    session.skipped_next = True
    u_before = dict(session.u_current)
    session.tick_single(warmup=False)
    u_after = dict(session.u_current)

    assert u_before == u_after
    assert any("Шаг пропущен" in item.text for item in session.feed)


def test_auto_exit_operator():
    session = load_scenario("test_op_exit", "S1", warmup_ticks=5)
    session.seconds_per_tick = 0.0
    set_mode(session, "AUTO")
    assert session.mode == "AUTO"

    set_mode(session, "ADVISORY")
    assert session.mode == "ADVISORY"
    assert session.last_auto_exit is not None
    assert session.last_auto_exit.reason_code == "OPERATOR"
    assert any("Оператор переключил в режим СОВЕТ" in item.text for item in session.feed)

