"""Граф вычислений и оркестрации LLM-супервизора (graph.py).

Построен на базе LangGraph StateGraph, реализует условную маршрутизацию ролей,
автоматическую валидацию (Grounding + PolicyValidator), теневой реплей (Shadow)
и фиксацию артефактов в SupervisorStore.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from src.supervisor.agents import (
    BriefingAgent,
    DiagnosticReport,
    DiagnosticsAgent,
    OperatorAnswer,
    OperatorQAAgent,
    PolicyAdvisor,
    PolicyProposal,
    ShiftBriefing,
)
from src.supervisor.evidence import EvidencePackage, build_evidence_package
from src.supervisor.llm_client import ReplayingOpenAIClient
from src.supervisor.shadow import ShadowReplayRunner, ShadowReport
from src.supervisor.store import (
    DEFAULT_SUPERVISOR_STORE,
    Finding,
    PolicyChangeRequest,
    SupervisorStore,
)
from src.supervisor.validation import (
    GroundingChecker,
    GroundingReport,
    PolicyValidationReport,
    PolicyValidator,
)


class SupervisorState(TypedDict, total=False):
    trigger: str
    question: Optional[str]
    evidence: Optional[EvidencePackage]
    diagnostic_report: Optional[DiagnosticReport]
    policy_proposal: Optional[PolicyProposal]
    shift_briefing: Optional[ShiftBriefing]
    operator_answer: Optional[OperatorAnswer]
    grounding_report: Optional[GroundingReport]
    policy_validation_report: Optional[PolicyValidationReport]
    shadow_report: Optional[ShadowReport]
    change_request: Optional[PolicyChangeRequest]
    output: Optional[Any]
    status: str
    error: Optional[str]


def node_build_evidence(state: SupervisorState) -> Dict[str, Any]:
    """Узел сборки детерминированного пакета доказательств."""
    trigger = state.get("trigger", "SCHEDULED")
    evidence = build_evidence_package(trigger=trigger)
    return {"evidence": evidence}


def route_by_trigger(state: SupervisorState) -> str:
    """Маршрутизация к соответствующему ролевому агенту."""
    tr = state.get("trigger", "").upper()
    if tr in ("OPERATOR_QUESTION", "QA"):
        return "operator_qa"
    elif tr in ("SHIFT_END", "BRIEFING"):
        return "briefing"
    elif tr in ("POLICY_EVAL", "POLICY_ADVISOR", "POLICY"):
        return "policy"
    else:
        # По умолчанию любые технологические инциденты и триггеры
        return "diagnostics"


def node_diagnostics(state: SupervisorState) -> Dict[str, Any]:
    evidence = state["evidence"]
    agent = DiagnosticsAgent()
    report = agent.run(evidence)
    return {"diagnostic_report": report, "output": report}


def node_policy(state: SupervisorState) -> Dict[str, Any]:
    evidence = state["evidence"]
    agent = PolicyAdvisor()
    proposal = agent.run(evidence)
    return {"policy_proposal": proposal, "output": proposal}


def node_briefing(state: SupervisorState) -> Dict[str, Any]:
    evidence = state["evidence"]
    agent = BriefingAgent()
    briefing = agent.run(evidence)
    return {"shift_briefing": briefing, "output": briefing}


def node_operator_qa(state: SupervisorState) -> Dict[str, Any]:
    evidence = state["evidence"]
    question = state.get("question", "В чем причина текущего состояния системы?")
    agent = OperatorQAAgent()
    answer = agent.run(evidence, question=question)
    return {"operator_answer": answer, "output": answer}


def node_validate_output(state: SupervisorState) -> Dict[str, Any]:
    """Проверка заземления (Grounding) для диагностических отчетов и ответов оператору."""
    output = state.get("output")
    evidence = state.get("evidence")
    if not output or not evidence:
        return {"grounding_report": GroundingReport(passed=True)}

    checker = GroundingChecker()
    report = checker.verify(output, evidence)
    return {"grounding_report": report}


def node_policy_validator(state: SupervisorState) -> Dict[str, Any]:
    """Проверка предложения политики по белому списку."""
    proposal = state.get("policy_proposal")
    if not proposal:
        return {"policy_validation_report": PolicyValidationReport(passed=False, violations=["Нет предложения"])}

    validator = PolicyValidator()
    report = validator.validate_proposal(proposal)
    return {"policy_validation_report": report}


def route_policy_validator(state: SupervisorState) -> str:
    rep = state.get("policy_validation_report")
    if rep and rep.passed:
        return "shadow_replay"
    return "publish"


def node_shadow_replay(state: SupervisorState) -> Dict[str, Any]:
    """Теневой прогон сценариев с предложенной политикой."""
    proposal = state.get("policy_proposal")
    if not proposal:
        return {}

    runner = ShadowReplayRunner()
    report = runner.run_shadow_test(proposal)
    return {"shadow_report": report}


def route_shadow(state: SupervisorState) -> str:
    rep = state.get("shadow_report")
    if rep and rep.passed:
        return "human_approval"
    return "publish"


def node_human_approval(state: SupervisorState) -> Dict[str, Any]:
    """Создает запрос на изменение с ожиданием подтверждения человеком (технологом)."""
    proposal = state.get("policy_proposal")
    if not proposal:
        return {}

    req = PolicyChangeRequest(
        request_id=f"CR-{proposal.proposal_id}",
        proposal=proposal,
        status="PENDING_APPROVAL",
        shadow_passed=True,
    )
    return {"change_request": req}


def node_publish(state: SupervisorState) -> Dict[str, Any]:
    """Публикует и сохраняет артефакты в SupervisorStore."""
    store = DEFAULT_SUPERVISOR_STORE

    # 1. Сохранение находки при диагностике
    if state.get("diagnostic_report"):
        finding = Finding.from_diagnostic_report(state["diagnostic_report"])
        store.save_finding(finding)

    # 2. Сохранение сводки смены
    if state.get("shift_briefing"):
        store.save_briefing(state["shift_briefing"])

    # 3. Сохранение запроса на изменение политики
    if state.get("change_request"):
        store.save_change_request(state["change_request"])

    return {"status": "PUBLISHED"}


def build_supervisor_graph(checkpointer: Optional[Any] = None) -> StateGraph:
    """Собирает полный вычислительный граф супервизора."""
    builder = StateGraph(SupervisorState)

    builder.add_node("build_evidence", node_build_evidence)
    builder.add_node("diagnostics", node_diagnostics)
    builder.add_node("policy", node_policy)
    builder.add_node("briefing", node_briefing)
    builder.add_node("operator_qa", node_operator_qa)
    builder.add_node("validate_output", node_validate_output)
    builder.add_node("policy_validator", node_policy_validator)
    builder.add_node("shadow_replay", node_shadow_replay)
    builder.add_node("human_approval", node_human_approval)
    builder.add_node("publish", node_publish)

    builder.add_edge(START, "build_evidence")
    builder.add_conditional_edges(
        "build_evidence",
        route_by_trigger,
        {
            "diagnostics": "diagnostics",
            "policy": "policy",
            "briefing": "briefing",
            "operator_qa": "operator_qa",
        },
    )

    builder.add_edge("diagnostics", "validate_output")
    builder.add_edge("briefing", "validate_output")
    builder.add_edge("operator_qa", "validate_output")
    builder.add_edge("validate_output", "publish")

    builder.add_edge("policy", "policy_validator")
    builder.add_conditional_edges(
        "policy_validator",
        route_policy_validator,
        {"shadow_replay": "shadow_replay", "publish": "publish"},
    )
    builder.add_conditional_edges(
        "shadow_replay",
        route_shadow,
        {"human_approval": "human_approval", "publish": "publish"},
    )
    builder.add_edge("human_approval", "publish")
    builder.add_edge("publish", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
