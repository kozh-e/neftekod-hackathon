"""Тесты кинетической модели реактора гидроочистки Р-202 (Этап 1, test_step7_kinetics)."""

import math
import pytest

from src.twin.params import ReactorParams
from src.twin.kinetics import ReactorKineticsCalculator, ReactorInputs


@pytest.fixture
def nominal_inputs() -> ReactorInputs:
    return ReactorInputs(
        t_in_c=363.3,
        feed_tph=219.6,
        p_mpa=3.922,
        gor_nm3m3=360.0,
        s_feed_ppm=9470.0,
        t95_feed_c=353.0,
        d15_feed=847.2,
        quench_tph=6.05,
    )


def test_kinetics_nominal_anchor(nominal_inputs):
    """Тест 1: Номинальная точка дает S_out в диапазоне [8.5, 8.7] и экзотерму в [0.3, 1.0]."""
    calc = ReactorKineticsCalculator(ReactorParams())
    out = calc.evaluate(nominal_inputs)

    assert 8.5 <= out.s_out_ppm <= 8.7, f"Сера в номинале: {out.s_out_ppm:.3f} вне [8.5, 8.7]"
    delta_t = out.t_out_c - nominal_inputs.t_in_c
    assert 0.3 <= delta_t <= 1.0, f"Экзотерма в номинале: {delta_t:.3f} вне [0.3, 1.0]"


@pytest.mark.parametrize(
    "param_name, delta, expected_sign",
    [
        ("t_in_c", +3.0, -1),      # Рост температуры ускоряет HDS -> сера снижается
        ("feed_tph", +20.0, +1),    # Рост нагрузки сокращает время пребывания -> сера растет
        ("p_mpa", +0.10, -1),       # Рост давления ускоряет реакцию -> сера снижается
        ("gor_nm3m3", +30.0, -1),   # Рост ВСГ/сырье снижает парциальное давление H2S -> сера снижается
        ("t95_feed_c", +5.0, +1),   # Утяжеление сырья увеличивает долю трудноудаляемой серы -> сера растет
        ("s_feed_ppm", +500.0, +1), # Рост серы сырья увеличивает выходную серу
    ]
)
def test_kinetics_sulfur_partial_derivatives(nominal_inputs, param_name, delta, expected_sign):
    """Тест 2: Проверка физической адекватности знаков частных производных серы."""
    calc = ReactorKineticsCalculator(ReactorParams())
    base_out = calc.evaluate(nominal_inputs)

    inp_dict = nominal_inputs.__dict__.copy()
    inp_dict[param_name] += delta
    varied_inputs = ReactorInputs(**inp_dict)

    varied_out = calc.evaluate(varied_inputs)
    diff = varied_out.s_out_ppm - base_out.s_out_ppm

    if expected_sign > 0:
        assert diff > 0.0, f"Ожидался рост серы при увеличении {param_name}"
    else:
        assert diff < 0.0, f"Ожидалось снижение серы при увеличении {param_name}"


def test_pressure_drop_increases_with_feed(nominal_inputs):
    """Тест 2b: Перепад давления на катализаторе возрастает с ростом расхода сырья."""
    calc = ReactorKineticsCalculator(ReactorParams())
    base_out = calc.evaluate(nominal_inputs)

    inp_dict = nominal_inputs.__dict__.copy()
    inp_dict["feed_tph"] += 20.0
    varied_out = calc.evaluate(ReactorInputs(**inp_dict))

    assert varied_out.dp_kpa > base_out.dp_kpa


def test_kinetics_temperature_step_effect(nominal_inputs):
    """Тест 3: Подъем температуры на +5 °C снижает серу не менее чем на 20% (S <= 0.8 * S_nom)."""
    calc = ReactorKineticsCalculator(ReactorParams())
    base_out = calc.evaluate(nominal_inputs)

    inp_dict = nominal_inputs.__dict__.copy()
    inp_dict["t_in_c"] += 5.0
    hot_out = calc.evaluate(ReactorInputs(**inp_dict))

    assert hot_out.s_out_ppm <= 0.80 * base_out.s_out_ppm


def test_kinetics_t95_heavy_feed_breakthrough(nominal_inputs):
    """Тест 4: Утяжеление сырья T95 на +10 °C пробивает границу ГОСТ (S > 10.0 ppm)."""
    calc = ReactorKineticsCalculator(ReactorParams())
    inp_dict = nominal_inputs.__dict__.copy()
    inp_dict["t95_feed_c"] += 10.0
    heavy_out = calc.evaluate(ReactorInputs(**inp_dict))

    assert heavy_out.s_out_ppm > 10.0, f"Сера при T95+10 должна превышать 10 ppm, получено {heavy_out.s_out_ppm:.2f}"


def test_kinetics_boundary_inputs_finite():
    """Тест 5: Граничные и нулевые входы не вызывают деления на ноль или исключений."""
    calc = ReactorKineticsCalculator(ReactorParams())
    extreme_inputs = ReactorInputs(
        t_in_c=300.0,
        feed_tph=1e-3,
        p_mpa=0.0,
        gor_nm3m3=0.0,
        s_feed_ppm=0.0,
        t95_feed_c=300.0,
        d15_feed=800.0,
        quench_tph=0.0,
    )
    out = calc.evaluate(extreme_inputs)
    assert not math.isnan(out.s_out_ppm)
    assert not math.isinf(out.s_out_ppm)
    assert not math.isnan(out.dp_kpa)
    assert not math.isinf(out.dp_kpa)
    assert out.s_out_ppm >= 0.0
