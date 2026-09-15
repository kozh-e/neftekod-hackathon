"""Узел Safe Hold: протокол мотивированного отказа и безопасного удержания режима.

Реализует требования Callout Box 4 ТЗ_нефтекод.docx и System_Design.md (Шаг 1.3):
При сбое датчиков, устаревании LIMS > 24ч или тотальном вето со стороны аудиторов,
система безударно замораживает уставки (delta_u = {}) и выдает обязательное
дословное русскоязычное сообщение для оператора ЦУП.
"""

from __future__ import annotations

from typing import Dict, Any
from src.agents.state import MasGraphState, FinalRecommendation

# Обязательный дословный регламентный текст отказа согласно Callout Box 4 ТЗ
REFUSAL_VERBATIM_TEXT: str = (
    "Надёжной рекомендации нет: последнее лабораторное значение устарело, "
    "а доступные варианты либо нарушают ограничение по качеству, "
    "либо выходят за заданный модельный диапазон."
)


def node_safe_hold(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел формирования мотивированного отказа (Graceful Refusal / Safe Hold).
    
    Переводит систему в режим безизменений: Delta u = {} (уставки фиксируются),
    предотвращая интегральное насыщение и разнос технологического режима.
    """
    final_rec = FinalRecommendation(
        status="SAFE_HOLD",
        explanation=REFUSAL_VERBATIM_TEXT,
        recommended_delta_u={}
    )
    
    return {"final_recommendation": final_rec}
