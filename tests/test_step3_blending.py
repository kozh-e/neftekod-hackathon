"""Автоматические тесты для Шага 3 MVP: Оптимизация поточного блендинга."""

import pytest
import math

from src.agents.blending import (
    OptimizerBlending,
    BlendingResult,
    BlendingRequest,
    node_blending_agent,
)
from src.agents.state import RawTelemetry


def test_blending_dilution_loophole_blocking():
    """Тест 1: Жесткая блокировка «Dilution Loophole» при сере дизеля > 10.0 ppm."""
    # Превышение серы: 12.5 ppm
    res_bad = OptimizerBlending.solve_recipe(
        sulfur_diesel=12.5,
        cfpp_diesel=-8.0,
        flash_diesel=65.0,
        target_cfpp=-15.0,
        target_flash=56.0
    )
    assert not res_bad.success
    assert "Dilution Loophole" in res_bad.error_message
    assert "10.0 ppm" in res_bad.error_message

    # Граничное превышение: 10.01 ppm
    res_edge = OptimizerBlending.solve_recipe(
        sulfur_diesel=10.01,
        cfpp_diesel=-8.0,
        flash_diesel=65.0
    )
    assert not res_edge.success

    # Допустимая норма: 9.8 ppm
    res_ok = OptimizerBlending.solve_recipe(
        sulfur_diesel=9.8,
        cfpp_diesel=-8.0,
        flash_diesel=65.0
    )
    assert res_ok.success


def test_blending_fbi_calculation_and_inversion():
    """Тест 2: Точность расчета индекса Вики-Читтендена FBI и обратного расчета температуры вспышки."""
    # Прямой расчет
    fbi_65 = OptimizerBlending.calculate_fbi(65.0)
    # 10^(4.1485 - 0.0601 * 65) = 10^(0.242) = 1.74582
    assert fbi_65 == pytest.approx(1.7458, rel=1e-3)

    fbi_42 = OptimizerBlending.calculate_fbi(42.0)
    # 10^(4.1485 - 0.0601 * 42) = 10^(1.6243) = 42.1017
    assert fbi_42 == pytest.approx(42.1017, rel=1e-3)

    # Обратимость формулы
    for t_test in [40.0, 52.5, 56.0, 65.0, 75.0]:
        fbi = OptimizerBlending.calculate_fbi(t_test)
        t_recovered = OptimizerBlending.calculate_flash_from_fbi(fbi)
        assert t_recovered == pytest.approx(t_test, abs=1e-4)


def test_blending_optimal_recipe_highs():
    """Тест 3: Расчет оптимальной рецептуры на базе SciPy HiGHS."""
    # Гидрогенизат: сера 8.0 ppm, CFPP = -6.0 °C, Flash = 65.0 °C
    # Требуется: CFPP <= -15.0 °C, Flash >= 56.0 °C
    res = OptimizerBlending.solve_recipe(
        sulfur_diesel=8.0,
        cfpp_diesel=-6.0,
        flash_diesel=65.0,
        target_cfpp=-15.0,
        target_flash=56.0,
        price_diesel=60000.0,
        price_kerosene=85000.0,
        price_ddp=1500000.0
    )

    assert res.success
    assert res.v_diesel + res.v_kerosene == pytest.approx(1.0, abs=1e-4)
    assert 0.50 <= res.v_diesel <= 1.0
    assert 0.0 <= res.v_kerosene <= 0.20
    assert 0.0 <= res.v_ddp_ppm <= 1500.0

    # Проверка соответствия стандартам ГОСТ 32511-2013
    assert res.expected_flash >= 56.0 - 1e-4
    assert res.expected_cfpp <= -15.0 + 1e-4
    assert res.expected_sulfur <= 10.0
    assert res.cost_per_ton > 60000.0  # Себестоимость включает вовлечение компонентов


def test_blending_flash_point_non_linear_safety():
    """Тест 4: Нелинейность вспышки - защита от пожароопасного рецепта."""
    # Если дизель имеет температуру вспышки 58.0 °C (близко к границе),
    # добавление 20% керосина (вспышка 42.0 °C) обвалит вспышку смеси ниже 55.0 °C.
    # Оптимизатор ОБЯЗАН уменьшить долю керосина и опереться на присадку ДДП.
    res = OptimizerBlending.solve_recipe(
        sulfur_diesel=8.0,
        cfpp_diesel=-7.0,
        flash_diesel=58.0,
        target_cfpp=-15.0,
        target_flash=56.0
    )

    assert res.success
    # Доля керосина должна быть строго ограничена во избежание обвала вспышки
    assert res.expected_flash >= 56.0 - 1e-4
    assert res.v_kerosene < 0.20  # Не может быть 20%, иначе вспышка < 56°C


def test_blending_ddp_saturation_isotherm():
    """Тест 5: Изотерма насыщения ДДП (максимум 9.76 °C при 1500 ppm)."""
    # Проверяем, что решатель находит решение при высокой потребности в депрессии,
    # не превышая предельной дозировки 1500 ppm по Ленгмюру.
    res = OptimizerBlending.solve_recipe(
        sulfur_diesel=8.0,
        cfpp_diesel=-4.0,
        flash_diesel=70.0,
        target_cfpp=-18.0,
        target_flash=56.0
    )

    assert res.success
    # Присадка не может превышать 1500 ppm
    assert 0.0 < res.v_ddp_ppm <= 1500.0 + 1e-3
    assert res.expected_cfpp <= -18.0 + 1e-4


def test_blending_infeasible_graceful_failure():
    """Тест 6: Регламентный отказ при физически недостижимых ограничениях (Infeasible LP)."""
    # Сверхтяжелый дизель (CFPP = +10.0 °C, Flash = 55.0 °C)
    # При максимуме 20% керосина и 1500 ppm ДДП физически невозможно получить -25.0 °C
    res = OptimizerBlending.solve_recipe(
        sulfur_diesel=8.0,
        cfpp_diesel=+10.0,
        flash_diesel=55.0,
        target_cfpp=-25.0,
        target_flash=56.0
    )

    assert not res.success
    assert res.error_message is not None
    assert "LP Solver failed" in res.error_message or "Невозможно" in res.error_message


def test_node_blending_agent_langgraph_integration():
    """Тест 7: Интеграция узла блендинга с состоянием MasGraphState."""
    telemetry = RawTelemetry(
        timestamp="2026-09-15T15:00:00",
        P52=0.05,
        D10=840.0,
        F15=400.0,
        T55=380.0,
        Sulfur=8.2,
        flash_diesel=64.0
    )
    # Задаем свойство ВАК через extra
    telemetry_dict = telemetry.model_dump()
    telemetry_dict["24-2000:GODT:CFPP"] = -8.0
    telemetry_obj = RawTelemetry.model_validate(telemetry_dict)

    state = {"raw_telemetry": telemetry_obj}
    output = node_blending_agent(state)

    assert "blending_recipe" in output
    recipe: BlendingResult = output["blending_recipe"]
    assert isinstance(recipe, BlendingResult)
    assert recipe.success
    assert recipe.expected_cfpp <= -15.0
