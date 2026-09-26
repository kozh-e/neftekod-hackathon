"""Тесты многокомпонентного блендинга из резервуаров (Этап 5, test_step7_blending_tanks)."""

import random
import pytest

from src.agents.blending import (
    BlendComponent,
    AdditiveSegment,
    BlendProblem,
    solve_blend,
)
from src.agents.tanks import ComponentTank


@pytest.fixture
def base_components():
    comp_godt = BlendComponent(
        name="GODT",
        price_rub_t=60000.0,
        stock_t=5000.0,
        v_min=0.50,
        v_max=1.0,
        s_ppm=8.6,
        d15=836.1,
        flash_c=68.0,
        cfpp_c=-6.0,
        t95_c=347.0,
        cn=53.75,
    )
    comp_kero = BlendComponent(
        name="Kerosene",
        price_rub_t=85000.0,
        stock_t=800.0,
        v_min=0.0,
        v_max=0.20,
        s_ppm=2.0,
        d15=785.0,
        flash_c=42.0,
        cfpp_c=-45.0,
        t95_c=240.0,
        cn=42.0,
    )
    comp_gasoil = BlendComponent(
        name="Gasoil",
        price_rub_t=50000.0,
        stock_t=1500.0,
        v_min=0.0,
        v_max=0.20,
        s_ppm=8.0,
        d15=860.0,
        flash_c=90.0,
        cfpp_c=0.0,
        t95_c=365.0,
        cn=45.0,
    )
    return comp_godt, comp_kero, comp_gasoil


@pytest.fixture
def base_additives():
    add_a1 = AdditiveSegment(name="depressor_seg1", max_kg_t=0.5, effect_per_kg_t=12.0, price_rub_t=1500000.0, target="cfpp")
    add_a2 = AdditiveSegment(name="depressor_seg2", max_kg_t=1.0, effect_per_kg_t=3.76, price_rub_t=1500000.0, target="cfpp")
    add_b1 = AdditiveSegment(name="cetane_seg1", max_kg_t=0.5, effect_per_kg_t=6.0, price_rub_t=1200000.0, target="cn")
    add_b2 = AdditiveSegment(name="cetane_seg2", max_kg_t=1.0, effect_per_kg_t=2.0, price_rub_t=1200000.0, target="cn")
    return add_a1, add_a2, add_b1, add_b2


def test_three_components_gasoil_binding_constraint(base_components, base_additives):
    """Тест 1: Три компонента — газойль дешев, но ограничен T95 или плотностью."""
    c_godt, c_kero, c_gasoil = base_components
    free_gasoil = BlendComponent(
        name=c_gasoil.name,
        price_rub_t=c_gasoil.price_rub_t,
        stock_t=c_gasoil.stock_t,
        v_min=c_gasoil.v_min,
        v_max=0.50,  # Разрешаем вовлечение выше 20%, чтобы сработало технологическое ограничение
        s_ppm=c_gasoil.s_ppm,
        d15=c_gasoil.d15,
        flash_c=c_gasoil.flash_c,
        cfpp_c=c_gasoil.cfpp_c,
        t95_c=c_gasoil.t95_c,
        cn=c_gasoil.cn,
    )
    problem = BlendProblem(
        components=(c_godt, c_kero, free_gasoil),
        additives=base_additives,
        batch_t=2000.0,
        target_cfpp=-15.0,
    )
    res = solve_blend(problem)
    assert res.success
    assert res.shares["Gasoil"] > 0.0
    # Активное ограничение должно быть либо по T95, либо по Density_Max
    assert any(c in res.binding_constraints for c in ("T95_Max", "Density_Max"))


def test_cetane_additive_injection_on_low_cn(base_components, base_additives):
    """Тест 2: При низком ЦЧ ГО ДТ (50.0) активируется присадка B и смесь достигает ЦЧ >= 51.5."""
    c_godt, c_kero, c_gasoil = base_components
    low_cn_godt = BlendComponent(
        name=c_godt.name,
        price_rub_t=c_godt.price_rub_t,
        stock_t=c_godt.stock_t,
        v_min=c_godt.v_min,
        v_max=c_godt.v_max,
        s_ppm=c_godt.s_ppm,
        d15=c_godt.d15,
        flash_c=c_godt.flash_c,
        cfpp_c=c_godt.cfpp_c,
        t95_c=c_godt.t95_c,
        cn=50.0,  # Заниженное ЦЧ базового дизеля
    )

    problem = BlendProblem(
        components=(low_cn_godt, c_kero, c_gasoil),
        additives=base_additives,
        batch_t=2000.0,
        target_cfpp=-15.0,
        cn_min=51.5,
    )
    res = solve_blend(problem)
    assert res.success
    total_dose_b = res.additive_doses_kg_t["cetane_seg1"] + res.additive_doses_kg_t["cetane_seg2"]
    assert total_dose_b > 0.0
    assert res.expected_cetane >= 51.5 - 1e-4


