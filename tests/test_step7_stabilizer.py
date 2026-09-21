"""Тесты модели колонны стабилизации К-201 (Этап 1, test_step7_stabilizer)."""

import pytest

from src.twin.params import StabilizerParams
from src.twin.stabilizer import StabilizerColumnCalculator


def test_stabilizer_nominal_point():
    """Тест 1: Вспышка в номинальной точке равна 68 ± 0.5 °C."""
    calc = StabilizerColumnCalculator(StabilizerParams())
    flash_nom = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173)
    assert flash_nom == pytest.approx(68.0, abs=0.5)


def test_stabilizer_feed_load_drop():
    """Тест 2: Рост нагрузки F9 на +30 т/ч снижает вспышку на 3.5–4.7 °C."""
    calc = StabilizerColumnCalculator(StabilizerParams())
    flash_nom = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173)
    flash_loaded = calc.evaluate(feed_tph=219.6 + 30.0, p24_mpa=0.585, w7_tph=0.173)

    drop = flash_nom - flash_loaded
    # a_F = -0.136 °C/(т/ч) * 30 т/ч = 4.08 °C
    assert 3.5 <= drop <= 4.7, f"Снижение вспышки: {drop:.2f} °C вне диапазона [3.5, 4.7]"


def test_stabilizer_p24_derivative():
    """Тест 3: Рост давления верха К-201 (P24) снижает температуру вспышки.

    Величина эффекта задаётся калиброванным a_P, поэтому проверяется не «магическое»
    число, а корректность проводки параметра в модель и его физическая правдоподобность.
    """
    params = StabilizerParams()
    calc = StabilizerColumnCalculator(params)
    flash_base = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173)
    flash_high_p = calc.evaluate(feed_tph=219.6, p24_mpa=0.585 + 0.10, w7_tph=0.173)

    assert flash_high_p < flash_base, "рост давления верха К-201 обязан снижать вспышку"
    diff = flash_base - flash_high_p
    assert diff == pytest.approx(-params.a_P * 0.10, abs=1e-6), "параметр a_P проведён неверно"
    # Физическая правдоподобность самого коэффициента: 0.5…4 °C на 0.1 МПа
    assert 0.5 <= diff <= 4.0, f"чувствительность к P24 вне физичного диапазона: {diff:.2f} °C"


def test_stabilizer_t18_anchor():
    """Тест 5: Якорь по APC-анализатору HT_T18 работает и остаётся опциональным."""
    params = StabilizerParams()
    calc = StabilizerColumnCalculator(params)

    base = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173)
    at_nominal = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173,
                               t18_c=params.t18_ref)
    # В номинальной точке анализатора якорь ничего не добавляет
    assert at_nominal == pytest.approx(base, abs=1e-9)

    # Рост показания анализатора повышает оценку вспышки, с весом a_T18
    higher = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173,
                           t18_c=params.t18_ref + 5.0)
    assert higher > base
    assert higher - base == pytest.approx(params.a_T18 * 5.0, abs=1e-6)
    assert 0.0 <= params.a_T18 <= 1.0, "вес показания анализатора вне [0; 1]"


def test_stabilizer_physical_bounds():
    """Тест 4: Значение вспышки строго ограничено диапазоном [40.0, 90.0] °C."""
    calc = StabilizerColumnCalculator(StabilizerParams())
    # Экстремальная разгрузка -> вспышка не выше 90 °C
    flash_high = calc.evaluate(feed_tph=10.0, p24_mpa=0.1, w7_tph=50.0)
    assert flash_high <= 90.0

    # Экстремальная перегрузка -> вспышка не ниже 40 °C
    flash_low = calc.evaluate(feed_tph=500.0, p24_mpa=2.0, w7_tph=0.0)
    assert flash_low >= 40.0
