"""Узел Safe Hold: протокол мотивированного отказа и безопасного удержания режима.

Реализует требования Callout Box 4 ТЗ_нефтекод.docx и System_Design.md (Шаг 1.3):
При сбое датчиков, устаревании LIMS > 24ч или тотальном вето со стороны аудиторов,
система безударно замораживает уставки (delta_u = {}) и выдает обязательное
дословное русскоязычное сообщение для оператора ЦУП.
"""

from __future__ import annotations

from typing import Any, Dict
from src.agents.state_legacy import FinalRecommendation, MasGraphState

# Обязательный дословный регламентный текст отказа согласно Callout Box 4 ТЗ
REFUSAL_VERBATIM_TEXT: str = (
    "Надёжной рекомендации нет: последнее лабораторное значение устарело, "
    "а доступные варианты либо нарушают ограничение по качеству, "
    "либо выходят за заданный модельный диапазон."
)


def node_safe_hold(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел формирования мотивированного отказа (Graceful Refusal / Safe Hold).
    
    Переводит систему в режим без изменений: Delta u = {} (уставки фиксируются),
    предотвращая интегральное насыщение и разнос технологического режима.
    Если рекомендация уже сформирована арбитражем (напр. SAFE_HOLD_EMPTY_ADMISSIBLE),
    она сохраняется вместе с перечнем нарушенных пределов.
    """
    existing_rec = state.get("final_recommendation")
    if existing_rec is not None and (
        existing_rec.status.startswith("SAFE_HOLD") or existing_rec.status == "REFUSAL_DATA"
    ):
        if not getattr(existing_rec, "markdown_report", None):
            existing_rec.markdown_report = f"### 🚨 Режим БЕЗОПАСНОГО УДЕРЖАНИЯ (Safe Hold)\n\n{existing_rec.explanation}"
        return {"final_recommendation": existing_rec}

    dq = state.get("data_quality")
    status_val = "REFUSAL_DATA" if dq is not None and dq.status_code == "REFUSAL_DATA" else "SAFE_HOLD"

    final_rec = FinalRecommendation(
        status=status_val,
        explanation=REFUSAL_VERBATIM_TEXT,
        recommended_delta_u={},
        markdown_report=f"### 🚨 Режим БЕЗОПАСНОГО УДЕРЖАНИЯ ({status_val})\n\n{REFUSAL_VERBATIM_TEXT}",
    )

    return {"final_recommendation": final_rec}