def test_kerosene_stock_limit_enforced(base_components, base_additives):
    """Тест 3: Остаток керосина 50 т при партии 2000 т ограничивает его вовлечение."""
    c_godt, c_kero, c_gasoil = base_components
    low_stock_kero = BlendComponent(
        name=c_kero.name,
        price_rub_t=c_kero.price_rub_t,
        stock_t=50.0,  # Малый остаток
        v_min=c_kero.v_min,
        v_max=c_kero.v_max,
        s_ppm=c_kero.s_ppm,
        d15=c_kero.d15,
        flash_c=c_kero.flash_c,
        cfpp_c=c_kero.cfpp_c,
        t95_c=c_kero.t95_c,
        cn=c_kero.cn,
    )

    problem = BlendProblem(
        components=(c_godt, low_stock_kero, c_gasoil),
        additives=base_additives,
        batch_t=2000.0,
        target_cfpp=-15.0,
        rho_ref=836.0,
    )
    res = solve_blend(problem)
    assert res.success
    expected_max_share = (50.0 * 836.0) / (2000.0 * 785.0)
    assert res.shares["Kerosene"] <= expected_max_share + 1e-4


def test_gasoil_dilution_loophole_blocked(base_components, base_additives):
    """Тест 4: Газойль с серой 15 ppm попадает в blocked_components, его доля строго 0."""
    c_godt, c_kero, c_gasoil = base_components
    dirty_gasoil = BlendComponent(
        name=c_gasoil.name,
        price_rub_t=c_gasoil.price_rub_t,
        stock_t=c_gasoil.stock_t,
        v_min=c_gasoil.v_min,
        v_max=c_gasoil.v_max,
        s_ppm=15.0,  # Некондиционная сера > 10 ppm
        d15=c_gasoil.d15,
        flash_c=c_gasoil.flash_c,
        cfpp_c=c_gasoil.cfpp_c,
        t95_c=c_gasoil.t95_c,
        cn=c_gasoil.cn,
    )

    problem = BlendProblem(
        components=(c_godt, c_kero, dirty_gasoil),
        additives=base_additives,
        batch_t=2000.0,
        target_cfpp=-15.0,
    )
    res = solve_blend(problem)
    assert res.success
    assert any("Gasoil" in b for b in res.blocked_components)
    assert res.shares["Gasoil"] == 0.0


def test_blending_monte_carlo_properties(base_additives):
    """Тест 5: На 200 случайных входах (seed=0) каждый успешный рецепт строго соблюдает все нормативы."""
    rng = random.Random(0)

    for _ in range(200):
        # Случайные свойства компонентов
        s_godt = rng.uniform(7.0, 9.5)
        flash_godt = rng.uniform(62.0, 72.0)
        cfpp_godt = rng.uniform(-10.0, -3.0)
        d15_godt = rng.uniform(830.0, 840.0)
        t95_godt = rng.uniform(342.0, 355.0)
        cn_godt = rng.uniform(51.0, 56.0)

        c_godt = BlendComponent("GODT", 60000.0, 5000.0, 0.50, 1.0, s_godt, d15_godt, flash_godt, cfpp_godt, t95_godt, cn_godt)
        c_kero = BlendComponent("Kerosene", 85000.0, 1000.0, 0.0, 0.20, 2.0, 785.0, 42.0, -45.0, 240.0, 42.0)
        c_gasoil = BlendComponent("Gasoil", 50000.0, 1500.0, 0.0, 0.20, 8.0, 860.0, 90.0, 0.0, 365.0, 45.0)

        problem = BlendProblem(
            components=(c_godt, c_kero, c_gasoil),
            additives=base_additives,
            batch_t=2000.0,
            target_cfpp=-15.0,
            sulfur_max=9.5,
            density_bounds=(821.25, 843.75),
            flash_min=56.0,
            t95_max=358.0,
            cn_min=51.5,
        )
        res = solve_blend(problem)
        if res.success:
            total_vol = sum(res.shares.values())
            assert total_vol == pytest.approx(1.0, abs=1e-4)
            assert res.expected_sulfur <= 9.5 + 1e-4
            assert 821.25 - 1e-4 <= res.expected_density <= 843.75 + 1e-4
            assert res.expected_flash >= 56.0 - 1e-4
            assert res.expected_cfpp <= -15.0 + 1e-4
            assert res.expected_t95 <= 358.0 + 1e-4
            assert res.expected_cetane >= 51.5 - 1e-4


def test_component_tank_mass_weighted_sulfur_mixing():
    """Тест 6: ComponentTank.receive: сера смешивается строго по массе потоков."""
    tank = ComponentTank(
        name="GODT",
        stock_t=1000.0,
        props={"S_ppm": 8.0, "D15": 836.0, "Flash": 68.0, "CFPP": -6.0, "T95": 347.0, "CN": 53.0},
    )

    # Принимаем 100 т потока с серой 12.0 ppm за 1 час (flow=100 т/ч, dt=1.0 ч)
    tank.receive(flow_tph=100.0, props_in={"S_ppm": 12.0}, dt_h=1.0)

    # (1000 * 8.0 + 100 * 12.0) / 1100 = 9200 / 1100 = 8.3636 ppm
    assert tank.stock_t == pytest.approx(1100.0)
    assert tank.props["S_ppm"] == pytest.approx(8.36, abs=0.02)
