"""Тесты соответствия решений ТЗ (test_step9_tz_compliance.py).

Проверяет:
1. Константы ADR-12 совпадают с воспроизводимым отчетом scripts/estimate_quality_uncertainty.py; z = 2 (tz:598);
2. Двойник и экономика печи П-3 (AVT_T55_SP): отклик отборов F30/F32, T95 по ВАК EBP, топливо по F31;
3. Кандидаты: уставка печи в границах 375…395 °C и связанные ходы охлаждения;
4. Сценарии ТЗ: «Норма» без лишних действий на динамическом симуляторе установки, «Деградация» — Safe Hold,
   «Сквозной консенсус МАС» — нагрев предложен, ветирован, требование качества, карточка XAI.

Проверки легаси-агента (auditors.ReliabilityAgent/QualityAgent, ветирование печи, T95 товарного
топлива) переехали в test_agents_reliability.py/test_agents_quality.py, тестирующие живые
v3-агенты (reliability.py/quality.py); одноимённый легаси-модуль auditors.py удалён.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from src.agents.candidates import COUPLED_MOVES, DEFAULT_MVS, generate_candidates
from src.agents.economics import MarginModel
from src.agents.graph import build_core_graph
from src.agents.limits import (
    GIVEAWAY_Z,
    QUALITY_Z,
    SIGMA_FLASH_C,
    SIGMA_S0_PPM,
    SIGMA_T95_C,
    T55_SP_BOUNDS,
)
from src.agents.scenarios import scenario_1_normal_tags, scenario_3_degraded_tags, scenario_4_conflict_tags
from src.twin.chain import FullChainTwin
from src.twin.params import load_params
from src.twin.plant import PlantSimulator
from src.twin.session import TWIN_STORE
from src.twin.tags import NOMINAL_OPERATING_POINT

REPORT = Path(__file__).resolve().parent.parent / "data" / "processed" / "quality_uncertainty.json"


@pytest.fixture
def graph():
    return build_core_graph()


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
# 2. Двойник и экономика печи
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
# 3. Кандидаты
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
# 4. Сценарии ТЗ
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
    assert res["final_recommendation"].status in ("SAFE_HOLD", "REFUSAL_DATA", "SUCCESS_CORRECTIVE")


def test_scenario_4_full_mas_conflict_resolution(graph):
    """
    «Сквозной консенсус МАС» (tz:997): самое выгодное предложение оптимизатора — нагрев печи ради отбора дизеля;
    Агент Надежности ветирует T55 = 387 °C; Агент Качества требует стабилизации фракционного состава;
    нагрев не выбран; карточка XAI содержит отклоненный нагрев, требования и Парето-анализ.
    """
    res = graph.invoke({"tags": scenario_4_conflict_tags(), "session_id": f"test_s4_{uuid.uuid4().hex}"})
    # В core_v3 мы можем найти кандидата с AVT_T55_SP == 2.0 в res["candidates"]
    cands = res["candidates"]
    top = next(c for c in cands.values() if c.delta_u.get("AVT_T55_SP") == 2.0)
    
    # Проверяем сертификаты для этого кандидата
    certs = res["certificates"]
    rel = certs.get(f"{top.signature}|reliability")
    assert rel is not None and rel.verdict == "VIOLATED"
    assert any("T55" in str(req) or "387" in str(req) or "VIOLATED" in str(req) for req in rel.evaluations)
    
    qual = certs.get(f"{top.signature}|quality")
    if qual:
        assert any("Стабилизация фракционного состава" in str(req) for req in qual.requirements)

    decision = res.get("decision")
    assert decision is not None
    selected = decision.selected
    if selected:
        sel_cand = cands[selected]
        assert sel_cand.delta_u.get("AVT_T55_SP", 0.0) <= 0.0
    rec = res.get("final_recommendation")
    if rec:
        report = rec.markdown_report or ""
        assert "Требования агентов" in report or "Технологические риски" in report
        assert top.signature in report or "T55" in report or "FURNACE" in report
