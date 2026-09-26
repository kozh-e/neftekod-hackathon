"""Фоновый сервис асинхронного LLM-супервизора (service.py).

Оркестрирует регулярный мониторинг технологических трасс решений,
реагирование на триггеры инцидентов, генерацию сводок смен и консультацию оператора.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.agents.decision_store import DecisionStore, DEFAULT_STORE
from src.supervisor.agents import OperatorAnswer, ShiftBriefing
from src.supervisor.graph import build_supervisor_graph
from src.supervisor.store import DEFAULT_SUPERVISOR_STORE, SupervisorStore
from src.supervisor.triggers import TriggerManager

logger = logging.getLogger(__name__)


class SupervisorService:
    """Сервис жизненного цикла LLM-супервизора."""

    def __init__(
        self,
        decision_store: Optional[DecisionStore] = None,
        supervisor_store: Optional[SupervisorStore] = None,
    ):
        self.decision_store = decision_store or DEFAULT_STORE
        self.supervisor_store = supervisor_store or DEFAULT_SUPERVISOR_STORE
        self.trigger_manager = TriggerManager()
        self.graph = build_supervisor_graph()

    def process_cycle_triggers(self) -> List[str]:
        """Проверяет возникновение триггеров на основе последних трасс и запускает диагностику при необходимости."""
        traces = self.decision_store.list_recent(limit=10)
        now = datetime.now()
        active_triggers = self.trigger_manager.detect_active_triggers(traces, now=now)

        for tr_name in active_triggers:
            logger.info("Запуск супервизора по триггеру: %s", tr_name)
            self.trigger_manager.mark_fired(tr_name, now=now)
            try:
                self.graph.invoke({"trigger": tr_name})
            except Exception as exc:
                logger.error("Ошибка при обработке триггера %s: %s", tr_name, exc)

        return active_triggers

    def answer_operator(self, question: str) -> OperatorAnswer:
        """Обрабатывает прямой вопрос оператора с проверкой заземления и отсутствия уставок."""
        state = self.graph.invoke({
            "trigger": "OPERATOR_QUESTION",
            "question": question,
        })
        answer = state.get("operator_answer")
        grounding = state.get("grounding_report")
        if answer is not None and grounding is not None and not grounding.passed:
            logger.warning(
                "Ответ супервизора не прошёл проверку заземления (grounding), отклонён: %s",
                "; ".join(grounding.notes) or "нет обоснования числами/ссылками из EvidencePackage",
            )
            return OperatorAnswer(
                question=question,
                direct_answer="Не удалось сформировать проверяемый ответ (LLM-ответ не подтверждён данными).",
                technical_explanation=(
                    "Ответ супервизора не прошёл проверку заземления GroundingChecker "
                    "(несовпадающие числа/ссылки на EvidencePackage) и не публикуется."
                ),
                operator_guidance="Обратитесь к трассам решений напрямую или повторите вопрос позже.",
            )
        return answer

    def generate_shift_briefing(self) -> ShiftBriefing:
        """Формирует структурированную сводку для передачи смены."""
        state = self.graph.invoke({"trigger": "SHIFT_END"})
        briefing = state.get("shift_briefing")
        grounding = state.get("grounding_report")
        if briefing is not None and grounding is not None and not grounding.passed:
            logger.warning(
                "Сводка смены не прошла проверку заземления (grounding), отклонена: %s",
                "; ".join(grounding.notes) or "нет обоснования числами/ссылками из EvidencePackage",
            )
            return ShiftBriefing(
                briefing_id=briefing.briefing_id,
                shift_period=briefing.shift_period,
                summary_text="Автосводка не прошла проверку заземления и не публикуется.",
                quality_assessment="Недоступно (отказ заземления)",
                safety_assessment="Недоступно (отказ заземления)",
            )
        return briefing


DEFAULT_SUPERVISOR_SERVICE = SupervisorService()
