"""Unit tests for SupplyAgent (P2.5)."""

from __future__ import annotations

import pytest

from src.agents.contracts import (
    Candidate,
    CandidateOrigin,
    ConstraintStatus,
    Prediction,
)
from src.agents.context import build_default_context
from src.agents.scenarios import scenario_1_normal_tags
from src.agents.supply import SupplyAgent


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


def test_supply_certify_nominal():
    """Тест 1: Номинальный сбалансированный режим (F_avt ~ F_9) проходит сертификацию T3."""
    agent = SupplyAgent()
    cand = _make_candidate({"HT_FEED_SP": 0.0})
    tags = scenario_1_normal_tags()

    # Сбалансированные расходы: F9 = 220, F_avt = 220
    ss = {
        "HT_F9": 220.0,
        "AVT_F30": 120.0,
        "AVT_F32": 100.0,
        "AVT_DIESEL_TPH": 220.0,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", ss)
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "ADMISSIBLE"
    assert all(e.status == ConstraintStatus.SATISFIED for e in cert.evaluations)


def test_supply_inventory_min_deficit_and_repair():
    """Тест 2: Превышение отбора ГО над выработкой АВТ ведет к дефициту буфера (< -300 т) и ремонту."""
    agent = SupplyAgent()
    cand = _make_candidate({"HT_FEED_SP": 40.0})
    tags = scenario_1_normal_tags()

    # Подача ГО 260 т/ч, выработка АВТ 210 т/ч -> дисбаланс -50 т/ч
    # За 8 часов: 0 + (-50) * 8 = -400 т < -300 т
    ss = {
        "HT_F9": 260.0,
        "AVT_F30": 110.0,
        "AVT_F32": 100.0,
        "AVT_DIESEL_TPH": 210.0,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", {"HT_F9": 210.0, "AVT_DIESEL_TPH": 210.0})
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "VIOLATED"
    assert any(e.spec_key == "BUFFER.INVENTORY_MIN" and e.status == ConstraintStatus.VIOLATED for e in cert.evaluations)

    # Ремонт должен предложить снижение подачи сырья
    assert cert.repair is not None
    assert cert.repair.delta_u.get("HT_FEED_SP", 40.0) < 40.0


def test_supply_inventory_max_overflow_and_repair():
    """Тест 3: Недогрузка ГО относительно АВТ ведет к переполнению буфера (> +300 т) и ремонту."""
    agent = SupplyAgent()
    cand = _make_candidate({"HT_FEED_SP": -50.0})
    tags = scenario_1_normal_tags()

    # Подача ГО 160 т/ч, выработка АВТ 210 т/ч -> дисбаланс +50 т/ч
    # За 8 часов: 0 + (+50) * 8 = +400 т > +300 т
    ss = {
        "HT_F9": 160.0,
        "AVT_F30": 110.0,
        "AVT_F32": 100.0,
        "AVT_DIESEL_TPH": 210.0,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", {"HT_F9": 210.0, "AVT_DIESEL_TPH": 210.0})
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "VIOLATED"
    assert any(e.spec_key == "BUFFER.INVENTORY_MAX" and e.status == ConstraintStatus.VIOLATED for e in cert.evaluations)

    # Ремонт должен предложить увеличение подачи сырья
    assert cert.repair is not None
    assert cert.repair.delta_u.get("HT_FEED_SP", -50.0) > -50.0


def test_supply_long_run_ratio():
    """Тест 4: Превышение допустимого баланса F9 > F9_max_allowed фиксируется в BUFFER.LONG_RUN_RATIO."""
    agent = SupplyAgent()
    cand = _make_candidate({"HT_FEED_SP": 30.0})
    tags = scenario_1_normal_tags()

    # При запасе 0 т и I_min = -300 т, I_avail = 300 т
    # F9_max_allowed = F_avt + 300 / 8 = 200 + 37.5 = 237.5 т/ч
    # Если F9_ss = 250 т/ч > 237.5 т/ч -> ratio = 250 / 237.5 = 1.05 > 1.0
    ss = {
        "HT_F9": 250.0,
        "AVT_F30": 100.0,
        "AVT_F32": 100.0,
        "AVT_DIESEL_TPH": 200.0,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", {"HT_F9": 200.0, "AVT_DIESEL_TPH": 200.0})
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert any(e.spec_key == "BUFFER.LONG_RUN_RATIO" and e.status == ConstraintStatus.VIOLATED for e in cert.evaluations)
