"""Unit tests for Blending v2 module (P2.4 - P2.6).

Tests:
- build_blend_problem with QualityEstimate and chance bounds
- solve_blend on nominal conditions (FEASIBLE)
- solve_elastic on tank farm component deficit (INFEASIBLE_ELASTIC)
- godt_prices finite difference shadow prices
- certify_blend certificate generation for both FEASIBLE and INFEASIBLE_ELASTIC cases
"""

import pytest
from src.agents.blending import (
    BlendComponent,
    BlendProblem,
    build_blend_problem,
    certify_blend,
    get_default_tanks,
    godt_prices,
    solve_blend,
    solve_elastic,
)
from src.agents.contracts import BlendingCertificate, Provenance, ProvenanceKind, QualityEstimate
from src.agents.policy import PolicyConfig


def test_build_blend_problem_nominal():
    tanks = get_default_tanks()
    prov = Provenance(kind=ProvenanceKind.ASSUMPTION, ref="unit_test")
    godt_estimates = {
        "GODT.S": QualityEstimate(
            stream="GODT",
            prop="S",
            value=8.5,
            domain="log",
            sigma_meas=0.15,
            sigma_calib=0.05,
            calib_age_h=0.5,
            anchor="PAK+bias",
            provenance=prov,
        ),
        "GODT.FLASH": QualityEstimate(
            stream="GODT",
            prop="FLASH",
            value=68.0,
            domain="linear",
            sigma_meas=4.0,
            sigma_calib=1.0,
            calib_age_h=0.5,
            anchor="PAK+bias",
            provenance=prov,
        ),
        "GODT.T95": QualityEstimate(
            stream="GODT",
            prop="T95",
            value=345.0,
            domain="linear",
            sigma_meas=3.0,
            sigma_calib=1.0,
            calib_age_h=0.5,
            anchor="LIMS",
            provenance=prov,
        ),
        "GODT.D15": QualityEstimate(
            stream="GODT",
            prop="D15",
            value=835.0,
            domain="linear",
            sigma_meas=1.0,
            sigma_calib=0.5,
            calib_age_h=0.5,
            anchor="LIMS",
            provenance=prov,
        ),
        "GODT.CFPP": QualityEstimate(
            stream="GODT",
            prop="CFPP",
            value=-6.0,
            domain="linear",
            sigma_meas=1.0,
            sigma_calib=0.5,
            calib_age_h=0.5,
            anchor="LIMS",
            provenance=prov,
        ),
        "GODT.CN": QualityEstimate(
            stream="GODT",
            prop="CN",
            value=53.0,
            domain="linear",
            sigma_meas=0.5,
            sigma_calib=0.2,
            calib_age_h=0.5,
            anchor="LIMS",
            provenance=prov,
        ),
        "GODT.E360": QualityEstimate(
            stream="GODT",
            prop="E360",
            value=96.5,
            domain="linear",
            sigma_meas=0.5,
            sigma_calib=0.2,
            calib_age_h=0.5,
            anchor="LIMS",
            provenance=prov,
        ),
    }

    problem = build_blend_problem(godt_estimates, tanks)
    assert len(problem.components) == 3
    assert len(problem.additives) == 4

    godt_comp = problem.components[0]
    assert godt_comp.name == "GODT"
    # Chance constraints applied: UCB for S, T95; LCB for Flash, E360
    assert godt_comp.s_ppm > 8.5
    assert godt_comp.flash_c < 68.0
    assert godt_comp.t95_c > 345.0
    assert godt_comp.e360_pct < 96.5


def test_solve_blend_nominal_feasible():
    tanks = get_default_tanks()
    godt_data = {
        "S_ppm": 8.0,
        "Flash": 67.0,
        "CFPP": -6.0,
        "D15": 836.0,
        "T95": 345.0,
        "CN": 53.0,
        "E360": 96.0,
    }
    problem = build_blend_problem(godt_data, tanks)
    res = solve_blend(problem)

    assert res.success is True
    assert res.status == "FEASIBLE"
    assert res.is_feasible is True
    assert res.shares["GODT"] >= 0.50
    assert res.expected_sulfur <= 10.0
    assert res.expected_flash >= 55.0
    assert res.expected_cfpp <= -15.0
    assert res.cost_per_ton > 0.0


def test_solve_elastic_component_deficit():
    tanks = get_default_tanks()
    # Deplete kerosene and gasoil tanks to 0
    tanks["Kerosene"].stock_t = 0.0
    tanks["Gasoil"].stock_t = 0.0

    # GODT with poor CFPP (-2 °C), needing impossible depressor depression without kerosene
    godt_data = {
        "S_ppm": 8.0,
        "Flash": 67.0,
        "CFPP": -2.0,
        "D15": 836.0,
        "T95": 345.0,
        "CN": 53.0,
        "E360": 96.0,
    }
    problem = build_blend_problem(godt_data, tanks)
    res = solve_blend(problem)

    assert res.status == "INFEASIBLE_ELASTIC"
    assert res.success is False
    assert res.is_feasible is False
    assert "CFPP_Target" in res.elastic_violations
    assert res.elastic_violations["CFPP_Target"] > 0.0


def test_godt_prices_finite_differences():
    tanks = get_default_tanks()
    godt_data = {
        "S_ppm": 8.0,
        "Flash": 67.0,
        "CFPP": -6.0,
        "D15": 836.0,
        "T95": 345.0,
        "CN": 53.0,
        "E360": 96.0,
    }
    prices = godt_prices(godt_data, tanks)
    assert len(prices) == 6

    props = [p.prop for p in prices]
    expected_props = ["GODT.S", "GODT.E360", "GODT.FLASH", "GODT.CFPP", "GODT.CN", "GODT.D15"]
    assert props == expected_props

    for p in prices:
        assert p.method == "finite_difference"
        assert isinstance(p.rub_per_unit_per_t, float)
        assert p.step > 0.0


def test_certify_blend():
    tanks = get_default_tanks()
    godt_data = {
        "S_ppm": 8.0,
        "Flash": 67.0,
        "CFPP": -6.0,
        "D15": 836.0,
        "T95": 345.0,
        "CN": 53.0,
        "E360": 96.0,
    }
    cert = certify_blend(
        cand="cand_test_1",
        godt_forecast=godt_data,
        tanks=tanks,
        hold_cost=60500.0,
        product_tph=215.0,
    )
    assert isinstance(cert, BlendingCertificate)
    assert cert.status == "FEASIBLE"
    assert cert.candidate == "cand_test_1"
    assert cert.cost_rub_per_t > 0.0
    assert len(cert.prices) == 6
    assert sum(cert.shares.values()) == pytest.approx(1.0, abs=1e-3)
