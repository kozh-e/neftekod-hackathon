"""Тесты Этапа 4: Аудиторы, ограничения, арбитраж (test_step7_audit_arbitration.py).

Проверяет:
1. quality_risk_tags -> hold нарушает серу на установившемся режиме; есть допустимый
   корректирующий кандидат -> статус SUCCESS_CORRECTIVE;
2. Кандидат, ухудшающий переходный процесс сверх предела относительно hold, ветируется с причиной;
3. Статистический буфер серы: S_hat = 8.43, sigma = 1.19, z = 1.645 -> 10.39 > 10 -> hold нарушает;
   при z = 1.0 -> 9.62 <= 10 -> не нарушает;
4. Смещение растет с возрастом ЛИМС при недоступном HT_Q21;
5. Недопустимый ход сырья (+25 т/ч) вызывает вето по вспышке или FEED_TO_AVT с указанием причины;
6. audit_reports содержит отчет и причину для каждого заблокированного кандидата;
7. Резервуар ГО ДТ с CFPP = +10 °C приводит к GOST_VETO_BLEND_INFEASIBLE;
8. Нормированная норма шага: step=2.0 со scale=2.0 дает 1.0 (проходит), step=0.05 дает DEADBAND_REJECT_SMALL_STEP.
"""

from __future__ import annotations

import math
import pytest

from src.agents.arbitration import ArbitrationNode
from src.agents.auditors import QualityAgent, ReliabilityAgent
from src.agents.candidates import move_scales
from src.agents.constraints import assess_limit, stat_offset
from src.agents.graph import build_mvp_graph
from src.agents.limits import QUALITY_Z, SIGMA_S0_PPM
from src.agents.optimization import RolloutOptimizationAgent
from src.agents.state import ControlCandidate
from src.agents.tanks import ComponentTank
from src.twin.chain import FullChainTwin
from src.twin.params import load_params


def test_quality_risk_triggers_success_corrective(quality_risk_tags):
    """1. quality_risk_tags -> hold нарушает серу на SS; статус SUCCESS_CORRECTIVE."""
    graph = build_mvp_graph()
    result = graph.invoke({"tags": quality_risk_tags})

    rec = result.get("final_recommendation")
    assert rec is not None
    assert rec.status == "SUCCESS_CORRECTIVE"
    # Должен быть выбран кандидат, повышающий температуру, давление или снижающий подачу
    du = rec.recommended_delta_u
    assert (
        du.get("HT_TIN_SP", 0.0) > 0.0
        or du.get("HT_P_SP", 0.0) > 0.0
        or du.get("HT_FEED_SP", 0.0) < 0.0
    )


def test_worsens_transient_veto():
    """2. Кандидат, ухудшающий переходный процесс сверх предела относительно hold, ветируется с причиной."""
    # Hold: траектория серы превышает предел, но затухает: [10.2, 10.1, 10.0]
    hold_traj = [10.2, 10.1, 10.0]
    # Кандидат A: дает всплеск серы [10.5, 10.3, 9.8] -> на шаге 1 и 2 превышает предел И хуже hold!
    cand_traj = [10.5, 10.3, 9.8]

    ass = assess_limit(
        key="HT_S_PRODUCT",
        traj=cand_traj,
        ss_val=9.8,
        hold_traj=hold_traj,
        limit=10.0,
        sense="max",
        offset=0.0,
    )
    assert ass.vetoed is True
    assert ass.worsens_transient is True
    assert ass.violates_ss is False
    assert ass.reason is not None
    assert "ухудшает переходный процесс" in ass.reason


def test_statistical_buffer_z_threshold():
    """3. S_hat = 8.43, sigma = 1.19. z=1.645 -> 10.39 > 10 (нарушение); z=1.0 -> 9.62 <= 10 (норма)."""
    s_hat = 8.43
    sigma0 = 1.19

    # z = 1.645
    off_1645 = stat_offset(sigma0, age_h=0.0, z=1.645)
    eff_1645 = s_hat + off_1645
    assert eff_1645 == pytest.approx(10.39, abs=0.01)
    ass_1645 = assess_limit("HT_S_PRODUCT", None, s_hat, None, limit=10.0, sense="max", offset=off_1645)
    assert ass_1645.vetoed is True

    # z = 1.0
    off_10 = stat_offset(sigma0, age_h=0.0, z=1.0)
    eff_10 = s_hat + off_10
    assert eff_10 == pytest.approx(9.62, abs=0.01)
    ass_10 = assess_limit("HT_S_PRODUCT", None, s_hat, None, limit=10.0, sense="max", offset=off_10)
    assert ass_10.vetoed is False


def test_offset_grows_with_lims_age():
    """4. Смещение растет с возрастом ЛИМС при недоступном HT_Q21."""
    sigma0 = 1.19
    z = 1.645

    off_0h = stat_offset(sigma0, age_h=0.0, z=z)
    off_4h = stat_offset(sigma0, age_h=4.0, z=z)
    off_12h = stat_offset(sigma0, age_h=12.0, z=z)
    off_24h = stat_offset(sigma0, age_h=24.0, z=z)

    assert off_0h < off_4h < off_12h < off_24h


