"""Ролевые агенты LLM-супервизора и структурированное исполнение (agents.py).

Реализует специализированные роли:
1. DiagnosticsAgent -> DiagnosticReport
2. PolicyAdvisor -> PolicyProposal
3. BriefingAgent -> ShiftBriefing
4. OperatorQAAgent -> OperatorAnswer
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Type, TypeVar
from pydantic import BaseModel, Field

from src.supervisor.cassettes import CassetteNotFoundError
from src.supervisor.evidence import EvidencePackage
from src.supervisor.llm_client import ReplayingOpenAIClient, extract_json_payload

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(role: str) -> str:
    """Загружает текст системного промпта соответствующей роли."""
    prompt_file = PROMPTS_DIR / f"{role}.v1.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Файл промпта не найден: {prompt_file}")
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


# =============================================================================
# Структурированные Pydantic-контракты вывода агентов
# =============================================================================

class DiagnosticReport(BaseModel):
    """Структурированный отчет технической диагностики инцидента."""
    finding_id: str
    category: str
    severity: Literal["CRITICAL", "WARNING", "INFO"] = "WARNING"
    title: str
    root_cause: str
    evidence_refs: List[str] = Field(default_factory=list)
    checks_recommended: List[str] = Field(default_factory=list)
    safety_risk_assessment: str


class PolicyProposalItem(BaseModel):
    """Единичное изменение параметра технологической политики."""
    field: str
    old_value: float
    new_value: float
    justification: str


class PolicyProposal(BaseModel):
    """Предложение по корректировке параметров технологической политики."""
    proposal_id: str
    items: List[PolicyProposalItem] = Field(default_factory=list)
    expected_kpi_impact: str
    evidence_refs: List[str] = Field(default_factory=list)


class ShiftBriefing(BaseModel):
    """Сводка состояния технологического комплекса при передаче смены."""
    briefing_id: str
    shift_period: str
    summary_text: str
    status_counts: Dict[str, int] = Field(default_factory=dict)
    quality_assessment: str
    safety_assessment: str
    open_concerns: List[str] = Field(default_factory=list)
    incoming_recommendations: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)


class OperatorAnswer(BaseModel):
    """Ответ на вопрос оператора технологической установки."""
    question: str
    direct_answer: str
    technical_explanation: str
    active_constraints_involved: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    operator_guidance: str


T = TypeVar("T", bound=BaseModel)


def run_structured(
    client: ReplayingOpenAIClient,
    role: str,
    evidence: EvidencePackage,
    output_model: Type[T],
    question: Optional[str] = None,
    cassette_name: Optional[str] = None,
) -> T:
    """Выполняет структурированный вызов LLM с валидацией через Pydantic."""
    system_prompt = load_prompt(role)

    user_content = evidence.as_prompt_block()
    if question:
        user_content += f"\n\nВОПРОС ОПЕРАТОРА:\n{question}"

    user_content += (
        f"\n\nОтветь строго в формате JSON, соответствующем схеме {output_model.__name__}."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    resp = client.create(
        messages=messages,
        response_format={"type": "json_object"},
        cassette_name=cassette_name,
    )

    raw_json = extract_json_payload(resp.content)
    return output_model.model_validate(raw_json)


# =============================================================================
# Классы ролевых агентов
# =============================================================================

class DiagnosticsAgent:
    """Агент глубокой диагностики технологических инцидентов и аномалий."""

    def __init__(self, client: Optional[ReplayingOpenAIClient] = None):
        self.client = client or ReplayingOpenAIClient()

    def run(
        self,
        evidence: EvidencePackage,
        cassette_name: Optional[str] = None,
    ) -> DiagnosticReport:
        return run_structured(
            client=self.client,
            role="diagnostics",
            evidence=evidence,
            output_model=DiagnosticReport,
            cassette_name=cassette_name,
        )


class PolicyAdvisor:
    """Агент анализа и предложения безопасных калибровок технологической политики."""

    def __init__(self, client: Optional[ReplayingOpenAIClient] = None):
        self.client = client or ReplayingOpenAIClient()

    def run(
        self,
        evidence: EvidencePackage,
        cassette_name: Optional[str] = None,
    ) -> PolicyProposal:
        return run_structured(
            client=self.client,
            role="policy",
            evidence=evidence,
            output_model=PolicyProposal,
            cassette_name=cassette_name,
        )


class BriefingAgent:
    """Агент подготовки аналитической сводки для смены."""

    def __init__(self, client: Optional[ReplayingOpenAIClient] = None):
        self.client = client or ReplayingOpenAIClient()

    def run(
        self,
        evidence: EvidencePackage,
        cassette_name: Optional[str] = None,
    ) -> ShiftBriefing:
        return run_structured(
            client=self.client,
            role="briefing",
            evidence=evidence,
            output_model=ShiftBriefing,
            cassette_name=cassette_name,
        )


class OperatorQAAgent:
    """Агент-консультант оператора по трассам и ограничениям."""

    def __init__(self, client: Optional[ReplayingOpenAIClient] = None):
        self.client = client or ReplayingOpenAIClient()

    def run(
        self,
        evidence: EvidencePackage,
        question: str,
        cassette_name: Optional[str] = None,
    ) -> OperatorAnswer:
        return run_structured(
            client=self.client,
            role="operator_qa",
            evidence=evidence,
            output_model=OperatorAnswer,
            question=question,
            cassette_name=cassette_name,
        )
