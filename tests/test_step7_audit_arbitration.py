"""Тесты ограничений и живого арбитража (test_step7_audit_arbitration.py).

Проверяет:
1. quality_risk_tags -> hold нарушает серу на установившемся режиме; есть допустимый
   корректирующий кандидат -> статус SUCCESS_CORRECTIVE;
2. Кандидат, ухудшающий переходный процесс сверх предела относительно hold, ветируется с причиной;
3. Статистический буфер серы (ADR-12): S_hat = 8.43, sigma = SIGMA_S0_PPM (0.83), z = QUALITY_Z (2, tz:598)
   -> 10.09 > 10 -> hold нарушает; при z = 1.0 -> 9.26 <= 10 -> не нарушает;
4. Смещение растет с возрастом ЛИМС при недоступном HT_Q21.

Тесты легаси auditors.ReliabilityAgent/QualityAgent.audit() и ArbitrationNode (вето по вспышке/
FEED_TO_AVT, audit_reports, GODT_VETO_BLEND_INFEASIBLE, нормированная норма шага) удалены вместе
с самими легаси-модулями (аудит 2026-09-20) — эквивалентная логика в v3 покрыта
test_agents_reliability.py/test_agents_quality.py и arbitration.py::decide().
"""

from __future__ import annotations

import pytest

from src.agents.constraints import assess_limit, stat_offset
from src.agents.graph import build_core_graph
from src.agents.limits import QUALITY_Z, SIGMA_S0_PPM


def test_quality_risk_triggers_success_corrective(quality_risk_tags):
    """1. quality_risk_tags -> hold нарушает серу на SS; статус SUCCESS_CORRECTIVE."""
    graph = build_core_graph()
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
    """3. S_hat = 8.43, sigma = 0.83. z=2 (tz:598) -> 10.09 > 10 (нарушение); z=1.0 -> 9.26 <= 10 (норма)."""
    s_hat = 8.43
    sigma0 = SIGMA_S0_PPM
    assert (QUALITY_Z, SIGMA_S0_PPM) == (2.0, 0.83)

    # z = 2 (промпт Агента Качества)
    off_2 = stat_offset(sigma0, age_h=0.0, z=QUALITY_Z)
    eff_2 = s_hat + off_2
    assert eff_2 == pytest.approx(10.09, abs=0.01)
    ass_2 = assess_limit("HT_S_PRODUCT", None, s_hat, None, limit=10.0, sense="max", offset=off_2)
    assert ass_2.vetoed is True

    # z = 1.0
    off_10 = stat_offset(sigma0, age_h=0.0, z=1.0)
    eff_10 = s_hat + off_10
    assert eff_10 == pytest.approx(9.26, abs=0.01)
    ass_10 = assess_limit("HT_S_PRODUCT", None, s_hat, None, limit=10.0, sense="max", offset=off_10)
    assert ass_10.vetoed is False


def test_offset_grows_with_lims_age():
    """4. Смещение растет с возрастом ЛИМС при недоступном HT_Q21."""
    sigma0 = SIGMA_S0_PPM
    z = QUALITY_Z

    off_0h = stat_offset(sigma0, age_h=0.0, z=z)
    off_4h = stat_offset(sigma0, age_h=4.0, z=z)
    off_12h = stat_offset(sigma0, age_h=12.0, z=z)
    off_24h = stat_offset(sigma0, age_h=24.0, z=z)

    assert off_0h < off_4h < off_12h < off_24h