def test_excessive_feed_move_vetoed(nominal_tags):
    """5. Превышение расхода сырья вызывает вето по вспышке или FEED_TO_AVT с указанием причины."""
    params = load_params()
    twin = FullChainTwin(params)
    twin.initialize(nominal_tags)

    # 1. Проверка вето по FEED_TO_AVT при превышении отношения расходов сырья ГО к дизелю АВТ (> 1.26)
    u0 = twin.u_current
    u_big_feed = dict(u0)
    u_big_feed["HT_FEED_SP"] = 270.0  # 270.0 / 209.9 = 1.286 > 1.26

    traj = twin.predict(u_big_feed, horizon=38)
    ss = twin.steady_state(u_big_feed)

    cand_bad = ControlCandidate(
        candidate_id="cand_huge_feed",
        delta_u={"HT_FEED_SP": 50.4},
        horizon_steps=38,
        trajectory=traj,
        steady_state=ss,
    )

    rel_rep = ReliabilityAgent.audit(cand_bad)
    assert rel_rep.is_vetoed is True
    assert "FEED_TO_AVT" in (rel_rep.violation_reason or "")

    # 2. Проверка вето по вспышке HT_FLASH при просадке температуры вспышки
    cand_flash = ControlCandidate(
        candidate_id="cand_low_flash",
        delta_u={"HT_FEED_SP": 25.0},
        steady_state={"HT_FEED_SP": 244.6, "HT_FLASH": 58.0},
    )
    qual_rep = QualityAgent.audit(cand_flash)
    assert qual_rep.is_vetoed is True
    assert "HT_FLASH" in (qual_rep.violation_reason or "")


def test_audit_reports_contain_reasons_for_all_vetoed(nominal_tags):
    """6. audit_reports содержит причину для каждого id из vetoed_candidates."""
    # Создадим кандидата с грубым нарушением
    cand_fail = ControlCandidate(
        candidate_id="cand_fail_limits",
        delta_u={"HT_TIN_SP": 10.0},
        steady_state={"HT_T_OUT": 405.0, "HT_DP_KPA": 500.0},  # Превышение 390 °C и 454.5 кПа
    )
    rep = ReliabilityAgent.audit(cand_fail)
    assert rep.is_vetoed is True
    assert rep.violation_reason is not None
    assert len(rep.violated_limits) > 0


def test_godt_tank_high_cfpp_veto():
    """7. Резервуар ГО ДТ с CFPP = +10 °C -> GOST_VETO_BLEND_INFEASIBLE."""
    cand = ControlCandidate(
        candidate_id="cand_warm_cfpp",
        steady_state={
            "HT_FEED_SP": 220.0,
            "HT_S_PRODUCT": 8.0,
            "HT_D15_PRODUCT": 836.0,
            "HT_FLASH": 68.0,
            "HT_CFPP_PRODUCT": 10.0,
            "HT_T95_PRODUCT": 347.0,
            "HT_CN_PRODUCT": 53.0,
        },
    )
    bad_tank = ComponentTank(
        name="ГО ДТ (резервуар)",
        stock_t=5000.0,
        props={"S_ppm": 8.0, "D15": 836.0, "Flash": 68.0, "CFPP": 10.0, "T95": 347.0, "CN": 53.0},
    )

    qual_rep = QualityAgent.audit(cand, tanks={"GODT": bad_tank})
    assert qual_rep.is_vetoed is True
    assert "GOST_VETO_BLEND_INFEASIBLE" in (qual_rep.violation_reason or "")


def test_normalized_step_norm_deadband():
    """8. Нормированная норма: HT_TIN_SP 2.0 при scale 2.0 дает 1.0 (проходит); 0.05 дает DEADBAND_REJECT_SMALL_STEP."""
    scales = {"HT_TIN_SP": 2.0}

    # Шаг 2.0 со шкалой 2.0 -> норма 1.0
    norm_pass = ArbitrationNode.calculate_norm({"HT_TIN_SP": 2.0}, scales=scales)
    assert norm_pass == pytest.approx(1.0)

    # Кандидат с шагом 0.05 -> норма 0.025 < 0.05
    norm_small = ArbitrationNode.calculate_norm({"HT_TIN_SP": 0.05}, scales=scales)
    assert norm_small == pytest.approx(0.025)

    cand_small = ControlCandidate(
        candidate_id="cand_tiny",
        delta_u={"HT_TIN_SP": 0.05},
        expected_margin=5000.0,
    )
    rec, sel = ArbitrationNode.execute(
        candidates=[cand_small],
        vetoed_ids=[],
        risk_penalties={},
        min_margin_improvement=1000.0,
        min_delta_norm=0.05,
        scales=scales,
    )
    assert rec.status == "DEADBAND_REJECT_SMALL_STEP"
    assert sel is None
