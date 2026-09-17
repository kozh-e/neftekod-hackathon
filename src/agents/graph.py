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
