"""Сборка графа вычислений LangGraph для мультиагентной системы замкнутого контура.

Реализует сквозной процесс (System_Design.md, Шаги 1-4):
Data Quality Guard -> [route_after_guard]
  -> (сбой КИП/LIMS) -> Safe Hold -> END
  -> (норма) -> Optimization Agent
       -> (Fan-Out) -> Reliability Agent (ПАЗ/ESD)
       -> (Fan-Out) -> Quality Agent (ГОСТ)
       -> (Fan-In) -> Pareto Analysis (недоминируемые допустимые кандидаты)
       -> Topological Arbitrator (Двухстадийный арбитраж)
            -> [route_after_arbitration]
                 -> (тотальное вето) -> Safe Hold -> END
                 -> (deadband) -> END
                 -> (успех) -> Fuel Blending Agent (HiGHS LP) -> END
"""

from __future__ import annotations

from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from src.agents.state import MasGraphState
from src.agents.data_guard import node_data_quality_guard
from src.agents.safe_hold import node_safe_hold
from src.agents.optimization import node_optimization
from src.agents.auditors import node_reliability_agent, node_quality_agent
from src.agents.pareto import node_pareto
from src.agents.arbitration import node_arbitration
from src.agents.blending import node_blending_agent


def route_after_guard(state: MasGraphState) -> Literal["safe_hold", "optimization"]:
    """Условный роутер после проверки качества КИПиА и возраста LIMS."""
    data_quality = state.get("data_quality")
    if data_quality is None or not data_quality.is_valid:
        return "safe_hold"
    return "optimization"


def route_after_arbitration(state: MasGraphState) -> Literal["safe_hold", "blending", "end"]:
    """Условный роутер после двухстадийного гибридного арбитража."""
    final_rec = state.get("final_recommendation")
    if final_rec is None or final_rec.status.startswith("SAFE_HOLD"):
        return "safe_hold"
    if final_rec.status.startswith("SUCCESS") and state.get("selected_candidate") is not None:
        return "blending"
    return "end"


def build_mvp_graph() -> CompiledStateGraph:
    """
    Конструирует и компилирует StateGraph полной мультиагентной системы.
    """
    builder = StateGraph(MasGraphState)

    # Регистрация всех узлов
    builder.add_node("data_guard", node_data_quality_guard)
    builder.add_node("safe_hold", node_safe_hold)
    builder.add_node("optimization", node_optimization)
    builder.add_node("reliability_agent", node_reliability_agent)
    builder.add_node("quality_agent", node_quality_agent)
    builder.add_node("pareto", node_pareto)
    builder.add_node("arbitration", node_arbitration)
    builder.add_node("blending", node_blending_agent)

    # Стартовая точка
    builder.set_entry_point("data_guard")

    # Условный переход после проверки данных
    builder.add_conditional_edges(
        "data_guard",
        route_after_guard,
        {
            "safe_hold": "safe_hold",
            "optimization": "optimization"
        }
    )

    # Параллельный Fan-Out из оптимизатора к аудиторам
    builder.add_edge("optimization", "reliability_agent")
    builder.add_edge("optimization", "quality_agent")

    # Слияние Fan-In от аудиторов в Парето-анализ (Шаг 7 цикла ТЗ), затем арбитраж
    builder.add_edge("reliability_agent", "pareto")
    builder.add_edge("quality_agent", "pareto")
    builder.add_edge("pareto", "arbitration")

    # Условный переход после арбитража
    builder.add_conditional_edges(
        "arbitration",
        route_after_arbitration,
        {
            "safe_hold": "safe_hold",
            "blending": "blending",
            "end": END
        }
    )

    # Завершение графа
    builder.add_edge("safe_hold", END)
    builder.add_edge("blending", END)

    return builder.compile()


# =============================================================================
# Целевой Граф Детерминированного Ядра Переговоров v3 (ADR-13, ADR-26, §5.14)
# =============================================================================

import datetime
import hashlib
import math
import time
import uuid
from typing import Dict, List, Literal, Optional
from langgraph.graph import StateGraph, START, END

