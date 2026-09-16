"""Тесты официальных расчетных моделей ВАК (Этап 0, test_step7_vak_official)."""

import pytest
from src.twin.vak import VakCalculator, VAK_REFERENCE_EXAMPLES


def test_vak_all_17_reference_examples():
    """Тест 1: Все 17 моделей ВАК совпадают с официальными примерами расчетов с точностью ±0.05."""
    assert len(VAK_REFERENCE_EXAMPLES) == 17
    for model_name, (inputs, expected_val) in VAK_REFERENCE_EXAMPLES.items():
        res = VakCalculator.calculate(inputs)
        assert model_name in res, f"Модель {model_name} не рассчитана"
        actual = res[model_name]
        assert actual == pytest.approx(expected_val, abs=0.05), (
            f"Несовпадение для {model_name}: ожидалось {expected_val}, получено {actual}"
        )


def test_avt_i350_positive_t6_derivative():
    """Тест 2: Выход фракции AVT6:350:I350 возрастает с ростом температуры AVT_T6 (знак +)."""
    base_inputs = {"L43": 60.0, "T6": 230.0, "T18": 150.0, "F64": 100.0, "T15": 130.0, "T11": 55.0}
    res_low = VakCalculator.calculate(base_inputs)["AVT6:350:I350"]

    high_inputs = dict(base_inputs)
    high_inputs["T6"] = 240.0
    res_high = VakCalculator.calculate(high_inputs)["AVT6:350:I350"]

    assert res_high > res_low, "Выход I350 должен возрастать при росте температуры низа К-1 T6 (+0.76664*T6)"
    delta = res_high - res_low
    assert delta == pytest.approx(0.76664 * 10.0, rel=1e-2)


def test_godt_cfpp_depends_on_t23_not_t6():
    """Тест 3: 24-2000:GODT:CFPP зависит от температуры низа К-201 T23, но НЕ от температуры входа Р-202 T6."""
    base_inputs = {
        "HT_T23": 234.0,
        "HT_P8": 0.12,
        "HT_F9": 170.0,
        "HT_W7": 0.15,
        "HT_P24": 0.60,
        "HT_T6": 360.0,
    }
    res_base = VakCalculator.calculate(base_inputs)["24-2000:GODT:CFPP"]

    # Меняем T6 (не должно влиять)
    t6_changed = dict(base_inputs)
    t6_changed["HT_T6"] = 380.0
    res_t6 = VakCalculator.calculate(t6_changed)["24-2000:GODT:CFPP"]
    assert res_t6 == pytest.approx(res_base, abs=1e-6), "CFPP ГО ДТ не должен зависеть от T6 (входа реактора)"

    # Меняем T23 (должно существенно влиять)
    t23_changed = dict(base_inputs)
    t23_changed["HT_T23"] = 244.0
    res_t23 = VakCalculator.calculate(t23_changed)["24-2000:GODT:CFPP"]
    assert res_t23 != pytest.approx(res_base, abs=0.1)
    # Коэффициент 0.22088 на 10 °C -> ~2.21 °C
    assert (res_t23 - res_base) == pytest.approx(0.22088 * 10.0, rel=1e-2)
