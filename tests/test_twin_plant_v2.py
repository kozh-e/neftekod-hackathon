"""Юнит-тесты стенда PlantSimulator v2 (P0.2).

Проверяет:
1. Рассогласование модели (PlantMismatch): варьирование E_h/R, k_h, перепада, dF30/dT55.
2. График ЛИМС: разрыв между отбором и готовностью, задержка 2-8 часов.
3. Отказы ПАК: frozen, drift, clamping (307.0/313.0), NaN.
4. Выдача измеренных положений MV в snapshot.
5. Метод truth(): корректность истинных значений без шума и отказов.
"""

import math
import pytest
from src.twin.params import load_params
from src.twin.plant import PlantSimulator, PlantMismatch
from src.twin.tags import NOMINAL_OPERATING_POINT


def test_plant_simulator_v2_mismatch():
    """Проверка рассогласования модели (PlantMismatch)."""
    tags0 = dict(NOMINAL_OPERATING_POINT)
    mismatch = PlantMismatch(
        e_h_r_factor=1.15,
        k_h_factor=0.85,
        fouling_factor=1.25,
        df30_dt55=0.15,
    )
    base_params = load_params()
    plant = PlantSimulator(tags0, mismatch=mismatch, seed=42)

    # Проверяем, что параметры физической модели скорректированы
    assert plant.twin.params.reactor.E_h_R == pytest.approx(base_params.reactor.E_h_R * 1.15)
    assert plant.twin.params.reactor.dp_ref_kpa == pytest.approx(base_params.reactor.dp_ref_kpa * 1.25)
    assert plant.twin.params.feed.dF30_dT55 == pytest.approx(0.15)

    snap = plant.measure()
    truth = plant.truth()

    # Засорение Р-202 приводит к росту перепада давления
    assert truth["HT_DP_KPA"] > base_params.reactor.dp_ref_kpa


def test_plant_simulator_v2_random_mismatch():
    """Проверка генерации случайного рассогласования с заданным зерном seed."""
    m1 = PlantMismatch.random(seed=123)
    m2 = PlantMismatch.random(seed=123)
    assert m1.e_h_r_factor == m2.e_h_r_factor
    assert 0.85 <= m1.e_h_r_factor <= 1.15
    assert 0.8 <= m1.k_h_factor <= 1.2
    assert 1.0 <= m1.fouling_factor <= 1.3
    assert -0.28 <= m1.df30_dt55 <= 1.57


def test_plant_simulator_v2_lims_schedule():
    """Проверка графика ЛИМС с разрывом во времени между отбором и результатом."""
    tags0 = dict(NOMINAL_OPERATING_POINT)
    plant = PlantSimulator(
        tags0,
        enable_lims_schedule=True,
        lims_interval_hours=8.0,
        lims_delay_hours=4.0,  # 4 часа = 24 такта по 10 мин
        seed=1,
    )

    # В начале запланирована проба на t=0, готовность на t=4ч (шаг 24)
    plant.schedule_lims_sample(delay_hours=4.0)

    # Делаем резкий шаг уставки (нагрев), снижающий серу
    plant.apply({"HT_TIN_SP": 5.0})

    # Прогоняем 10 шагов (< 24). Онлайн сера HT_Q21 падает, а LIMS_HT_S должна оставаться старой!
    for _ in range(10):
        snap = plant.measure()

    assert snap["HT_Q21"] < tags0["HT_Q21"]
    # LIMS еще не пришел
    assert snap["LIMS_HT_S"] == pytest.approx(tags0["LIMS_HT_S"], abs=0.1)

    # Прогоняем еще 20 шагов (всего 30 > 24). Анализ ЛИМС должен примениться!
    for _ in range(20):
        snap = plant.measure()

    assert "lims_age_hours" in snap
    assert snap["lims_age_hours"] >= 4.0


def test_plant_simulator_v2_sensor_faults():
    """Проверка отказов ПАК: frozen, drift, clamping, NaN."""
    tags0 = dict(NOMINAL_OPERATING_POINT)
    plant = PlantSimulator(tags0, seed=10)

    # 1. NaN
    plant.set_fault("PAK_D15", "nan")
    snap = plant.measure()
    assert math.isnan(snap["PAK_D15"])

    # 2. Clamping
    plant.set_fault("AVT_D10", "clamping", value=307.0)
    snap = plant.measure()
    assert snap["AVT_D10"] == 307.0

    # 3. Frozen
    plant.set_fault("HT_Q21", "frozen", value=9.85)
    plant.apply({"HT_TIN_SP": 10.0})  # резкий нагрев
    for _ in range(5):
        snap = plant.measure()
    assert snap["HT_Q21"] == 9.85
    # Истинное значение при этом упало!
    assert plant.truth()["HT_S_PRODUCT"] < 8.0

    # 4. Drift (на установившемся датчике)
    plant.clear_faults()
    plant.set_fault("HT_F9", "drift", drift_rate_per_step=0.5)
    f1 = plant.measure()["HT_F9"]
    f2 = plant.measure()["HT_F9"]
    assert f2 == pytest.approx(f1 + 0.5)


def test_plant_simulator_v2_measured_mvs_and_truth():
    """Проверка выдачи измеренных MV и метода truth()."""
    tags0 = dict(NOMINAL_OPERATING_POINT)
    plant = PlantSimulator(tags0, q21_noise_ppm=0.05, seed=7)

    snap = plant.measure()
    truth = plant.truth()

    # Проверка measured_mvs в snapshot
    assert "measured_mvs" in snap
    assert "HT_FEED_SP" in snap["measured_mvs"]
    assert "HT_TIN_SP" in snap["measured_mvs"]
    assert "AVT_T55_SP" in snap["measured_mvs"]
    assert snap["HT_F9"] == snap["measured_mvs"]["HT_FEED_SP"]

    # Проверка truth()
    assert "HT_S_PRODUCT" in truth
    assert "HT_DP_KPA" in truth
    assert "HT_T11" in truth
    assert "AVT_T55" in truth
    assert "HT_FLASH" in truth

    # Истинное значение не содержит добавленного шума анализатора
    assert isinstance(truth["HT_S_PRODUCT"], float)
    assert not math.isnan(truth["HT_S_PRODUCT"])
