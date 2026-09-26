"""LLM-обогащение вкладки «Агенты и XAI»: короткий отчёт «почему агенты приняли такое решение».

Работает поверх уже собранной детерминированной DecisionCard (src/xai/card.py) и полного журнала
переговоров текущего такта (NegotiationEventDTO) — LLM не придумывает факты, а пересказывает их
оператору понятным языком. Использует тот же клиент/режимы (REPLAY_STRICT/LIVE_RECORD/OFF), что и
остальной LLM-супервизор (src/supervisor/llm_client.py), поэтому при отсутствии ключа или
недоступности кассеты просто возвращает None — вызывающий код (src/console/api.py) в этом случае
не показывает отчёт.

Помимо базовой (сбалансированной) рекомендации, поддерживает объяснение конкретной
Парето-альтернативы («Макс. маржа» и т.п.), которую оператор выбрал на пульте — см.
`generate_xai_report(..., alternative=...)`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.supervisor.cassettes import CassetteNotFoundError, LLMDisabledError
from src.supervisor.llm_client import ReplayingOpenAIClient, extract_json_payload
from src.xai.card import DecisionCard

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """# РОЛЬ: Объяснение хода мультиагентной системы для вкладки «Агенты и XAI»
Версия: xai_explainer.v3

## 1. НАЗНАЧЕНИЕ
Ты — инженер-технолог, который кратко и понятно объясняет старшему оператору, почему
мультиагентная система (специализированные агенты + независимое ядро безопасности) приняла
именно такое решение (или альтернативный вариант) в текущем такте управления установкой
гидроочистки дизельного топлива.

## 2. КАТЕГОРИЧЕСКИЕ ПРАВИЛА
1. СТРОГОЕ ЗАЗЕМЛЕНИЕ: используй только факты, цифры и формулировки из карточки решения и данных,
   переданных ниже. Не придумывай числа, теги и ограничения, которых там нет.
2. ЗАПРЕТ РЕКОМЕНДАЦИЙ: ты объясняешь уже принятое решение (или его альтернативу) постфактум, а не
   советуешь новые уставки.
3. СТИЛЬ: простой инженерный русский язык, 4-6 предложений, без канцелярита, без
   markdown-заголовков и без внутреннего жаргона алгоритма согласования (не используй слова вроде
   "QP-ремонт", "dist=", "сигнатура", хэши кандидатов — переведи их смысл на обычный язык:
   "агенты скорректировали ход, чтобы он не нарушал ограничения" и т.п.).
4. СТРУКТУРА ОТВЕТА: строго валидный JSON по схеме ниже.

