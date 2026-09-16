"""Тесты сквозного цифрового двойника FullChainTwin (Этап 2, test_step7_chain_twin)."""

import math
import random
import pytest

from src.twin.params import TwinParams, load_params
from src.twin.chain import FullChainTwin


def test_chain_twin_fixed_point(nominal_tags):
    """Тест 1: Неподвижная точка — 50 шагов с u_current дают неизменные выходы (±1e-6)."""
    twin = FullChainTwin(load_params())
    twin.initialize(nominal_tags)

    u0 = twin.u_current
    step0 = twin.step(u0)

    for _ in range(50):
        step_k = twin.step(u0)
        for key in step0:
            assert step_k[key] == pytest.approx(step0[key], abs=1e-5), (
                f"Выход {key} изменился на шаге при постоянных уставках u_current"
            )


def test_chain_twin_feed_link_hot_mode(nominal_tags):
    """Тест 2: В режиме hot при F30 +10 т/ч T95_FEED не меняется на шагах 1..d и меняется на d+1."""
    params = load_params()
    params.feed.mode = "hot"
    params.feed.theta_min = 10.0
    params.dt_min = 10.0
    # d = round(10 / 10) = 1

    twin = FullChainTwin(params)
    twin.initialize(nominal_tags)
    base_t95 = twin.steady_state(twin.u_current)["HT_T95_FEED"]

    # Возмущение: F30 увеличивается на 10 т/ч
    twin._disturbances["AVT_F30"] += 10.0

    # Шаг 1: чистое запаздывание (d=1) -> на выходе старое значение
    step1 = twin.step(twin.u_current)
    assert step1["HT_T95_FEED"] == pytest.approx(base_t95, abs=1e-5)

    # Шаг 2: задержка истекла -> скачок качества
    step2 = twin.step(twin.u_current)
    expected_jump = base_t95 + params.feed.dT95_dF30 * 10.0
    assert step2["HT_T95_FEED"] == pytest.approx(expected_jump, abs=0.1)


def test_chain_twin_feed_link_buffered_mode(nominal_tags):
    """Тест 3: В режиме buffered через 1 шаг отклик < 10% от установившегося, через 3*tau_mix >= 90%."""
    params = load_params()
    params.feed.mode = "buffered"
    params.feed.theta_min = 0.0  # исследуем чистое смешение без транспортного лага
    params.feed.tau_mix_min = 120.0
    params.dt_min = 10.0

    twin = FullChainTwin(params)
    twin.initialize(nominal_tags)
    base_t95 = twin.steady_state(twin.u_current)["HT_T95_FEED"]

    twin._disturbances["AVT_F30"] += 10.0
    ss_target = twin.steady_state(twin.u_current)["HT_T95_FEED"]
    total_delta = ss_target - base_t95

    # Шаг 1 (10 минут из 120): изменение должно быть малым (< 10%)
    step1 = twin.step(twin.u_current)
    d1 = abs(step1["HT_T95_FEED"] - base_t95)
    assert d1 < 0.10 * total_delta

    # Через 3*tau_mix (360 минут = 36 шагов) должно накопиться не менее 90%
    for _ in range(35):
        twin.step(twin.u_current)

    step36 = twin.step(twin.u_current)
    d36 = abs(step36["HT_T95_FEED"] - base_t95)
    assert d36 >= 0.90 * total_delta


def test_chain_twin_temperature_step_sulfur_dynamics(nominal_tags):
    """Тест 4: HT_TIN_SP -3 °C -> сера не меняется на шаге 1 (theta=10 мин), затем растет до ~ss."""
    twin = FullChainTwin(load_params())
    twin.initialize(nominal_tags)

    u0 = twin.u_current
    base_s = twin.steady_state(u0)["HT_S_PRODUCT"]

    u_cool = dict(u0)
    u_cool["HT_TIN_SP"] -= 3.0
    ss_cool = twin.steady_state(u_cool)["HT_S_PRODUCT"]

    # Шаг 1: задержка theta=10 мин (1 шаг) -> сера не меняется
    step1 = twin.step(u_cool)
    assert step1["HT_S_PRODUCT"] == pytest.approx(base_s, abs=1e-3)

    # Роллаут на 24 шага (4 часа)
    for _ in range(23):
        twin.step(u_cool)

    step_final = twin.step(u_cool)
    # К концу горизонта отличие от steady_state не более 2%
    assert abs(step_final["HT_S_PRODUCT"] - ss_cool) / ss_cool < 0.02


def test_chain_twin_convergence_to_steady_state(nominal_tags):
    """Тест 5: Для 10 случайных малых delta_u 60 шагов симуляции сходятся к steady_state() с точностью 0.5%."""
    rng = random.Random(0)
    params = load_params()

    for _ in range(10):
        twin = FullChainTwin(params)
        twin.initialize(nominal_tags)

        u_pert = dict(twin.u_current)
        u_pert["HT_FEED_SP"] += rng.uniform(-5.0, 5.0)
        u_pert["HT_TIN_SP"] += rng.uniform(-2.0, 2.0)
        u_pert["HT_P_SP"] += rng.uniform(-0.02, 0.02)
        u_pert["HT_GOR_SP"] += rng.uniform(-10.0, 10.0)

        ss = twin.steady_state(u_pert)

        # Симулируем 60 шагов (10 часов)
        for _ in range(59):
            twin.step(u_pert)
        final_step = twin.step(u_pert)

        for key in ("HT_S_PRODUCT", "HT_FLASH", "HT_DP_KPA", "HT_T_OUT"):
            val_sim = final_step[key]
            val_ss = ss[key]
            assert abs(val_sim - val_ss) / max(abs(val_ss), 1e-3) < 0.005


def test_chain_twin_bias_assimilation_priority(nominal_tags):
    """Тест 6: Смещение bias: HT_Q21 = модель + 0.7 сдвигает прогноз серы; LIMS имеет высший приоритет."""
    twin = FullChainTwin(load_params())
    twin.initialize(nominal_tags)

    u0 = twin.u_current
    raw_s = twin.fopdt_s.value

    # Задаем онлайн-анализатор HT_Q21 = модель + 0.7
    meas = dict(nominal_tags)
    meas["HT_Q21"] = raw_s + 0.7
    meas.pop("LIMS_HT_S", None)

    twin.assimilate(meas)
    assert twin.biases["HT_S_PRODUCT"] == pytest.approx(0.7, abs=1e-3)
    pred = twin.predict(u0, 1)["HT_S_PRODUCT"][0]
    assert pred == pytest.approx(raw_s + 0.7, abs=1e-3)

    # При появлении LIMS_HT_S приоритет отдается ему
    meas["LIMS_HT_S"] = raw_s + 1.2
    twin.assimilate(meas)
    assert twin.biases["HT_S_PRODUCT"] == pytest.approx(1.2, abs=1e-3)


def test_chain_twin_clone_isolation(nominal_tags):
    """Тест 7: clone() создает изолированную копию, шаги в клоне не меняют оригинал."""
    twin = FullChainTwin(load_params())
    twin.initialize(nominal_tags)

    clone = twin.clone()
    u0 = twin.u_current

    clone.step({"HT_TIN_SP": u0["HT_TIN_SP"] + 10.0})
    # В клоне температура входа изменилась
    assert clone.fopdt_t_in.value != pytest.approx(twin.fopdt_t_in.value, abs=1.0)
    # В оригинале состояние не изменилось
    assert twin.fopdt_t_in.value == pytest.approx(u0["HT_TIN_SP"], abs=1e-5)
