"""Сборка графа вычислений LangGraph для Шага 1 MVP.

Реализует отказоустойчивую машину состояний без риска дедлоков:
Data Quality Guard -> [route_after_guard] -> Safe Hold (при отказе КИП/LIMS)
                                         -> Optimization (при валидных данных)
"""

from __future__ import annotations

from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from src.agents.state import MasGraphState
from src.agents.data_guard import node_data_quality_guard
from src.agents.safe_hold import node_safe_hold
from src.agents.optimization_stub import node_optimization_stub


def route_after_guard(state: MasGraphState) -> Literal["safe_hold", "optimization"]:
    """
    Условный роутер после узла проверки качества данных.
    
    Если КИПиА или анализы LIMS повреждены/устарели, немедленно
    направляет поток управления в узел Safe Hold в обход математического
    ядра оптимизации, предотвращая сбои SLSQP и NaN-взрывы.
    """
    data_quality = state.get("data_quality")
    if data_quality is None or not data_quality.is_valid:
        return "safe_hold"
    return "optimization"


def build_mvp_graph() -> CompiledStateGraph:
    """
    Конструирует и компилирует StateGraph для Шага 1 MVP.
    
    Возвращает скомпилированный объект графа с поддержкой вызова .invoke().
    """
    builder = StateGraph(MasGraphState)

    # Регистрация узлов обработки
    builder.add_node("data_guard", node_data_quality_guard)
    builder.add_node("safe_hold", node_safe_hold)
    builder.add_node("optimization", node_optimization_stub)

    # Стартовая точка вычислений
    builder.set_entry_point("data_guard")

    # Условный переход из data_guard
    builder.add_conditional_edges(
        "data_guard",
        route_after_guard,
        {
            "safe_hold": "safe_hold",
            "optimization": "optimization"
        }
    )

    # Завершение графа
    builder.add_edge("safe_hold", END)
    builder.add_edge("optimization", END)

    return builder.compile()
