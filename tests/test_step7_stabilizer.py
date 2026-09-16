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
    """Тест 3: Рост давления верха К-201 (P24) снижает температуру вспышки."""
    calc = StabilizerColumnCalculator(StabilizerParams())
    flash_base = calc.evaluate(feed_tph=219.6, p24_mpa=0.585, w7_tph=0.173)
    flash_high_p = calc.evaluate(feed_tph=219.6, p24_mpa=0.585 + 0.10, w7_tph=0.173)

    assert flash_high_p < flash_base
    diff = flash_base - flash_high_p
    # a_P = -19.9 °C/МПа * 0.1 МПа = 1.99 °C
    assert diff == pytest.approx(1.99, abs=0.1)


def test_stabilizer_physical_bounds():
    """Тест 4: Значение вспышки строго ограничено диапазоном [40.0, 90.0] °C."""
    calc = StabilizerColumnCalculator(StabilizerParams())
    # Экстремальная разгрузка -> вспышка не выше 90 °C
    flash_high = calc.evaluate(feed_tph=10.0, p24_mpa=0.1, w7_tph=50.0)
    assert flash_high <= 90.0

    # Экстремальная перегрузка -> вспышка не ниже 40 °C
    flash_low = calc.evaluate(feed_tph=500.0, p24_mpa=2.0, w7_tph=0.0)
    assert flash_low >= 40.0
