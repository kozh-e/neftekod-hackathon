"""Инструменты (Tools) прямого чтения данных технологического комплекса для LLM-супервизора.

Все инструменты строго read-only (только чтение), работают со снимком
DecisionStore и возвращают компактные детерминированные словари.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime

from src.agents.decision_store import DecisionStore, DEFAULT_STORE
from src.agents.policy import PolicyStore, POLICY_WHITELIST
from src.agents.registry import REGISTRY_BY_KEY


def get_calibration_history(
    prop: str,
    window_h: float = 168.0,
    store: Optional[DecisionStore] = None,
) -> Dict[str, Any]:
    """Возвращает историю калибровки показателя (смещение bias, неопределенность sigma, возраст) с ограничением до 200 точек."""
    store = store or DEFAULT_STORE
    traces = store.list_recent(limit=200)

    quality_key = f"GODT.{prop.upper()}"
    points = []
    for tr in reversed(traces):
        if tr.estimate and quality_key in tr.estimate.quality:
            q = tr.estimate.quality[quality_key]
            points.append({
                "cycle_id": tr.cycle_id,
                "timestamp": tr.t.isoformat() if hasattr(tr.t, "isoformat") else str(tr.t),
                "value": round(q.value, 3),
                "sigma_calib": round(q.sigma_calib, 4),
                "calib_age_h": round(q.calib_age_h, 2),
                "anchor": q.anchor,
            })

    # Ограничиваем не более 200 точек
    if len(points) > 200:
        step = len(points) // 200 + 1
        points = points[::step]

    return {
        "prop": prop,
        "total_points": len(points),
        "history": points,
    }


def get_constraint_activity(
    window_h: float = 72.0,
    spec_key: Optional[str] = None,
    store: Optional[DecisionStore] = None,
) -> Dict[str, Any]:
    """Возвращает активность технологических ограничений: долю активных/нарушенных тактов и перцентили запаса."""
    store = store or DEFAULT_STORE
    traces = store.list_recent(limit=150)

    spec_stats: Dict[str, Dict[str, Any]] = {}

    for tr in traces:
        for cert in tr.certificates:
            key = cert.spec_key
            if spec_key and key != spec_key:
                continue
            if key not in spec_stats:
                spec_stats[key] = {"active_count": 0, "violated_count": 0, "total": 0, "margins": []}

            spec_stats[key]["total"] += 1
            if cert.active:
                spec_stats[key]["active_count"] += 1
            if cert.status.value == "VIOLATED":
                spec_stats[key]["violated_count"] += 1
            if cert.margin is not None:
                spec_stats[key]["margins"].append(cert.margin)

    summary = {}
    for key, st in spec_stats.items():
        total = st["total"] or 1
        margins = sorted(st["margins"])
        p05 = round(margins[int(len(margins) * 0.05)], 3) if margins else None
        p50 = round(margins[int(len(margins) * 0.50)], 3) if margins else None
        summary[key] = {
            "active_ratio": round(st["active_count"] / total, 3),
            "violated_ratio": round(st["violated_count"] / total, 3),
            "margin_p05": p05,
            "margin_median": p50,
            "total_cycles": total,
        }

    return {"window_h": window_h, "constraints": summary}


def get_cycle_trace(
    cycle_id: str,
    store: Optional[DecisionStore] = None,
) -> Dict[str, Any]:
    """Возвращает сжатую трассу конкретного цикла оптимизации."""
    store = store or DEFAULT_STORE
    trace = store.get(cycle_id)
    if not trace:
        return {"error": f"Cycle trace for '{cycle_id}' not found"}

    active_certs = [
        {"spec": c.spec_key, "margin": round(c.margin, 3) if c.margin is not None else None, "status": c.status.value}
        for c in trace.certificates if c.active or c.status.value == "VIOLATED"
    ]

    return {
        "cycle_id": trace.cycle_id,
        "timestamp": trace.t.isoformat() if hasattr(trace.t, "isoformat") else str(trace.t),
        "status": trace.decision.status.value if hasattr(trace.decision.status, "value") else str(trace.decision.status),
        "delta_u": {k: round(v, 4) for k, v in trace.decision.delta_u.items()},
        "active_constraints": active_certs,
        "kernel_passed": trace.kernel.passed if trace.kernel else True,
        "refusal_text": trace.decision.refusal_text,
        "reason_codes": list(trace.decision.reason_codes),
    }


def get_lims_vs_pak(
    prop: str = "S",
    window_h: float = 168.0,
    store: Optional[DecisionStore] = None,
) -> Dict[str, Any]:
    """Возвращает пары измерений ЛИМС и поточного анализатора ПАК на момент пробоотбора."""
    store = store or DEFAULT_STORE
    traces = store.list_recent(limit=100)

    quality_key = f"GODT.{prop.upper()}"
    pairs = []
    for tr in traces:
        if tr.estimate and quality_key in tr.estimate.quality:
            q = tr.estimate.quality[quality_key]
            # Если проба ЛИМС была взята
            if q.last_lims_sampled_at:
                pairs.append({
                    "cycle_id": tr.cycle_id,
                    "sampled_at": q.last_lims_sampled_at.isoformat() if hasattr(q.last_lims_sampled_at, "isoformat") else str(q.last_lims_sampled_at),
                    "estimated_value": round(q.value, 3),
                    "sigma_calib": round(q.sigma_calib, 3),
                    "age_h": round(q.calib_age_h, 1),
                })
        if len(pairs) >= 20:
            break

    return {"prop": prop, "pairs_count": len(pairs), "pairs": pairs}


def get_open_findings(store: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Возвращает список открытых находок и предупреждений супервизора."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    target_store = store or DEFAULT_SUPERVISOR_STORE
    return [f.model_dump() for f in target_store.list_findings(status="OPEN")]


def get_policy() -> Dict[str, Any]:
    """Возвращает текущую политику, белый список допустимых изменений и историю."""
    policy_store = PolicyStore()
    active = policy_store.active_policy

    whitelist_data = [
        {"field": w.field, "lo": w.lo, "hi": w.hi, "direction": w.direction}
        for w in POLICY_WHITELIST
    ]

    return {
        "active_policy": active.model_dump(),
        "whitelist": whitelist_data,
    }


def list_decisions(
    window_h: float = 72.0,
    status: Optional[str] = None,
    limit: int = 100,
    store: Optional[DecisionStore] = None,
) -> List[Dict[str, Any]]:
    """Возвращает краткий список последних решений автоматики."""
    store = store or DEFAULT_STORE
    traces = store.list_recent(limit=limit)

    results = []
    for tr in traces:
        st = tr.decision.status.value if hasattr(tr.decision.status, "value") else str(tr.decision.status)
        if status and st != status:
            continue
        results.append({
            "cycle_id": tr.cycle_id,
            "timestamp": tr.t.isoformat() if hasattr(tr.t, "isoformat") else str(tr.t),
            "status": st,
            "kernel_passed": tr.kernel.passed if tr.kernel else True,
        })
    return results


def explain_constraint(spec_key: str) -> Dict[str, Any]:
    """Возвращает паспорт и физический смысл ограничения из единого технологического реестра."""
    if spec_key not in REGISTRY_BY_KEY:
        return {"error": f"Спецификация '{spec_key}' не найдена в едином технологическом реестре."}

    spec = REGISTRY_BY_KEY[spec_key]
    prov = spec.provenance
    return {
        "key": spec.key,
        "label": spec.label,
        "owner": spec.owner,
        "tier": spec.tier.name if hasattr(spec.tier, "name") else str(spec.tier),
        "quantity": spec.quantity,
        "sense": spec.sense,
        "limit": spec.limit,
        "unit": spec.unit,
        "provenance": {
            "kind": prov.kind.value if hasattr(prov.kind, "value") else str(prov.kind),
            "ref": prov.ref,
            "note": prov.note,
        },
    }
