"""Координатор переговоров мультиагентной системы (Negotiation Coordinator).

Соответствует спецификации §5.8 implementation_plan_v3.md (Задача P3.4):
1. Раундовый цикл переговоров (r <= max_rounds = 4);
2. Узлы графа LangGraph: node_propose, node_predict, node_coordinate;
3. Вспомогательные узлы агентов: node_reliability, node_quality, node_supply, node_blending;
4. route_after_coordinate: маршрутизация в следующий раунд (next_round) или завершение (done);
5. merit_table и lexi_best: построение таблицы достоинств кандидатов и лексикографический выбор;
6. Отбор перспективных недопустимых кандидатов и совместный QP-ремонт qp_repair;
7. Локальное дробление шага refine_half_step вокруг лучшего кандидата;
8. Контроль мягкого бюджета времени (p95 <= 2.0 с);
9. Журналирование событий NegotiationEvent.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple
import numpy as np

from src.agents.candidates import DEFAULT_MVS, MVSpec
from src.agents.context import AgentContext, build_default_context
from src.agents.contracts import (
    BlendingCertificate,
    Candidate,
    CandidateOrigin,
    ConstraintCertificate,
    ConstraintEvaluation,
    ConstraintStatus,
    Merit,
    NegotiationEvent,
    Prediction,
    Tier,
)
from src.agents.economics import EconomicsEvaluator
from src.agents.generator import (
    dedupe_by_signature,
    hold,
    propose_initial,
    refine_half_step,
    signature_of,
)
from src.agents.policy import PolicyConfig
from src.agents.quality import QualityAgent
from src.agents.reliability import ReliabilityAgent
from src.agents.repair import qp_repair
from src.agents.state import CoreState
from src.agents.supply import SupplyAgent
from src.agents.twin_view import TwinView
from src.agents.blending import certify_blend
from src.twin.chain import FullChainTwin
from src.twin.params import load_params


def _extract_context(state: CoreState) -> AgentContext:
    """Извлекает или конструирует AgentContext из состояния CoreState."""
    policy = state.get("policy") or PolicyConfig()
    estimate = state.get("estimate")
    data = state.get("data")
    tags = state.get("raw_tags") or state.get("tags") or {}

    if estimate is None:
        from src.agents.context import build_default_context
        ctx_def = build_default_context(tags=tags, policy=policy, data=data)
        estimate = ctx_def.estimate
        if data is None:
            data = ctx_def.data

    twin_view = state.get("twin_view")
    if twin_view is None:
        twin = FullChainTwin(load_params())
        if estimate:
            twin.initialize(estimate.u_actual)
        elif tags:
            twin.initialize(tags)
        twin_view = TwinView(twin=twin, estimate=estimate, policy=policy)

    return AgentContext(
        estimate=estimate,
        data=data,
        policy=policy,
        twin_view=twin_view,
        round=state.get("round", 0),
        tags=tags,
    )


def calculate_merit(
    cand: Candidate,
    certs: Sequence[ConstraintCertificate],
    blend_cert: Optional[BlendingCertificate],
    pred: Optional[Prediction] = None,
    hold_pred: Optional[Prediction] = None,
    hold_blend_cert: Optional[BlendingCertificate] = None,
    ctx: AgentContext = None, # type: ignore
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
) -> Merit:
    """
    Рассчитывает критерии достоинства (Merit) кандидата:
    - v = (v0, v1, v2, v3): вектор нарушений ярусов;
    - utility_rub_h: экономическая полезность;
    - min_slack: минимальный запас устойчивости;
    - move_norm: нормированная норма вектора хода.
    """
    mv_dict = {mv.name: mv for mv in mvs}
    policy = ctx.policy

    # 1. Нарушение T0 (границы и скорость хода)
    v0 = 0.0
    u0 = ctx.estimate.u_actual if ctx.estimate else {}
    for name, delta in cand.delta_u.items():
        mv = mv_dict.get(name)
        if not mv:
            continue
        curr = float(u0.get(name, (mv.lo + mv.hi) / 2.0))
        target = curr + delta
        if target < mv.lo - 1e-4:
            v0 += (mv.lo - target) / mv.step
        elif target > mv.hi + 1e-4:
            v0 += (target - mv.hi) / mv.step
        if abs(delta) > mv.max_move + 1e-4:
            v0 += (abs(delta) - mv.max_move) / mv.step

    # 2. Нарушения T1, T2, T3 из сертификатов
    v1 = 0.0
    v2 = 0.0
    v3 = 0.0
    all_slacks: List[float] = []

    for cert in certs:
        for e in cert.evaluations:
            if e.slack is not None:
                all_slacks.append(e.slack)
                if e.slack < -1e-5:
                    viol = abs(e.slack)
                    if e.tier == Tier.T1_EQUIPMENT:
                        v1 += viol
                    elif e.tier == Tier.T2_QUALITY:
                        v2 += viol
                    elif e.tier == Tier.T3_OPERATIONAL:
                        v3 += viol
            elif e.status == ConstraintStatus.UNKNOWN:
                # Отсутствие данных в применимом ярусе -> inf
                if e.tier == Tier.T1_EQUIPMENT:
                    v1 = float("inf")
                elif e.tier == Tier.T2_QUALITY:
                    v2 = float("inf")
                elif e.tier == Tier.T3_OPERATIONAL:
                    v3 = float("inf")

    # Товарные ограничения блендинга T2 (ADR-20, Q14, E8, I8)
    # По ADR-20 / I8: невыполнимость смешения не ветирует ходы ГО (не добавляется в v2).
    # Штраф за нарушение товарных ограничений смеси учитывается в utility через elastic_penalty.

    # 3. Экономическая полезность
    econ = EconomicsEvaluator(policy=policy)
    ss = pred.steady_state if pred else {}
    ss_hold = hold_pred.steady_state if hold_pred else ss
    utility = econ.utility(ss=ss, ss_hold=ss_hold, cand=cand, blending_certificate=blend_cert, policy=policy)

    # 4. Минимальный запас
    min_slack = min(all_slacks) if all_slacks else 1.0

    # 5. Норма хода
    move_norm = 0.0
    for name, delta in cand.delta_u.items():
        step = mv_dict[name].step if name in mv_dict else 1.0
        move_norm += (delta / step) ** 2
    move_norm = math.sqrt(move_norm)

    return Merit(
        v=(round(v0, 4), round(v1, 4), round(v2, 4), round(v3, 4)),
        utility_rub_h=round(utility, 2),
        min_slack=round(min_slack, 4),
        move_norm=round(move_norm, 4),
    )


def merit_table(state: CoreState) -> Dict[str, Tuple[Candidate, Merit]]:
    """Формирует таблицу (Candidate, Merit) для всех оцененных кандидатов состояния."""
    ctx = _extract_context(state)
    candidates = state.get("candidates", {})
    predictions = state.get("predictions", {})
    certificates = state.get("certificates", {})
    blending = state.get("blending", {})

    hold_cand = hold()
    hold_sig = hold_cand.signature
    hold_pred = predictions.get(hold_sig)

    table: Dict[str, Tuple[Candidate, Merit]] = {}

    for sig, cand in candidates.items():
        pred = predictions.get(sig)
        blend_cert = blending.get(sig)

        # Собираем сертификаты всех агентов
        cand_certs: List[ConstraintCertificate] = []
        for agent in ("reliability", "quality", "supply"):
            cert_key = f"{sig}|{agent}"
            if cert_key in certificates:
                cand_certs.append(certificates[cert_key])

        m = calculate_merit(
            cand=cand,
            certs=cand_certs,
            blend_cert=blend_cert,
            pred=pred,
            hold_pred=hold_pred,
            hold_blend_cert=blending.get(hold_sig),
            ctx=ctx,
        )
        table[sig] = (cand, m)

    return table


def lexi_best(table: Mapping[str, Tuple[Candidate, Merit]]) -> Tuple[Candidate, Merit]:
    """
    Находит лучшего кандидата по лексикографическому порядку:
    1. min v = (v0, v1, v2, v3);
    2. max utility_rub_h;
    3. max min_slack;
    4. min move_norm.
    """
    items = list(table.values())
    if not items:
        h = hold()
        return h, Merit(v=(0, 0, 0, 0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0)

    def sort_key(item: Tuple[Candidate, Merit]):
        c, m = item
        # Лексикографическое сравнение:
        # v минимизируется
        # полезность максимизируется (-utility)
        # минимальный запас максимизируется (-min_slack)
        # норма хода минимизируется (+move_norm)
        return (m.v, -m.utility_rub_h, -m.min_slack, m.move_norm)

    items.sort(key=sort_key)
    return items[0]


# =============================================================================
# Узлы графа LangGraph
# =============================================================================

def node_propose(state: CoreState) -> dict:
    """Узел 0: генерация начального переговорного пула кандидатов."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    initial_candidates = propose_initial(ctx)

    c_map = {c.signature: c for c in initial_candidates}
    frontier = [c.signature for c in initial_candidates]

    event = NegotiationEvent(
        round=0,
        kind="PROPOSED",
        actor="coordinator",
        candidate=None,
        detail=f"Начальный пул: {len(initial_candidates)} кандидатов (HOLD, LOCAL, GLOBAL)",
    )

    return {
        "round": 0,
        "candidates": c_map,
        "frontier": frontier,
        "negotiation_log": [event],
        "timings_ms": {"propose": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_predict(state: CoreState) -> dict:
    """Узел: расчет динамических и установившихся прогнозов для кандидатов frontier."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    twin_view = ctx.twin_view
    frontier = state.get("frontier", [])
    candidates = state.get("candidates", {})
    existing_preds = state.get("predictions", {})

    new_preds: Dict[str, Prediction] = {}
    hold_sig = signature_of({})

    for sig in frontier:
        if sig not in existing_preds and sig in candidates:
            cand = candidates[sig]
            store_traj = (sig == hold_sig)
            pred = twin_view.predict(cand, store_traj=store_traj)
            new_preds[sig] = pred

    return {
        "predictions": new_preds,
        "timings_ms": {"predict": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_reliability(state: CoreState) -> dict:
    """Узел агента Надежности."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    agent = ReliabilityAgent()
    frontier = state.get("frontier", [])
    candidates = state.get("candidates", {})
    predictions = state.get("predictions", {})
    hold_pred = predictions.get(signature_of({}))

    new_certs: Dict[str, ConstraintCertificate] = {}
    for sig in frontier:
        cand = candidates.get(sig)
        pred = predictions.get(sig)
        if cand and pred and hold_pred:
            cert = agent.certify(cand, pred, hold_pred, ctx)
            new_certs[f"{sig}|reliability"] = cert

    return {
        "certificates": new_certs,
        "timings_ms": {"reliability": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_quality(state: CoreState) -> dict:
    """Узел агента Качества."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    agent = QualityAgent()
    frontier = state.get("frontier", [])
    candidates = state.get("candidates", {})
    predictions = state.get("predictions", {})
    hold_pred = predictions.get(signature_of({}))

    new_certs: Dict[str, ConstraintCertificate] = {}
    for sig in frontier:
        cand = candidates.get(sig)
        pred = predictions.get(sig)
        if cand and pred and hold_pred:
            cert = agent.certify(cand, pred, hold_pred, ctx)
            new_certs[f"{sig}|quality"] = cert

    return {
        "certificates": new_certs,
        "timings_ms": {"quality": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_supply(state: CoreState) -> dict:
    """Узел агента Баланса Сырья."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    agent = SupplyAgent()
    frontier = state.get("frontier", [])
    candidates = state.get("candidates", {})
    predictions = state.get("predictions", {})
    hold_pred = predictions.get(signature_of({}))

    new_certs: Dict[str, ConstraintCertificate] = {}
    for sig in frontier:
        cand = candidates.get(sig)
        pred = predictions.get(sig)
        if cand and pred and hold_pred:
            cert = agent.certify(cand, pred, hold_pred, ctx)
            new_certs[f"{sig}|supply"] = cert

    return {
        "certificates": new_certs,
        "timings_ms": {"supply": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_blending(state: CoreState) -> dict:
    """Узел агента Блендинга."""
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    frontier = state.get("frontier", [])
    candidates = state.get("candidates", {})
    certificates = state.get("certificates", {})
    tanks = state.get("tanks") or {}
    policy = ctx.policy

    new_blends: Dict[str, BlendingCertificate] = {}
    for sig in frontier:
        cand = candidates.get(sig)
        q_cert = certificates.get(f"{sig}|quality")
        if cand and q_cert:
            godt_fc = q_cert.forecasts
            blend_cert = certify_blend(cand, godt_fc, tanks=tanks, policy=policy)
            new_blends[sig] = blend_cert

    return {
        "blending": new_blends,
        "timings_ms": {"blending": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def node_coordinate(state: CoreState) -> dict:
    """
    Узел Координатора:
    - Построение merit_table;
    - Лексикографический поиск лучшего кандидата;
    - Проверка условий остановки (бюджет, макс. раундов);
    - Отбор до 6 перспективных кандидатов на QP-ремонт qp_repair;
    - Локальное дробление refine_half_step;
    - Формирование frontier следующего раунда.
    """
    t_start = time.perf_counter()
    ctx = _extract_context(state)
    r = state.get("round", 0)
    policy = ctx.policy

    table = merit_table(state)
    best_cand, best_merit = lexi_best(table)

    events: List[NegotiationEvent] = [
        NegotiationEvent(
            round=r,
            kind="BEST_UPDATED",
            actor="coordinator",
            candidate=best_cand.signature,
            detail=f"Раунд {r}: лучший v={best_merit.v}, полезность={best_merit.utility_rub_h} руб/ч",
        )
    ]

    stop_reason: Optional[str] = None
    if r + 1 >= policy.max_rounds:
        stop_reason = "CONVERGED"
    elif (time.perf_counter() - state.get("t_cycle_start", t_start)) > policy.soft_budget_s:
        stop_reason = "BUDGET_EXHAUSTED"

    new_frontier: List[Candidate] = []
    if stop_reason is None:
        # 1. Отбор до 6 перспективных недопустимых кандидатов для ремонта
        infeasible = [item for item in table.values() if item[1].v != (0, 0, 0, 0)]
        # Сортируем: сначала наименьшее нарушение старших ярусов, затем высокая полезность
        infeasible.sort(key=lambda x: (x[1].v, -x[1].utility_rub_h))
        promising = infeasible[:6]

        for cand, _ in promising:
            # Собираем все оценки от всех агентов
            evals: List[ConstraintEvaluation] = []
            for agent in ("reliability", "quality", "supply"):
                cert = state.get("certificates", {}).get(f"{cand.signature}|{agent}")
                if cert:
                    evals.extend(cert.evaluations)
            rep = qp_repair(cand, evals, ctx)
            if rep and rep.delta_u:
                rep_cand = Candidate(
                    signature=signature_of(rep.delta_u),
                    delta_u=rep.delta_u,
                    origin=CandidateOrigin.REPAIR,
                    proposed_by="repair",
                    parent=cand.signature,
                    round=r + 1,
                )
                new_frontier.append(rep_cand)
                events.append(
                    NegotiationEvent(
                        round=r + 1,
                        kind="REPAIR_COMPOSED",
                        actor="repair",
                        candidate=rep_cand.signature,
                        detail=f"Совместный QP-ремонт для {cand.signature[:8]} (dist={rep.distance:.3f})",
                    )
                )

        # 2. Дробление шага 1/2 вокруг лучшего кандидата
        refined = refine_half_step(best_cand, ctx)
        new_frontier.extend(refined)

        # 3. Дедупликация и фильтрация уже существующих
        existing_sigs = set(state.get("candidates", {}).keys())
        unique_new = [c for c in dedupe_by_signature(new_frontier) if c.signature not in existing_sigs]

        if not unique_new:
            stop_reason = "NO_NEW_CANDIDATES"
        else:
            new_candidates_map = {c.signature: c for c in unique_new}
            return {
                "round": r + 1,
                "frontier": [c.signature for c in unique_new],
                "candidates": new_candidates_map,
                "negotiation_log": events,
                "timings_ms": {"coordinate": round((time.perf_counter() - t_start) * 1000, 2)},
            }

    # Остановка
    events.append(
        NegotiationEvent(
            round=r,
            kind=stop_reason,
            actor="coordinator",
            candidate=best_cand.signature,
            detail=f"Переговоры завершены: причина={stop_reason}",
        )
    )

    return {
        "frontier": [],
        "negotiation_log": events,
        "timings_ms": {"coordinate": round((time.perf_counter() - t_start) * 1000, 2)},
    }


def route_after_coordinate(state: CoreState) -> Literal["next_round", "done"]:
    """Маршрутизатор после координатора: если есть новые кандидаты в frontier - следующий раунд."""
    frontier = state.get("frontier")
    return "next_round" if frontier else "done"