from src.agents.contracts import (
    ArbitrationDecision,
    AutomationLevel,
    DecisionStatus,
    DecisionTrace,
    KernelVerdict,
    Merit,
    NegotiationEvent,
    PlantEstimate,
    Tier,
)
from src.agents.data_guard import (
    CLAMPING_VALUES,
    CRITICAL_TAGS,
    Q21_MAX_PLAUSIBLE_PPM,
    assess_data,
    compute_confidence,
)
from src.twin.tags import fill_from_nominal, normalize_tags
from src.agents.decision_store import save_decision_trace
from src.agents.estimation import StateEstimator
from src.agents.generator import hold, signature_of
from src.agents.negotiation import (
    node_blending,
    node_coordinate,
    node_predict,
    node_propose,
    node_quality,
    node_reliability,
    node_supply,
    route_after_coordinate,
    _extract_context,
)
from src.agents.arbitration import (
    decide,
    node_arbitrate,
    TZ_REFUSAL_DATA_TEXT,
    TZ_REFUSAL_NO_SAFE_ACTION_TEXT,
)
from src.agents.policy import PolicyConfig
from src.agents.registry import REGISTRY_ADAPTER
from src.safety_kernel.kernel import SafetyKernel
from src.agents.state import CoreState
from src.agents.state_legacy import FinalRecommendation
from src.agents.twin_view import TwinView
from src.twin.chain import FullChainTwin
from src.twin.params import load_params


class _KernelTwinAdapter:
    """Даёт twin_factory ядра безопасности (SafetyKernel.verify) интерфейс .steady_state(u1)
    поверх TwinView, чтобы независимая проверка использовала те же калибровочные поправки
    PlantEstimate (§5 kernel.py), что и агенты качества/надёжности (quality.py, reliability.py) —
    без этого сырой FullChainTwin.steady_state() даёт несопоставимо другие значения (проверено
    эмпирически: разница в предсказании HT_FLASH могла достигать ~10 °C)."""

    def __init__(self, twin_view: TwinView) -> None:
        self._tv = twin_view

    def steady_state(self, u1: dict) -> dict:
        raw_ss = self._tv.twin.steady_state(u1)
        return self._tv._apply_corrections(raw_ss)


def node_ingest(state: CoreState) -> dict:
    """Узел 1: распаковка входных тегов, генерация cycle_id и inputs_hash."""
    t_now = datetime.datetime.now(datetime.timezone.utc)
    raw_tags = dict(state.get("raw_tags") or state.get("tags") or {})
    policy = state.get("policy") or PolicyConfig()
    cycle_id = state.get("cycle", {}).get("cycle_id", str(uuid.uuid4()))

    tag_str = ";".join(f"{k}={raw_tags[k]}" for k in sorted(raw_tags.keys()))
    inp_hash = hashlib.sha1(tag_str.encode("utf-8")).hexdigest()

    return {
        "cycle": {"cycle_id": cycle_id, "t": t_now, "inputs_hash": inp_hash},
        "raw_tags": raw_tags,
        "policy": policy,
        "t_cycle_start": time.perf_counter(),
    }


