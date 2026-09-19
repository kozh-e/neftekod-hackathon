"""Формирование ленты событий и действий оператора и агентов (R1).

Преобразует протокол переговоров МАС, вердикты ядра и предупреждения DataGuard в элементы FeedItem.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.console.contracts import FeedItem


def feed_from_graph_result(graph_result: Dict[str, Any], t: datetime) -> List[FeedItem]:
    """
    Превращает negotiation_log, kernel.checks, вето агентов и предупреждения DataGuard в FeedItem.
    Один цикл графа дает не более 3 ключевых записей (приоритет: shield > veto > warn > info).
    ID детерминированный: {cycle_id}:{index}.
    """
    items: List[FeedItem] = []
    cycle_id = graph_result.get("cycle", {}).get("cycle_id") or "cycle"
    t_str = t.strftime("%H:%M")

    # 1. Проверки ядра безопасности (shield)
    kernel = graph_result.get("kernel")
    if kernel:
        checks = getattr(kernel, "checks", ())
        if isinstance(checks, dict):
            checks = checks.values()
        for c in checks:
            passed = getattr(c, "passed", True)
            name = getattr(c, "name", "")
            detail = getattr(c, "detail", "")
            if not passed:
                items.append(
                    FeedItem(
                        id=f"{cycle_id}:shield:{len(items)}",
                        t=t_str,
                        kind="shield",
                        text=f"Ядро безопасности отклонило ход по {name}: {detail}",
                        source="kernel",
                    )
                )

    # 2. Переговоры и вето агентов (veto / shield)
    neg_events = graph_result.get("negotiation_log", ()) or graph_result.get("negotiation", ())
    for ev in neg_events:
        kind = getattr(ev, "kind", "")
        actor = getattr(ev, "actor", "")
        detail = getattr(ev, "detail", "")
        if "VETO" in kind or "REJECT" in kind or "OVERRIDE" in kind:
            items.append(
                FeedItem(
                    id=f"{cycle_id}:veto:{len(items)}",
                    t=t_str,
                    kind="veto",
                    text=f"Агент {actor} наложил вето: {detail}",
                    source=actor,
                )
            )
        elif "REPAIR" in kind or "LIMITED" in kind:
            items.append(
                FeedItem(
                    id=f"{cycle_id}:shield:{len(items)}",
                    t=t_str,
                    kind="shield",
                    text=f"Срезание шага ({actor}): {detail}",
                    source=actor,
                )
            )

    # 3. Предупреждения качества данных DataGuard (warn)
    dq = graph_result.get("data_quality")
    if dq:
        reasons = getattr(dq, "reasons", ())
        for r in reasons:
            items.append(
                FeedItem(
                    id=f"{cycle_id}:warn:{len(items)}",
                    t=t_str,
                    kind="warn",
                    text=f"Предупреждение DataGuard: {r}",
                    source="DataGuard",
                )
            )

    # 4. Если нет критических событий — информационная запись о статусе решения (info)
    if not items:
        final_rec = graph_result.get("final_recommendation")
        status = getattr(final_rec, "status", "SUCCESS")
        explanation = getattr(final_rec, "explanation", "Плановый цикл оптимизации")
        items.append(
            FeedItem(
                id=f"{cycle_id}:info:0",
                t=t_str,
                kind="info",
                text=f"Рекомендация {status}: {explanation[:120]}",
                source="system",
            )
        )

    # Ограничение: не более 3 записей за цикл
    return items[:3]
