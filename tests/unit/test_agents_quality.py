"""Unit tests for QualityAgent (P2.3)."""

from __future__ import annotations

import pytest

from src.agents.contracts import (
    Candidate,
    CandidateOrigin,
    ConstraintStatus,
    Prediction,
)
from src.agents.context import build_default_context
from src.agents.quality import QualityAgent
from src.agents.scenarios import scenario_1_normal_tags


def _make_candidate(delta_u: dict[str, float], origin: CandidateOrigin = CandidateOrigin.LOCAL) -> Candidate:
    return Candidate(
        signature="cand_" + "_".join(f"{k}{v}" for k, v in sorted(delta_u.items())),
        delta_u=delta_u,
        origin=origin,
        proposed_by="optimizer",
    )


def _make_prediction(cand_sig: str, ss: dict[str, float]) -> Prediction:
    return Prediction(
        candidate=cand_sig,
        horizon_steps=60,
        steady_state=ss,
        trajectory_extrema={k: (v, v, 0) for k, v in ss.items()},
    )


def test_quality_certify_nominal_and_forecasts():
    """Тест 1: Номинальный кандидат проходит сертификацию и публикует 7 прогнозов качества."""
    agent = QualityAgent()
    cand = _make_candidate({"HT_FEED_SP": 1.0, "HT_TIN_SP": 0.5})
    tags = scenario_1_normal_tags()
    tags["HT_Q21"] = 7.5  # S <= 10 ppm c учетом 2σ буфера

    ss = {
        "GODT.S": 7.5,
        "GODT.FLASH": 67.0,
        "GODT.T95": 347.0,
        "GODT.D15": 836.0,
        "GODT.CFPP": -6.0,
        "GODT.CN": 53.5,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", ss)
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "ADMISSIBLE"

    # Проверка публикации прогнозов качества для блендинга
    expected_props = {"GODT.S", "GODT.FLASH", "GODT.T95", "GODT.D15", "GODT.CFPP", "GODT.CN", "GODT.E360"}
    assert expected_props.issubset(set(cert.forecasts.keys()))

    # Проверка расчета E360 при T95 = 347 °C (должно быть >= 95 %)
    e360_est = cert.forecasts["GODT.E360"]
    assert e360_est.value >= 95.0
    assert e360_est.sigma_meas > 0.0


def test_quality_certify_sulfur_violation_and_repair():
    """Тест 2: При высокой сере (S > 10.0 ppm) выдается VIOLATED и предложение ремонта (нагрев реактора)."""
    agent = QualityAgent()
    cand = _make_candidate({"HT_FEED_SP": 5.0})
    tags = scenario_1_normal_tags()
    tags["HT_Q21"] = 10.5  # Высокая сера

    ss_viol = {
        "GODT.S": 10.8,
        "GODT.FLASH": 65.0,
        "GODT.T95": 348.0,
        "GODT.D15": 836.0,
        "GODT.CFPP": -6.0,
        "GODT.CN": 53.5,
    }
    pred = _make_prediction(cand.signature, ss_viol)
    hold_pred = _make_prediction("hold", {"GODT.S": 10.5, "GODT.FLASH": 65.0})
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "VIOLATED"
    assert any(e.spec_key == "GODT.S_MAX" and e.status == ConstraintStatus.VIOLATED for e in cert.evaluations)

    # Проверка предложения ремонта (увеличение HT_TIN_SP для снижения серы)
    assert cert.repair is not None
    assert cert.repair.delta_u.get("HT_TIN_SP", 0.0) > 0.0


def test_quality_furnace_move_integration():
    """Тест 3: Ход печью AVT_T55_SP включает требование стабилизации фракционного состава."""
    agent = QualityAgent()
    cand = _make_candidate({"AVT_T55_SP": -2.0})
    tags = scenario_1_normal_tags()
    tags["HT_Q21"] = 7.5

    ss_cand = {"GODT.S": 7.5, "GODT.FLASH": 66.0, "GODT.T95": 344.0}
    ss_hold = {"GODT.S": 7.5, "GODT.FLASH": 66.0, "GODT.T95": 347.0}

    pred = _make_prediction(cand.signature, ss_cand)
    hold_pred = _make_prediction("hold", ss_hold)
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    # Ход печью должен порождать требование по стабилизации фракционного состава
    assert any("фракционн" in req.lower() for req in cert.requirements)