## 3. СХЕМА ОТВЕТА (JSON)
```json
{"report_markdown": "Связный текст на 4-6 предложений."}
```
"""


class XaiLLMReport(BaseModel):
    """Короткий LLM-отчёт с объяснением хода агентов для оператора."""

    report_markdown: str = Field(..., min_length=1)


def _format_rounds_block(events: Optional[List[Any]]) -> str:
    if not events:
        return "(журнал переговоров для этого такта пуст)"

    rounds: Dict[int, List[str]] = {}
    for ev in events:
        r = getattr(ev, "round", 0) or 0
        actor = getattr(ev, "actor", "") or ""
        kind = getattr(ev, "kind", "") or ""
        detail = getattr(ev, "detail", "") or ""
        candidate = getattr(ev, "candidate", None)
        cand_str = f" (кандидат {candidate[:8]})" if candidate else ""
        rounds.setdefault(r, []).append(f"[{actor}] {kind}: {detail}{cand_str}")

    lines: List[str] = []
    for r in sorted(rounds.keys()):
        lines.append(f"Раунд {r}:")
        for entry in rounds[r]:
            lines.append(f"  - {entry}")
    return "\n".join(lines)


def _format_alternative_block(alternative: Dict[str, Any]) -> str:
    label = alternative.get("label", "альтернатива")
    changes = alternative.get("changes") or []
    effect = alternative.get("effect") or {}

    lines = [f"Вариант «{label}» (Парето-точка, отличная от базовой рекомендации):"]
    if changes:
        lines.append("Изменения уставок относительно текущих значений:")
        for ch in changes:
            lines.append(
                f"  - {ch.get('title', ch.get('tag', ''))}: {ch.get('current')} -> {ch.get('target')} "
                f"({ch.get('delta'):+g} {ch.get('unit', '')})" if isinstance(ch.get("delta"), (int, float))
                else f"  - {ch.get('title', ch.get('tag', ''))}: {ch.get('current')} -> {ch.get('target')} {ch.get('unit', '')}"
            )
    else:
        lines.append("Изменения уставок: совпадают с базовой рекомендацией (нет отдельной точки).")

    eff_parts = []
    if effect.get("margin_delta_rub_h") is not None:
        eff_parts.append(f"изменение маржи {effect['margin_delta_rub_h']:+.0f} руб/ч")
    if effect.get("sulfur_4h") is not None:
        eff_parts.append(f"прогноз серы через 4ч {effect['sulfur_4h']:.1f} ppm")
    if effect.get("flash_margin_c") is not None:
        eff_parts.append(f"запас по вспышке {effect['flash_margin_c']:+.1f} °C")
    if eff_parts:
        lines.append("Ожидаемый эффект: " + ", ".join(eff_parts) + ".")

    return "\n".join(lines)


def generate_xai_report(
    card: DecisionCard,
    events: Optional[List[Any]] = None,
    alternative: Optional[Dict[str, Any]] = None,
    client: Optional[ReplayingOpenAIClient] = None,
) -> Optional[str]:
    """Генерирует короткий отчёт «почему агенты сделали такой ход».

    Без `alternative` — объясняет базовую (сбалансированную) рекомендацию такта, опираясь на
    журнал переговоров. С `alternative` (словарь {label, changes, effect} — см.
    src/console/contracts.py::Alternative) — объясняет именно эту Парето-альтернативу, которую
    оператор выбрал на пульте («Макс. маржа» и т.п.), а не заново пересказывает базовое решение.

    Возвращает текст отчёта либо None, если LLM недоступна (нет ключа/кассеты/режим OFF) или
    ответ не удалось разобрать — вызывающий код должен в этом случае просто не показывать отчёт.
    """
    llm_client = client or ReplayingOpenAIClient()

    if alternative is None:
        rounds_block = _format_rounds_block(events)
        user_content = (
            f"{card.markdown}\n\n"
            "=== ЖУРНАЛ ПЕРЕГОВОРОВ ТЕКУЩЕГО ТАКТА (по раундам) ===\n"
            f"{rounds_block}\n\n"
            "Составь короткий отчёт для оператора: почему агенты и ядро безопасности приняли именно "
            "такое решение в этом такте, какие ограничения и компромиссы были ключевыми, и были ли "
            "отклонены альтернативы (и почему).\n\n"
            'Ответь строго в формате JSON: {"report_markdown": "..."}'
        )
    else:
        alt_block = _format_alternative_block(alternative)
        user_content = (
            f"{card.markdown}\n\n"
            "=== ЗАПРОШЕННЫЙ ОПЕРАТОРОМ АЛЬТЕРНАТИВНЫЙ ВАРИАНТ ===\n"
            f"{alt_block}\n\n"
            "Объясни оператору именно этот альтернативный вариант (а не базовую рекомендацию выше): "
            "чем он отличается от базовой рекомендации, какой компромисс делает (например, больше "
            "маржи ценой меньшего запаса по сере, или наоборот) и в какой ситуации его стоит выбрать. "
            "Используй только цифры из блоков выше.\n\n"
            'Ответь строго в формате JSON: {"report_markdown": "..."}'
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = llm_client.create(messages=messages, response_format={"type": "json_object"})
        payload = extract_json_payload(resp.content)
        parsed = XaiLLMReport.model_validate(payload)
        return parsed.report_markdown
    except (CassetteNotFoundError, LLMDisabledError) as exc:
        logger.info("XAI LLM-отчёт недоступен: %s", exc)
        return None
    except Exception:
        logger.exception("Не удалось сгенерировать XAI LLM-отчёт")
        return None