def node_data_guard_core(state: CoreState) -> dict:
    """Узел 2: верификация КИПиА и возраста ЛИМС DataGuard."""
    t_start = time.perf_counter()
    raw_tags = state.get("raw_tags") or {}
    policy = state.get("policy") or PolicyConfig()
    assessment = assess_data(raw_tags, policy=policy)

    # Индекс уверенности (§B1 аудита консоли): наблюдаемое поле для UI пульта,
    # не влияет на assessment/маршрутизацию выше. Величины n_filled_critical/
    # q21_unavailable/lims_age считаются тем же способом, что и в
    # node_data_quality_guard (src/agents/data_guard.py), формула не меняется —
    # см. compute_confidence.
    norm_tags, _norm_warnings = normalize_tags(raw_tags)
    norm_tags, _fill_warnings = fill_from_nominal(norm_tags, CRITICAL_TAGS)
    n_filled_critical = len([w for w in _fill_warnings if w.startswith("FILLED:")])

    lims_age = float(raw_tags.get("lims_age_hours", 0.0))

    q21_val = norm_tags.get("HT_Q21")
    q21_is_spike = False
    if isinstance(q21_val, (int, float)) and not math.isnan(q21_val):
        q21_val_f = float(q21_val)
        if q21_val_f in CLAMPING_VALUES or q21_val_f > Q21_MAX_PLAUSIBLE_PPM:
            q21_is_spike = True
    q21_unavailable = (
        q21_val is None
        or not isinstance(q21_val, (int, float))
        or math.isnan(q21_val)
        or q21_is_spike
    )

    confidence = compute_confidence(n_filled_critical, q21_unavailable, lims_age)

    return {
        "data": assessment,
        "confidence": confidence,
        "timings_ms": {"data_guard": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_estimate_core(state: CoreState) -> dict:
    """Узел 3: оценка состояния PlantEstimate."""
    t_start = time.perf_counter()
    if state.get("estimate") is not None:
        return {"timings_ms": {"estimate": round((time.perf_counter() - t_start) * 1000, 2)}}

    ctx = _extract_context(state)
    return {
        "estimate": ctx.estimate,
        "timings_ms": {"estimate": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def route_after_estimate(state: CoreState) -> Literal["refuse", "propose"]:
    """Маршрутизация: при REFUSAL_DATA немедленный переход к отказу."""
    data = state.get("data")
    if data and data.automation_level == AutomationLevel.REFUSAL_DATA:
        return "refuse"
    return "propose"


def node_refuse(state: CoreState) -> dict:
    """Узел формирования регламентного отказа ТЗ при сбое данных."""
    data = state.get("data")
    h_cand = hold()
    h_merit = Merit(v=(0, 0, 0, 0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0)
    decision = ArbitrationDecision(
        status=DecisionStatus.REFUSAL_DATA,
        selected=None,
        delta_u={},
        merit=None,
        hold_merit=h_merit,
        refusal_text=TZ_REFUSAL_DATA_TEXT,
        reason_codes=tuple(data.reasons if data else ()),
    )
    return {"decision": decision}


def node_safety_kernel(state: CoreState) -> dict:
    """Узел независимого ядра безопасности SafetyKernel."""
    t_start = time.perf_counter()
    decision = state.get("decision")
    estimate = state.get("estimate")
    data = state.get("data")
    policy = state.get("policy")

    verdict = SafetyKernel.verify(
        decision=decision,
        estimate=estimate,
        data=data,
        registry=REGISTRY_ADAPTER,
        policy=policy,
        # Без twin_factory проверки T0.bounds/T0.rate и независимая проверка
        # оборудования/качества (§5 kernel.py, GODT.FLASH_MIN и др.) молча не выполняются
        # (registry без mv_lo/mv_hi/mv_max_move/applicable, twin_factory отсутствует).
        twin_factory=lambda est: _KernelTwinAdapter(
            TwinView(twin=FullChainTwin(load_params()), estimate=est, policy=policy)
        ),
    )

    events: List[NegotiationEvent] = []
    updated_decision = decision
    if not verdict.passed:
        events.append(
            NegotiationEvent(
                round=state.get("round", 0),
                kind="KERNEL_OVERRIDE",
                actor="safety_kernel",
                candidate=decision.selected if decision else None,
                detail="Решение отклонено независимым ядром безопасности",
            )
        )
        updated_decision = ArbitrationDecision(
            status=verdict.overridden_status or DecisionStatus.REFUSAL_NO_SAFE_ACTION,
            selected=None,
            delta_u={},
            merit=decision.merit if decision else None,
            hold_merit=decision.hold_merit if decision else Merit(v=(0,0,0,0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0),
            refusal_text=TZ_REFUSAL_NO_SAFE_ACTION_TEXT,
            reason_codes=("KERNEL_OVERRIDE",),
        )

    res = {
        "kernel": verdict,
        "decision": updated_decision,
        "timings_ms": {"safety_kernel": round((time.perf_counter() - t_start) * 1000, 2)},
    }
    if events:
        res["negotiation_log"] = events
    return res


def node_blend_recipe(state: CoreState) -> dict:
    """Узел фиксации рецептуры смешения для выбранного кандидата."""
    decision = state.get("decision")
    sel_sig = decision.selected if (decision and decision.selected) else signature_of({})
    blend_cert = state.get("blending", {}).get(sel_sig)
    if blend_cert is None:
        blend_cert = state.get("blending", {}).get(signature_of({}))
    shares = blend_cert.shares if blend_cert else {}
    return {
        "recipe_shares": shares,
        "recipe": blend_cert,
        "blending_certificate": blend_cert,
    }


def node_card(state: CoreState) -> dict:
    """Узел генерации детерминированной XAI-карточки решения."""
    from src.xai.card import build_decision_card

    card = build_decision_card(state)
    decision = state.get("decision")

    status_str = decision.status.value if decision and hasattr(decision.status, "value") else str(getattr(decision, "status", "REFUSAL_DATA"))
    delta_dict = decision.delta_u if decision else {}
    expl = card.markdown

    # Адаптер для обратной совместимости с тестами
    final_rec = FinalRecommendation(
        status=status_str,
        recommended_delta_u=delta_dict,
        explanation=card.refusal_text or f"Статус решения: {status_str}",
        markdown_report=expl,
    )

    events = [e.kind for e in state.get("negotiation_log", []) if hasattr(e, "kind")]

    return {
        "card": card,
        "xai_card": expl,
        "final_recommendation": final_rec,
        "events": events,
    }


def node_journal(state: CoreState) -> dict:
    """Узел сохранения трассы DecisionTrace в SQLite и JSONL."""
    cycle_info = state.get("cycle", {})
    c_id = cycle_info.get("cycle_id", str(uuid.uuid4()))
    t_val = cycle_info.get("t", datetime.datetime.now(datetime.timezone.utc))
    inp_hash = cycle_info.get("inputs_hash", "")
    policy = state.get("policy")
    decision = state.get("decision")
    kernel = state.get("kernel") or KernelVerdict(passed=True, checks=(), overridden_status=None, kernel_version="1.0.0")

    if decision and state.get("data") and state.get("estimate"):
        trace = DecisionTrace(
            cycle_id=c_id,
            t=t_val,
            code_version="3.0.0",
            policy_version=policy.version if policy else "1.0.0",
            inputs_hash=inp_hash,
            data=state["data"],
            estimate=state["estimate"],
            candidates=tuple(state.get("candidates", {}).values()),
            predictions=tuple(state.get("predictions", {}).values()),
            certificates=tuple(state.get("certificates", {}).values()),
            blending=tuple(state.get("blending", {}).values()),
            negotiation=tuple(state.get("negotiation_log", [])),
            decision=decision,
            kernel=kernel,
            recipe_shares=state.get("recipe_shares"),
            timings_ms=dict(state.get("timings_ms", {})),
        )
        try:
            save_decision_trace(trace)
        except Exception:
            pass
    return {}


def build_core_graph() -> CompiledStateGraph:
    """
    Конструирует и компилирует детерминированный граф переговоров v3 (§5.14).
    """
    builder = StateGraph(CoreState)

    # Регистрация узлов
    builder.add_node("ingest", node_ingest)
    builder.add_node("data_guard", node_data_guard_core)
    builder.add_node("estimate", node_estimate_core)
    builder.add_node("refuse", node_refuse)
    builder.add_node("propose", node_propose)
    builder.add_node("predict", node_predict)
    builder.add_node("reliability", node_reliability)
    builder.add_node("quality", node_quality)
    builder.add_node("supply", node_supply)
    builder.add_node("blending", node_blending)
    builder.add_node("coordinate", node_coordinate)
    builder.add_node("arbitrate", node_arbitrate)
    builder.add_node("safety_kernel", node_safety_kernel)
    builder.add_node("blend_recipe", node_blend_recipe)
    builder.add_node("card", node_card)
    builder.add_node("journal", node_journal)

    # Ребра графа
    builder.set_entry_point("ingest")
    builder.add_edge("ingest", "data_guard")
    builder.add_edge("data_guard", "estimate")

    builder.add_conditional_edges(
        "estimate",
        route_after_estimate,
        {"refuse": "refuse", "propose": "propose"}
    )

    builder.add_edge("propose", "predict")
    builder.add_edge("predict", "reliability")
    builder.add_edge("predict", "quality")
    builder.add_edge("predict", "supply")
    builder.add_edge("quality", "blending")

    # Ожидающее соединение (Fan-In)
    builder.add_edge(["reliability", "supply", "blending"], "coordinate")

    # Раундовый цикл переговоров
    builder.add_conditional_edges(
        "coordinate",
        route_after_coordinate,
        {"next_round": "predict", "done": "arbitrate"}
    )

    builder.add_edge("arbitrate", "safety_kernel")
    builder.add_edge("safety_kernel", "blend_recipe")
    builder.add_edge("blend_recipe", "card")
    builder.add_edge("refuse", "card")
    builder.add_edge("card", "journal")
    builder.add_edge("journal", END)

    return builder.compile()


# Переключение графа по умолчанию
DEFAULT_GRAPH_MODE = "core_v3"


def get_graph(graph_mode: Optional[str] = None) -> CompiledStateGraph:
    mode = graph_mode or DEFAULT_GRAPH_MODE
    if mode == "legacy":
        return build_mvp_graph()
    elif mode == "core_v3":
        return build_core_graph()
    elif mode == "shadow":
        # Для режима shadow возвращается core_v3 с теневым исполнением
        return build_core_graph()
    return build_core_graph()
