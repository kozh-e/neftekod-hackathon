"""Тесты соответствия решений ТЗ (test_step9_tz_compliance.py).

Проверяет:
1. Константы ADR-12 совпадают с воспроизводимым отчетом scripts/estimate_quality_uncertainty.py; z = 2 (tz:598);
2. Агент Надежности: вето нагрева печи внутри зоны предупреждения (антипаттерн 3) и вето T55 ≥ 386.4;
3. Агент Качества: T95 проверяется на товарном топливе через рецепт с запасом 2σ, гидрогенизат по T95 не ветируется;
   требование стабилизации фракционного состава для ходов печью;
4. Двойник и экономика печи П-3 (AVT_T55_SP): отклик отборов F30/F32, T95 по ВАК EBP, топливо по F31;
5. Кандидаты: уставка печи в границах 375…395 °C и связанные ходы охлаждения;
6. Сценарии ТЗ: «Норма» без лишних действий на динамическом симуляторе установки, «Деградация» — Safe Hold,
   «Сквозной консенсус МАС» — нагрев предложен, ветирован, требование качества, карточка XAI.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from src.agents.auditors import QualityAgent, ReliabilityAgent
from src.agents.candidates import COUPLED_MOVES, DEFAULT_MVS, generate_candidates
from src.agents.economics import MarginModel
from src.agents.graph import build_mvp_graph
from src.agents.limits import (
    GIVEAWAY_Z,
    QUALITY_Z,
    SIGMA_FLASH_C,
    SIGMA_S0_PPM,
    SIGMA_T95_C,
    T55_SP_BOUNDS,
)
from src.agents.optimization import RolloutOptimizationAgent
from src.agents.scenarios import scenario_1_normal_tags, scenario_3_degraded_tags, scenario_4_conflict_tags
from src.agents.state import ControlCandidate
from src.agents.tanks import ComponentTank
from src.twin.chain import FullChainTwin
from src.twin.params import load_params
from src.twin.plant import PlantSimulator
from src.twin.session import TWIN_STORE
from src.twin.tags import NOMINAL_OPERATING_POINT

REPORT = Path(__file__).resolve().parent.parent / "data" / "processed" / "quality_uncertainty.json"


@pytest.fixture
def graph():
    return build_mvp_graph()


@pytest.fixture
def twin():
    t = FullChainTwin(copy.deepcopy(load_params()))
    t.initialize(NOMINAL_OPERATING_POINT)
    return t


# =============================================================================
# 1. Константы ADR-12
# =============================================================================

def test_quality_constants_follow_tz_and_estimation_report():
    """z = 2 по промпту Агента Качества; σ и граница переочистки совпадают с отчетом оценки по архиву."""
    assert QUALITY_Z == 2.0
    if not REPORT.exists():
        pytest.skip("Отчет data/processed/quality_uncertainty.json не сформирован (нужны архивы телеметрии)")
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    assert SIGMA_S0_PPM == pytest.approx(rep["sulfur"]["sigma_train"], abs=0.005)
    assert SIGMA_FLASH_C == pytest.approx(rep["flash"]["sigma_train"], abs=0.005)
    assert SIGMA_T95_C == pytest.approx(rep["t95"]["sigma0_backed_out"], abs=0.005)
    assert GIVEAWAY_Z == pytest.approx(rep["giveaway"]["z_min"], abs=0.005)
    p = load_params()
    assert p.feed.dF30_dT55 == pytest.approx(rep["furnace"]["dF30_dT55_tph_per_C"], abs=0.001)
    assert p.feed.dF32_dT55 == pytest.approx(rep["furnace"]["dF32_dT55_tph_per_C"], abs=0.001)
    assert p.economics.f31_ref == pytest.approx(rep["furnace"]["f31_median_tph"], abs=0.1)


# =============================================================================
# 2. Агент Надежности: печь П-3
# =============================================================================

def _furnace_cand(cid: str, t55: float, du: float) -> ControlCandidate:
    return ControlCandidate(candidate_id=cid, delta_u={"AVT_T55_SP": du}, expected_t55=t55)


def test_reliability_vetoes_heating_inside_warning_zone():
    """Антипаттерн 3 ТЗ: нагрев печи с итогом > 380 °C ветируется даже ниже буфера 386.4; охлаждение — нет."""
    hold = ControlCandidate(candidate_id="cand_hold", is_hold=True, expected_t55=381.7)
    heat = ReliabilityAgent.audit(_furnace_cand("cand_heat", 383.7, +2.0), hold_cand=hold)
    assert heat.is_vetoed and "AVT_T55_WARNING_ZONE" in heat.violated_limits
    assert "антипаттерн 3" in heat.violation_reason

    cool = ReliabilityAgent.audit(_furnace_cand("cand_cool", 379.7, -2.0), hold_cand=hold)
    assert not cool.is_vetoed

    low_hold = ControlCandidate(candidate_id="cand_hold", is_hold=True, expected_t55=376.0)
    low_heat = ReliabilityAgent.audit(_furnace_cand("cand_heat_low", 378.0, +2.0), hold_cand=low_hold)
    assert not low_heat.is_vetoed  # ниже зоны предупреждения нагрев допустим


def test_reliability_vetoes_coil_overheat_as_in_scenario_4():
    """Сценарий 4 ТЗ: T55 = 387 °C превышает буфер 386.4 °C — вето с числами в причине."""
    hold = ControlCandidate(candidate_id="cand_hold", is_hold=True, expected_t55=385.0)
    rep = ReliabilityAgent.audit(_furnace_cand("cand_heat", 387.0, +2.0), hold_cand=hold)
    assert rep.is_vetoed and "AVT_T55" in rep.violated_limits
    assert "T55=387.00" in rep.violation_reason


# =============================================================================
# 3. Агент Качества: T95 товарного топлива
# =============================================================================

def _quality_cand(cid: str, t95: float, sulfur: float = 7.5) -> ControlCandidate:
    return ControlCandidate(
        candidate_id=cid,
        horizon_steps=6,
        steady_state={"HT_S_PRODUCT": sulfur, "HT_FLASH": 68.0, "HT_T95_PRODUCT": t95, "HT_FEED_SP": 219.6,
                      "HT_D15_PRODUCT": 836.0, "HT_CFPP_PRODUCT": -6.0, "HT_CN_PRODUCT": 53.75},
    )


def test_hydrotreated_t95_not_vetoed_when_blend_is_feasible():
    """T95 гидрогенизата выше 360 − 2σ не ветируется: норматив проверяется у товарного топлива через рецепт."""
    rep = QualityAgent.audit(_quality_cand("cand_x", 356.0), lims_age_hours=2.0)
    assert "HT_T95_PRODUCT" not in rep.violated_limits
    assert "BLEND_FEASIBILITY" not in rep.violated_limits

    # Запас T95 товарного топлива уменьшается ровно на рост 2σ(age): 2·σ0·(√(1+24/12) − 1)
    fresh = QualityAgent.audit(_quality_cand("cand_x", 356.0), lims_age_hours=0.0)
    stale = QualityAgent.audit(_quality_cand("cand_x", 356.0), lims_age_hours=24.0)
    shrink = fresh.limit_margins["BLEND_T95_GODT_UCB"] - stale.limit_margins["BLEND_T95_GODT_UCB"]
    assert shrink == pytest.approx(2.0 * SIGMA_T95_C * (3.0 ** 0.5 - 1.0), abs=1e-3)


def test_blend_t95_veto_uses_two_sigma_on_hydrotreated_forecast():
    """Без керосина рецепт невыполним, если T95 ГО ДТ + 2σ > 360 °C; при запасе — выполним."""
    tanks = {
        "GODT": ComponentTank(name="GODT", stock_t=5000.0, props={"S_ppm": 7.5, "D15": 836.0, "Flash": 68.0, "CFPP": -6.0, "T95": 358.0, "CN": 53.75}),
        "Kerosene": ComponentTank(name="Kerosene", stock_t=0.0, props={}),
        "Gasoil": ComponentTank(name="Gasoil", stock_t=0.0, props={}),
    }
    bad = QualityAgent.audit(_quality_cand("cand_bad", 358.0), lims_age_hours=0.0, tanks=tanks)
    assert "BLEND_FEASIBILITY" in bad.violated_limits

    tanks_ok = copy.deepcopy(tanks)
    tanks_ok["GODT"].props["T95"] = 350.0
    good = QualityAgent.audit(_quality_cand("cand_good", 350.0), lims_age_hours=0.0, tanks=tanks_ok)
    assert "BLEND_FEASIBILITY" not in good.violated_limits


def test_quality_states_fractional_stabilization_requirement_for_furnace_moves(twin):
    """Ход печью меняет T95 ГО ДТ: Агент Качества формулирует требование стабилизации фракционного состава."""
    cands, _ = RolloutOptimizationAgent().propose(twin)
    hold = next(c for c in cands if c.is_hold)
    heat = next(c for c in cands if c.delta_u == {"AVT_T55_SP": 2.0})
    rep = QualityAgent.audit(heat, hold_cand=hold, lims_age_hours=2.0)
    assert any("Стабилизация фракционного состава" in r for r in rep.requirements)
    feed = next(c for c in cands if c.delta_u == {"HT_FEED_SP": 5.0})
    assert QualityAgent.audit(feed, hold_cand=hold, lims_age_hours=2.0).requirements == []


# =============================================================================
# 4. Двойник и экономика печи
# =============================================================================

def test_twin_furnace_setpoint_response(twin):
    """+2 °C T55: отборы F30/F32 по архиву, T95 сырья по официальной ВАК EBP (2.66463), T55 = уставка."""
    p = twin.params
    u0 = twin.u_current
    ss0 = twin.steady_state(u0)
    ss1 = twin.steady_state({**u0, "AVT_T55_SP": u0["AVT_T55_SP"] + 2.0})
    assert ss1["AVT_T55"] == pytest.approx(u0["AVT_T55_SP"] + 2.0)
    assert ss1["AVT_DIESEL_TPH"] - ss0["AVT_DIESEL_TPH"] == pytest.approx(2.0 * (p.feed.dF30_dT55 + p.feed.dF32_dT55), abs=1e-9)
    assert ss1["HT_T95_FEED"] - ss0["HT_T95_FEED"] == pytest.approx(2.0 * p.feed.dF30_dT55 * p.feed.dT95_dF30, abs=1e-9)


def test_margin_model_values_furnace_move(twin):
    """Экономика хода печью: выручка = спред «прямогон − нефть» × Δдизеля; топливо = F31·cp·ΔT/η."""
    p = twin.params
    u0 = twin.u_current
    u1 = {**u0, "AVT_T55_SP": u0["AVT_T55_SP"] + 2.0}
    mb = MarginModel(p.economics, p.reactor).evaluate(twin.steady_state(u1), twin.steady_state(u0), u1, u0)
    delta_diesel = 2.0 * (p.feed.dF30_dT55 + p.feed.dF32_dT55)
    assert mb.avt_diesel == pytest.approx(p.economics.crude_to_straight_spread * delta_diesel, abs=0.01)
    fuel_mwh = p.economics.f31_ref * 1000.0 * p.economics.cp_oil * 2.0 / (3.6e6 * p.economics.eta_furnace)
    assert mb.furnace == pytest.approx(p.economics.fuel_rub_mwh * fuel_mwh, rel=1e-3)


# =============================================================================
# 5. Кандидаты
# =============================================================================

def test_furnace_candidates_and_coupled_cooling_moves():
    """Уставка печи в границах ТЗ (375 … ПАЗ 395); связанные ходы охлаждения с ходами гидроочистки; ≤ 25."""
    mv = next(m for m in DEFAULT_MVS if m.name == "AVT_T55_SP")
    assert (mv.lo, mv.hi) == T55_SP_BOUNDS == (375.0, 395.0)
    moves = generate_candidates({**NOMINAL_OPERATING_POINT, "AVT_T55_SP": 381.7, "HT_FEED_SP": 219.6, "HT_TIN_SP": 363.3,
                                 "HT_P_SP": 3.922, "HT_GOR_SP": 360.0})
    deltas = [du for _, du in moves]
    assert {"AVT_T55_SP": 2.0} in deltas and {"AVT_T55_SP": -2.0} in deltas
    cooling_pairs = [cm for cm in COUPLED_MOVES if cm.get("AVT_T55_SP") == -1.0]
    assert len(cooling_pairs) == 5
    assert len(moves) <= 25


# =============================================================================
# 6. Сценарии ТЗ
# =============================================================================

def test_scenario_1_normal_no_excess_actions_and_no_furnace_heating(graph):
    """«Норма» (tz:988) на динамическом симуляторе: после выхода на оптимум ходов нет, печь не нагревается."""
    tags0 = scenario_1_normal_tags()
    plant = PlantSimulator(tags0, q21_noise_ppm=0.05, seed=7)
    sid = f"test_s1_{uuid.uuid4().hex}"

    def cycle() -> bool:
        res = graph.invoke({"tags": plant.measure(), "session_id": sid})
        rec = res["final_recommendation"]
        if rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)
            return True
        return False

    converge = 0
    while converge < 30 and cycle():
        converge += 1
    assert converge < 30
    actions = sum(cycle() for _ in range(30))
    assert actions <= 1
    assert plant.twin.u_current["AVT_T55_SP"] <= tags0["AVT_T55"] + 1e-9


def test_scenario_3_degraded_data_safe_hold(graph):
    """«Деградация данных» (tz:994): ЛИМС старше 24 ч -> Safe Hold, оптимизация и Парето не выполняются."""
    res = graph.invoke({"tags": scenario_3_degraded_tags(), "session_id": f"test_s3_{uuid.uuid4().hex}"})
    assert res["final_recommendation"].status == "SAFE_HOLD"
    assert res.get("pareto") is None


def test_scenario_4_full_mas_conflict_resolution(graph):
    """
    «Сквозной консенсус МАС» (tz:997): самое выгодное предложение оптимизатора — нагрев печи ради отбора дизеля;
    Агент Надежности ветирует T55 = 387 °C; Агент Качества требует стабилизации фракционного состава;
    нагрев не выбран; карточка XAI содержит отклоненный нагрев, требования и Парето-анализ.
    """
    res = graph.invoke({"tags": scenario_4_conflict_tags(), "session_id": f"test_s4_{uuid.uuid4().hex}"})
    cands = res["candidates"]
    top = max(cands, key=lambda c: c.expected_margin)
    assert top.delta_u == {"AVT_T55_SP": 2.0}
    assert top.margin_breakdown["avt_diesel"] > 0.0

    reports = [r for r in res["audit_reports"] if r.candidate_id == top.candidate_id]
    rel = next(r for r in reports if r.agent == "reliability")
    assert rel.is_vetoed and "T55=387.00" in rel.violation_reason
    qual = next(r for r in reports if r.agent == "quality")
    assert any("Стабилизация фракционного состава" in req for req in qual.requirements)
    assert not any("HT_S_PRODUCT" in v for v in qual.violated_limits)  # конфликт печной, не по сере

    rec = res["final_recommendation"]
    selected = res.get("selected_candidate")
    assert selected is None or selected.delta_u.get("AVT_T55_SP", 0.0) <= 0.0
    assert rec.status.startswith("SUCCESS") or rec.status.startswith("DEADBAND")
    report = rec.markdown_report or ""
    assert "Требования агентов" in report and "Парето-анализ" in report
    assert top.candidate_id in report  # отклоненный нагрев объяснен в карточке
    assert res["pareto"].point(top.candidate_id).status == "vetoed"
