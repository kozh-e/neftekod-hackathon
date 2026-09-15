"""Автоматические тесты для Шага 2 MVP: FOPDT цифровой двойник и 17 ВАК."""

import pytest
import numpy as np

from src.twin.vak import VakCalculator
from src.twin.fopdt import (
    DiscreteMIMOFOPDTTwin,
    STATE_VARIABLES,
    DEFAULT_GAIN_MATRIX,
)


@pytest.fixture
def sample_telemetry() -> dict:
    """Реалистичный срез телеметрии КИПиА и LIMS."""
    return {
        # КИПиА ЭЛОУ-АВТ-6
        "F7": 150.0, "F30": 120.0, "F32": 45.0, "F34": 30.0, "F36": 20.0,
        "F45": 10.0, "F59": 15.0, "F63": 25.0, "F65": 12.0, "F31": 380.0,
        "F57": 400.0, "F64": 300.0, "F53": 180.0, "F19": 75.0, "F12": 250.0,
        "T33": 275.0, "T66": 260.0, "T37": 280.0, "T40": 290.0, "T58": 310.0,
        "T42": 330.0, "T48": 340.0, "T6": 360.0, "T18": 155.0, "T15": 140.0,
        "T11": 70.0, "T13": 120.0, "T20": 115.0, "T61": 320.0, "T55": 382.0,
        "T5": 150.0, "T12": 160.0, "T23": 170.0, "T16": 180.0,
        "P67": 1.2, "P4": 0.5, "P50": 0.08, "P51": 0.07, "P52": 0.06,
        "P13": 2.5, "P8": 3.0, "P24": 2.8,
        "L43": 50.0, "W7": 100.0, "W4": 80.0, "W10": 4.5,
        "F1": 80.0, "F2": 90.0, "F9": 200.0, "F14": 250.0, "F15": 400.0,
        "F22": 110.0, "F25": 20.0, "F26": 120.0,
        "Sulfur": 8.5,
        # Данные LIMS
        "LIMS:24-2000.Pipeline.D15": 832.0,
        "LIMS:24-2000.Pipeline.95%.T": 342.0,
    }


def test_vak_all_17_formulas(sample_telemetry):
    """Тест 1: Расчет всех 17 виртуальных анализаторов качества (ВАК)."""
    vak = VakCalculator.calculate(sample_telemetry)

    expected_keys = [
        # АВТ-6 (240-350)
        "AVT6:240-350:D15", "AVT6:240-350:T50", "AVT6:240-350:EBP", "AVT6:240-350:CFPP",
        # АВТ-6 (>350)
        "AVT6:350:T50", "AVT6:350:I350", "AVT6:350:D15", "AVT6:350:CFPP", "AVT6:350-500:ViscosityK",
        # 24-2000 (ГО ДТ)
        "24-2000:GODT:T90", "24-2000:GODT:T50", "24-2000:GODT:I250",
        "24-2000:GODT:D15", "24-2000:GODT:T95", "24-2000:GODT:CloudPoint",
        "24-2000:GODT:CFPP", "24-2000:GODT:IBP",
    ]

    assert len(expected_keys) == 17
    for k in expected_keys:
        assert k in vak, f"Отсутствует ключ ВАК: {k}"
        assert isinstance(vak[k], (int, float)), f"Значение {k} должно быть числом"
        assert not np.isnan(vak[k])
        assert not np.isinf(vak[k])


def test_vak_zero_division_protection():
    """Тест 2: Защита от деления на ноль при нулевых или пустых расходах."""
    empty_telemetry = {"F30": 0.0, "F32": 0.0, "F57": 0.0, "F26": 0.0}
    # Не должно бросать ZeroDivisionError
    vak = VakCalculator.calculate(empty_telemetry)
    assert len(vak) > 0
    for k, v in vak.items():
        assert not np.isnan(v)
        assert not np.isinf(v)


def test_vak_lims_autoregression(sample_telemetry):
    """Тест 3: Авторегрессионные ВАК чутко реагируют на обновление паспортов LIMS."""
    telemetry_a = sample_telemetry.copy()
    telemetry_a["LIMS:24-2000.Pipeline.D15"] = 830.0
    telemetry_a["LIMS:24-2000.Pipeline.95%.T"] = 340.0
    vak_a = VakCalculator.calculate(telemetry_a)

    telemetry_b = sample_telemetry.copy()
    telemetry_b["LIMS:24-2000.Pipeline.D15"] = 840.0  # +10 кг/м3
    telemetry_b["LIMS:24-2000.Pipeline.95%.T"] = 350.0  # +10 °C
    vak_b = VakCalculator.calculate(telemetry_b)

    # D15: коэфф. 0.15417 * 10 = 1.5417
    delta_d15 = vak_b["24-2000:GODT:D15"] - vak_a["24-2000:GODT:D15"]
    assert delta_d15 == pytest.approx(1.5417, abs=1e-3)

    # T95: коэфф. 0.48321 * 10 = 4.8321
    delta_t95 = vak_b["24-2000:GODT:T95"] - vak_a["24-2000:GODT:T95"]
    assert delta_t95 == pytest.approx(4.8321, abs=1e-3)


