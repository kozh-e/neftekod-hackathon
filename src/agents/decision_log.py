"""Журнал технологических решений и аудита мультиагентной системы (Decision Log).

Реализует требование 1.E.4 ТЗ и implementation_plan_v2.md (T6.3):
Фиксация полного контекста каждого цикла оптимизации в формате JSONL:
- Входные данные (телеметрия/теги, возраст LIMS);
- Предупреждения и индекс достоверности (confidence);
- Оценки аудиторов и перечень кандидатов;
- Парето-фронт допустимых кандидатов, их метрики и статусы (фронт / доминируемые / вето);
- Итоговая рекомендация и оптимальная рецептура блендинга.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional


def append_decision(
    result: Mapping[str, Any],
    inputs: Mapping[str, Any],
    path: Optional[str | Path] = None,
) -> Optional[Path]:
    """
    Записывает транзакцию принятия решения в JSONL журнал.
    При NEFTEKOD_DECISION_LOG="off" запись пропускается.
    """
    env_path = os.environ.get("NEFTEKOD_DECISION_LOG")
    if env_path == "off" and path is None:
        return None

    target_path = Path(path if path is not None else (env_path or "data/decisions/decisions.jsonl"))
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Сериализация кандидатов
    cands_serialized = []
    for c in result.get("candidates", []):
        if hasattr(c, "candidate_id"):
            cands_serialized.append({
                "candidate_id": c.candidate_id,
                "delta_u": c.delta_u,
                "expected_margin": getattr(c, "expected_margin", 0.0),
                "is_hold": getattr(c, "is_hold", False),
                "expected_sulfur": getattr(c, "expected_sulfur", None),
                "expected_flash": getattr(c, "expected_flash", None),
                "steady_state": getattr(c, "steady_state", {}),
            })
        elif isinstance(c, dict):
            cands_serialized.append(c)

    # Сериализация отчетов аудита
    audits_serialized = []
    for r in result.get("audit_reports", []):
        if hasattr(r, "candidate_id"):
            audits_serialized.append({
                "candidate_id": r.candidate_id,
                "agent": getattr(r, "agent", "unknown"),
                "is_vetoed": r.is_vetoed,
                "risk_penalty_rub_h": getattr(r, "risk_penalty_rub_h", 0.0),
                "violation_reason": getattr(r, "violation_reason", None),
                "violated_limits": getattr(r, "violated_limits", []),
            })
        elif isinstance(r, dict):
            audits_serialized.append(r)

    # Сериализация финальной рекомендации
    final_rec = result.get("final_recommendation")
    rec_serialized = None
    if final_rec is not None:
        if hasattr(final_rec, "status"):
            rec_serialized = {
                "status": final_rec.status,
                "explanation": final_rec.explanation,
                "recommended_delta_u": final_rec.recommended_delta_u,
            }
        elif isinstance(final_rec, dict):
            rec_serialized = final_rec

    # Сериализация блендинга
    blend_recipe = result.get("blending_recipe")
    blend_serialized = None
    if blend_recipe is not None:
        if hasattr(blend_recipe, "success"):
            blend_serialized = {
                "success": blend_recipe.success,
                "fractions": blend_recipe.fractions if hasattr(blend_recipe, "fractions") else {},
                "cost_per_ton": getattr(blend_recipe, "cost_per_ton", 0.0),
                "binding_constraints": getattr(blend_recipe, "binding_constraints", []),
            }
        elif isinstance(blend_recipe, dict):
            blend_serialized = blend_recipe

    pareto = result.get("pareto")
    pareto_serialized = pareto.model_dump(mode="json") if hasattr(pareto, "model_dump") else pareto

    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": inputs.get("session_id") or result.get("session_id"),
        "inputs": dict(inputs),
        "twin_warnings": result.get("twin_warnings", []),
        "confidence": result.get("confidence", {}),
        "candidates": cands_serialized,
        "audit_reports": audits_serialized,
        "alternatives": result.get("alternatives", []),
        "pareto": pareto_serialized,
        "final_recommendation": rec_serialized,
        "blending_recipe": blend_serialized,
    }

    with open(target_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return target_path
