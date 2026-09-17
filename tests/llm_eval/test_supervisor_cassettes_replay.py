"""Тесты детерминированного реплея кассет LLM (Задача P4.1 / P4.12).

Проверяет 100% автономное выполнение без сети и без GPU:
1. Загрузка и валидация DiagnosticReport на кассете diagnostics_demo;
2. Загрузка и валидация ShiftBriefing на кассете briefing_demo;
3. Загрузка и валидация OperatorAnswer на кассете operator_qa_demo;
4. Загрузка и валидация PolicyProposal на кассете policy_demo;
5. Запрос неизвестной кассеты в REPLAY_STRICT вызывает явное исключение CassetteNotFoundError;
6. Режим OFF вызывает LLMDisabledError;
7. Нарушений GroundingChecker в опубликованных эталонных ответах = 0.
"""

from __future__ import annotations

import pytest
from src.supervisor.agents import (
    BriefingAgent,
    DiagnosticsAgent,
    OperatorQAAgent,
    PolicyAdvisor,
)
from src.supervisor.cassettes import CassetteNotFoundError, LLMDisabledError
from src.supervisor.evidence import build_evidence_package
from src.supervisor.llm_client import ReplayingOpenAIClient
from src.supervisor.validation import GroundingChecker


@pytest.fixture
def base_evidence():
    ev = build_evidence_package()
    # Фиксируем ключевые адреса для строгости заземления
    ev.add("equipment.avt_t55.measured", 385.0, unit="°C")
    ev.add("quality.godt_s.value", 8.45, unit="мг/кг")
    ev.add("quality.godt_s.sigma_calib", 0.82, unit="мг/кг")
    ev.add("quality.godt_s.calib_age_h", 2.0, unit="ч")
    return ev


def test_diagnostics_agent_replay(base_evidence):
    """DiagnosticsAgent детерминированно возвращает DiagnosticReport из кассеты."""
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    agent = DiagnosticsAgent(client=client)
    report = agent.run(base_evidence, cassette_name="diagnostics_demo")

    assert report.finding_id == "FND-20260917-001"
    assert report.category == "LIMS_PAK_CONFLICT"
    assert len(report.checks_recommended) >= 2
    assert "quality.godt_s.value" in report.evidence_refs

    # Проверка заземления (Grounding violations == 0)
    checker = GroundingChecker()
    grounding = checker.verify(report, base_evidence)
    assert grounding.passed is True
    assert len(grounding.missing_refs) == 0
    assert len(grounding.unmatched_numbers) == 0


def test_briefing_agent_replay(base_evidence):
    """BriefingAgent детерминированно возвращает ShiftBriefing из кассеты."""
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    agent = BriefingAgent(client=client)
    briefing = agent.run(base_evidence, cassette_name="briefing_demo")

    assert briefing.briefing_id == "SHF-20260917-001"
    assert "08:00 - 20:00" in briefing.shift_period
    assert briefing.status_counts.get("SUCCESS") == 40
    assert len(briefing.incoming_recommendations) >= 2

    checker = GroundingChecker()
    grounding = checker.verify(briefing, base_evidence)
    assert grounding.passed is True
    assert len(grounding.missing_refs) == 0


def test_operator_qa_agent_replay(base_evidence):
    """OperatorQAAgent детерминированно отвечает на вопрос из кассеты."""
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    agent = OperatorQAAgent(client=client)
    question = "Почему не поднимается загрузка сырья?"
    answer = agent.run(base_evidence, question=question, cassette_name="operator_qa_demo")

    assert answer.question == question
    assert "заблокировано" in answer.direct_answer
    assert "FURNACE.COT_MAX" in answer.active_constraints_involved
    assert len(answer.operator_guidance) > 0

    checker = GroundingChecker()
    grounding = checker.verify(answer, base_evidence)
    assert grounding.passed is True


def test_policy_advisor_replay(base_evidence):
    """PolicyAdvisor детерминированно возвращает PolicyProposal из кассеты."""
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    agent = PolicyAdvisor(client=client)
    proposal = agent.run(base_evidence, cassette_name="policy_demo")

    assert proposal.proposal_id == "POL-20260917-001"
    assert len(proposal.items) == 1
    assert proposal.items[0].field == "alpha_quality"
    assert proposal.items[0].new_value == 0.015


def test_replay_strict_miss_raises_error(base_evidence):
    """Промах мимо кассеты в режиме REPLAY_STRICT приводит к явному CassetteNotFoundError."""
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    agent = DiagnosticsAgent(client=client)
    with pytest.raises(CassetteNotFoundError):
        agent.run(base_evidence, cassette_name="non_existent_cassette_xyz")


def test_llm_mode_off_raises_error(base_evidence):
    """Режим NEFTEKOD_LLM_MODE=OFF приводит к LLMDisabledError."""
    client = ReplayingOpenAIClient(mode="OFF")
    agent = DiagnosticsAgent(client=client)
    with pytest.raises(LLMDisabledError):
        agent.run(base_evidence, cassette_name="diagnostics_demo")