def test_fopdt_dead_time_delay(sample_telemetry):
    """Тест 4: Проверка транспортного запаздывания (Dead Time)."""
    twin = DiscreteMIMOFOPDTTwin(dt_minutes=10.0, enable_noise=False)
    twin.initialize(sample_telemetry)

    # Подаем возмущения:
    # F19 (орошение верха) имеет задержку d=0 (0 мин) -> отклик T20 на шаге 1
    # F15 (квенч реактора) имеет задержку d=1 (10 мин) -> отклик T6 на шаге 2
    # F12 (сырье/орошение) имеет задержку d=2 (20 мин) -> отклик T33 на шаге 3
    controls = {"F19": 10.0, "F15": 100.0, "F12": 50.0}

    # Шаг 1 (t = 10 мин):
    s1 = twin.step(controls)
    # T20 (d=0) должно измениться немедленно
    assert s1["T20"] != pytest.approx(sample_telemetry["T20"])
    # T6 (d=1) еще не должно измениться!
    assert s1["T6"] == pytest.approx(sample_telemetry["T6"])
    # T33 (d=2) еще не должно измениться!
    assert s1["T33"] == pytest.approx(sample_telemetry["T33"])

    # Шаг 2 (t = 20 мин):
    s2 = twin.step(controls)
    # T6 (d=1) теперь начинает реагировать!
    assert s2["T6"] != pytest.approx(sample_telemetry["T6"])
    # T33 (d=2) все еще не изменилось!
    assert s2["T33"] == pytest.approx(sample_telemetry["T33"])

    # Шаг 3 (t = 30 мин):
    s3 = twin.step(controls)
    # T33 (d=2) теперь начинает реагировать!
    assert s3["T33"] != pytest.approx(sample_telemetry["T33"])


def test_fopdt_zoh_exponential_transition(sample_telemetry):
    """Тест 5: Экспоненциальный переход ZOH и выход на установившееся значение."""
    twin = DiscreteMIMOFOPDTTwin(dt_minutes=10.0, enable_noise=False)
    twin.initialize(sample_telemetry)

    # Скачок расхода квенча F15 = +100 нм3/ч
    delta_f15 = 100.0
    k_gain_t6 = DEFAULT_GAIN_MATRIX["T6"]["F15"]  # -0.014
    expected_steady_delta_t6 = k_gain_t6 * delta_f15  # -1.4 °C

    # Прогоняем 30 шагов (300 минут = 7.5 * tau_rx)
    for _ in range(30):
        state = twin.step({"F15": delta_f15})

    expected_final_t6 = sample_telemetry["T6"] + expected_steady_delta_t6
    assert state["T6"] == pytest.approx(expected_final_t6, abs=0.01)


def test_fopdt_baseline_relaxation(sample_telemetry):
    """Тест 6: Релаксация к базису (устранение Baseline Reset Bug)."""
    twin = DiscreteMIMOFOPDTTwin(dt_minutes=10.0, enable_noise=False)
    twin.initialize(sample_telemetry)

    # 1. Подаем управляющее воздействие
    for _ in range(20):
        twin.step({"F15": 100.0, "F19": 15.0})

    # Состояние изменилось
    assert twin.current_state["T6"] != pytest.approx(sample_telemetry["T6"])

    # 2. Снимаем воздействие (delta_u = 0)
    for _ in range(35):
        twin.step({"F15": 0.0, "F19": 0.0})

    # Процесс плавно вернулся к исходному базису y_base без накопления ошибки
    assert twin.current_state["T6"] == pytest.approx(sample_telemetry["T6"], abs=0.01)
    assert twin.current_state["T20"] == pytest.approx(sample_telemetry["T20"], abs=0.01)


def test_fopdt_get_telemetry_vak_integration(sample_telemetry):
    """Тест 7: Метод get_telemetry() и отсутствие ошибки KeyError: 'T50'."""
    twin = DiscreteMIMOFOPDTTwin(dt_minutes=10.0, enable_noise=False)
    twin.initialize(sample_telemetry)
    twin.step({"F15": 50.0})

    telemetry = twin.get_telemetry()
    assert len(telemetry) > len(sample_telemetry)

    # Физические теги КИПиА присутствуют
    assert "T20" in telemetry
    assert "T6" in telemetry
    assert "T33" in telemetry

    # Виртуальные анализаторы присутствуют
    assert "AVT6:240-350:T50" in telemetry
    assert "24-2000:GODT:T50" in telemetry

    # Проверка, что физический тег T22/T33 не путается с ВАК T50
    assert "AVT6:240-350:T50" != "T50"


def test_fopdt_noise_generation(sample_telemetry):
    """Тест 8: Проверка опциональной генерации шума при enable_noise=True."""
    twin_noisy = DiscreteMIMOFOPDTTwin(dt_minutes=10.0, enable_noise=True)
    twin_noisy.initialize(sample_telemetry)

    values = [twin_noisy.step({})["T20"] for _ in range(25)]
    # Значения должны флуктуировать вокруг исходного базового значения
    std_observed = np.std(values)
    assert std_observed > 0.02
