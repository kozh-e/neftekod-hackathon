"""Unit tests for ReliabilityAgent (P2.2)."""

from __future__ import annotations

import pytest

from src.agents.contracts import (
    Candidate,
    CandidateOrigin,
    ConstraintStatus,
    Prediction,
)
from src.agents.context import build_default_context
from src.agents.reliability import ReliabilityAgent
from src.agents.scenarios import scenario_1_normal_tags


def _make_candidate(delta_u: dict[str, float], origin: CandidateOrigin = CandidateOrigin.LOCAL) -> Candidate:
    return Candidate(
        signature="cand_" + "_".join(f"{k}{v}" for k, v in sorted(delta_u.items())),
        delta_u=delta_u,
        origin=origin,
        proposed_by="optimizer",
    )


def _make_prediction(cand_sig: str, ss: dict[str, float], extrema: dict[str, tuple[float, float, int]] | None = None) -> Prediction:
    ext = extrema or {k: (v, v, 0) for k, v in ss.items()}
    return Prediction(
        candidate=cand_sig,
        horizon_steps=60,
        steady_state=ss,
        trajectory_extrema=ext,
    )


def test_reliability_certify_nominal():
    agent = ReliabilityAgent()
    cand = _make_candidate({"HT_FEED_SP": 1.0})
    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 378.0
    tags["AVT_F31"] = 380.0

    ss = {
        "AVT_T55": 378.0,
        "HT_DP_KPA": 180.0,
        "HT_T_OUT": 370.0,
        "HT_GOR": 220.0,
        "HT_FEED_TO_AVT": 1.0,
    }
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", ss)
    ctx = build_default_context(tags=tags)

    cert = agent.certify(cand, pred, hold_pred, ctx)
    assert cert.verdict == "ADMISSIBLE"
    assert cert.violation_by_tier.get(1, 0.0) == 0.0


def test_reliability_furnace_preconditions_fail_closed():
    """E1: При AVT_F31 < 362.5 или AVT_P52 < 0.10 ход печью AVT_T55_SP запрещен fail-closed."""
    agent = ReliabilityAgent()
    cand_furnace = _make_candidate({"AVT_T55_SP": 1.0})
    cand_ht_only = _make_candidate({"HT_FEED_SP": -2.0})

    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 378.0
    tags["AVT_F31"] = 350.0  # Нарушено предусловие F31 (350 < 362.5)

    ss = {"AVT_T55": 379.0, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}
    pred_f = _make_prediction(cand_furnace.signature, ss)
    pred_ht = _make_prediction(cand_ht_only.signature, ss)
    hold_pred = _make_prediction("hold", ss)

    ctx = build_default_context(tags=tags)

    cert_f = agent.certify(cand_furnace, pred_f, hold_pred, ctx)
    assert cert_f.verdict == "VIOLATED"
    assert any("FURNACE.F31_MIN" in e.spec_key and e.status == ConstraintStatus.VIOLATED for e in cert_f.evaluations)

    # Ход только гидроочисткой не зависит от печи и должен проходить
    cert_ht = agent.certify(cand_ht_only, pred_ht, hold_pred, ctx)
    assert cert_ht.verdict == "ADMISSIBLE"


def test_reliability_cot_policy_warm():
    """Нагрев печи при T55 >= 380 °C запрещен политикой FURNACE.COT_POLICY_WARM."""
    agent = ReliabilityAgent()
    cand_heat = _make_candidate({"AVT_T55_SP": 1.5})
    cand_cool = _make_candidate({"AVT_T55_SP": -1.5})

    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 382.0
    tags["AVT_F31"] = 380.0

    ss_heat = {"AVT_T55": 383.5, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}
    ss_cool = {"AVT_T55": 380.5, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}

    pred_heat = _make_prediction(cand_heat.signature, ss_heat)
    pred_cool = _make_prediction(cand_cool.signature, ss_cool)
    hold_pred = _make_prediction("hold", {"AVT_T55": 382.0, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0})

    ctx = build_default_context(tags=tags)

    cert_heat = agent.certify(cand_heat, pred_heat, hold_pred, ctx)
    assert cert_heat.verdict == "VIOLATED"
    assert any("COT_POLICY" in e.spec_key for e in cert_heat.evaluations)

    # Охлаждение разрешено
    cert_cool = agent.certify(cand_cool, pred_cool, hold_pred, ctx)
    assert cert_cool.verdict == "ADMISSIBLE"


def test_reliability_dp_max_multiplicative_clogging():
    """При высоком перепаде разгрузка допустима и улучшает запас, а загрузка отвергается."""
    agent = ReliabilityAgent()
    cand_load = _make_candidate({"HT_FEED_SP": 5.0})
    cand_unload = _make_candidate({"HT_FEED_SP": -5.0})

    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 378.0
    tags["AVT_F31"] = 380.0
    tags["HT_DP_KPA"] = 470.0  # > предела 454.5 кПа

    # При замеренном перепаде 470 кПа > предела 454.5 кПа
    ss_load = {"HT_DP_KPA": 485.0, "AVT_T55": 378.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}
    ss_unload = {"HT_DP_KPA": 440.0, "AVT_T55": 378.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}
    ss_hold = {"HT_DP_KPA": 470.0, "AVT_T55": 378.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}

    pred_load = _make_prediction(cand_load.signature, ss_load)
    pred_unload = _make_prediction(cand_unload.signature, ss_unload)
    hold_pred = _make_prediction("hold", ss_hold)

    ctx = build_default_context(tags=tags)

    cert_load = agent.certify(cand_load, pred_load, hold_pred, ctx)
    assert cert_load.verdict == "VIOLATED"

    cert_unload = agent.certify(cand_unload, pred_unload, hold_pred, ctx)
    assert cert_unload.verdict == "ADMISSIBLE"


def test_reliability_local_repair():
    """Проверка генерации локального ремонта при нарушении."""
    agent = ReliabilityAgent()
    cand = _make_candidate({"AVT_T55_SP": 3.0})
    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 385.0
    tags["AVT_F31"] = 380.0

    ss = {"AVT_T55": 388.0, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0}
    pred = _make_prediction(cand.signature, ss)
    hold_pred = _make_prediction("hold", {"AVT_T55": 385.0, "HT_DP_KPA": 180.0, "HT_T_OUT": 370.0, "HT_GOR": 220.0, "HT_FEED_TO_AVT": 1.0})

    ctx = build_default_context(tags=tags)
    cert = agent.certify(cand, pred, hold_pred, ctx)

    assert cert.verdict == "VIOLATED"
    assert cert.repair is not None
    assert cert.repair.delta_u["AVT_T55_SP"] < 3.0
