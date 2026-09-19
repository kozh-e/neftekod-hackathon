"""Контракты данных и глобальное состояние мультиагентного графа.

Содержит:
1. CoreState и типизированный редьюсер merge_new_keys для нового детерминированного ядра (§4.9);
2. Полный реэкспорт устаревших контрактов из src/agents/state_legacy.py для обратной совместимости;
3. Реэкспорт новых контрактов Pydantic v2 из src/agents/contracts.py.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from src.agents.contracts import (
    ArbitrationDecision,
    AutomationLevel,
    BlendingCertificate,
    Candidate,
    CandidateOrigin,
    ConstraintCertificate,
    ConstraintEvaluation,
    ConstraintSpec,
    ConstraintStatus,
    DataAssessment,
    DecisionStatus,
    DecisionTrace,
    Frozen,
    KernelCheck,
    KernelVerdict,
    Measurement,
    Merit,
    NegotiationEvent,
    PlantEstimate,
    Prediction,
    Provenance,
    ProvenanceKind,
    QualityEstimate,
    QualityProp,
    RecoveryPlan,
    RecoveryStep,
    RepairProposal,
    SignalQuality,
    SignalSource,
    Tier,
)
from src.agents.policy import PolicyConfig

# Реэкспорт legacy-моделей состояния для старого графа MVP и 144 существующих тестов
from src.agents.state_legacy import (
    BaseAgentProtocol,
    ControlCandidate,
    DataQuality,
    FinalRecommendation,
    MasGraphState,
    RawTelemetry,
    SafetyAuditReport,
    merge_audit_dict,
    merge_risk_penalties,
)

# Синоним для совместимости импортов
GraphState = MasGraphState


def merge_new_keys(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """
    Объединение словарей без перезаписи: повтор ключа с другим содержимым — ошибка детерминизма (§4.9).
    """
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key in merged and merged[key] != value:
            raise ValueError(f"Попытка перезаписать неизменяемую запись {key}: было {merged[key]}, стало {value}")
        merged[key] = value
    return merged


def update_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Обновление словаря с перезаписью значений (для замеров времени и изменяемых счетчиков)."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class CoreState(TypedDict, total=False):
    """
    Состояние графа детерминированного ядра переговоров мультиагентной системы (§4.9).
    """
    cycle: dict[str, Any]                  # cycle_id, t, code_version, inputs_hash
    policy: PolicyConfig
    raw_tags: dict[str, Any]
    tags: dict[str, Any]                   # алиас для совместимости с входными вызовами графа
    session_id: str                        # идентификатор сессии двойника
    tanks: dict[str, Any]
    data: DataAssessment
    confidence: dict[str, Any]              # индекс уверенности для UI пульта (§B1 аудита консоли), не участвует в T0-T3/маршрутизации
    estimate: PlantEstimate
    round: int
    frontier: list[str]                    # список сигнатур для текущего раунда
    candidates: Annotated[dict[str, Candidate], merge_new_keys]
    predictions: Annotated[dict[str, Prediction], merge_new_keys]
    certificates: Annotated[dict[str, ConstraintCertificate], merge_new_keys]  # ключ "signature|agent"
    blending: Annotated[dict[str, BlendingCertificate], merge_new_keys]
    negotiation_log: Annotated[list[NegotiationEvent], operator.add]           # журнал переговоров
    decision: ArbitrationDecision
    kernel: KernelVerdict
    recipe_shares: dict[str, float]
    recipe: Any
    blending_recipe: Any
    card: dict[str, Any]
    xai_card: str
    final_recommendation: Any
    events: list[str]
    timings_ms: Annotated[dict[str, float], update_dict]
