"""Детерминированный лексикографический арбитраж v3 (ADR-15, ADR-16, §5.10).

Строго соблюдает приоритет ярусов T0/T1 (безопасность) > T2 (качество) > T3 (снабжение/опер.)
> экономика (см. decide()). Формирует ArbitrationDecision, список альтернатив для XAI и план
восстановления при нарушении hold (RecoveryPlanner).

Легаси-движок MVP (ArbitrationNode/node_arbitration, implementation_plan_v2.md, Шаг 4 дорожной
карты) удалён вместе с auditors.py/optimization.py (аудит 2026-09-20, не вызывался из
build_core_graph()) — эквивалентная логика здесь, в decide().
"""

from __future__ import annotations

import time
from typing import Any, List, Mapping, Sequence, Tuple

from src.agents.contracts import (
    Alternative,
    ArbitrationDecision,
    AutomationLevel,
    Candidate,
    DecisionStatus,
    Merit,
    Tier,
)
from src.agents.furnace_ensemble import evaluate_furnace_move
from src.agents.generator import hold, signature_of
from src.agents.negotiation import merit_table, _extract_context
from src.agents.policy import PolicyConfig
from src.agents.recovery import RecoveryPlanner

TZ_REFUSAL_DATA_TEXT = (
    "Внимание! Оптимизация невозможна ввиду недостоверности или недостаточности данных. "
    "Управление переведено в режим удержания текущих уставок. Требуется вмешательство оператора."
)

TZ_REFUSAL_NO_SAFE_ACTION_TEXT = (
    "Внимание! Автоматическая коррекция режима невозможна: отсутствует допустимое безопасное "
    "управляющее воздействие. Управление переведено в режим удержания текущих уставок. "
    "Требуется немедленное вмешательство оператора."
)


def highest_violated_tier(merit: Merit) -> int:
    """Возвращает индекс наивысшего (наиболее критичного) нарушенного яруса (0..3)."""
    for t in range(4):
        if merit.v[t] > 1e-4:
            return t
    return 3


def _corrective_only_admissible(cand: Candidate, level: AutomationLevel) -> bool:
    """
    В режиме CORRECTIVE_ONLY запрещено наращивание расхода сырья HT_FEED_SP (§5.3) —
    воспроизводит правило legacy ArbitrationNode.execute() (строки 88-93, 112 до Strangler),
    которое в v3-арбитраже (decide()) не переприменялось для веток hold-violated
    (SUCCESS_CORRECTIVE/RECOVERY_ADVISORY).
    """
    if level == AutomationLevel.CORRECTIVE_ONLY:
        return cand.delta_u.get("HT_FEED_SP", 0.0) <= 1e-9
    return True


def economic_move_allowed(cand: Candidate, level: AutomationLevel, ctx: Any) -> bool:
    """
    Проверяет, разрешено ли управляющее воздействие экономической оптимизации:
    1. В CORRECTIVE_ONLY или REFUSAL_DATA разрешен только hold;
    2. Ход с изменением уставки печи AVT_T55_SP проверяется на ансамбле откликов печи.
    """
    if level in (AutomationLevel.CORRECTIVE_ONLY, AutomationLevel.REFUSAL_DATA):
        return not cand.delta_u

    # Проверка ходов по перегибу печи АВТ
    if "AVT_T55_SP" in cand.delta_u and abs(cand.delta_u["AVT_T55_SP"]) > 1e-4:
        twin_view = getattr(ctx, "twin_view", None)
        policy = getattr(ctx, "policy", None) or PolicyConfig()
        if twin_view is not None:
            verdict = evaluate_furnace_move(cand, twin_view, policy)
            if not verdict.economic_move_allowed:
                return False

    return True


def pareto_v3_front(
    candidates: Sequence[Candidate],
    table: Mapping[str, Tuple[Candidate, Merit]],
    ctx: Any,
) -> Tuple[str, ...]:
    """
    Выделяет Парето-фронт среди полностью допустимых кандидатов (v = 0)
    по целям T3: полезность (max), мин. запас (max), норма хода (min).
    """
    if not candidates:
        return ()

    # Точки для Парето-анализа
    points = []
    for c in candidates:
        cand, m = table[c.signature]
        # Вектор для минимизации: (-utility, -min_slack, move_norm)
        pt = (-m.utility_rub_h, -m.min_slack, m.move_norm)
        points.append((c.signature, pt))

    # Простое недоминируемое выделение
    front_sigs: List[str] = []
    for i, (sig_i, pt_i) in enumerate(points):
        dominated = False
        for j, (sig_j, pt_j) in enumerate(points):
            if i == j:
                continue
            # pt_j доминирует pt_i если pt_j <= pt_i по всем целям и < хотя бы по одной
            if all(pt_j[k] <= pt_i[k] + 1e-5 for k in range(3)) and any(pt_j[k] < pt_i[k] - 1e-5 for k in range(3)):
                dominated = True
                break
        if not dominated:
            front_sigs.append(sig_i)

    return tuple(front_sigs)


def build_alternatives(
    table: Mapping[str, Tuple[Candidate, Merit]],
    front_sigs: Sequence[str],
    selected_cand: Candidate,
    hold_cand: Candidate,
) -> Tuple[Alternative, ...]:
    """Формирует список объясняющих альтернатив для XAI-карточки."""
    alts: List[Alternative] = []

    # 1. Альтернативы на Парето-фронте
    for sig in front_sigs:
        if sig != selected_cand.signature and sig in table:
            c, m = table[sig]
            alts.append(
                Alternative(
                    candidate=sig,
                    kind="pareto_more_margin" if m.utility_rub_h > (table[selected_cand.signature][1].utility_rub_h) else "pareto_more_margin",
                    delta_u=c.delta_u,
                    utility_rub_h=m.utility_rub_h,
                    reasons=(f"Парето-альтернатива: запас {m.min_slack:.2f}, полезность {m.utility_rub_h:.1f} руб/ч",),
                )
            )

    # 2. Недопустимые с максимальной маржой (почему отклонены)
    infeasible = [item for item in table.values() if item[1].v != (0, 0, 0, 0)]
    if infeasible:
        infeasible.sort(key=lambda x: -x[1].utility_rub_h)
        top_inf_cand, top_inf_merit = infeasible[0]
        alts.append(
            Alternative(
                candidate=top_inf_cand.signature,
                kind="rejected_best_margin",
                delta_u=top_inf_cand.delta_u,
                utility_rub_h=top_inf_merit.utility_rub_h,
                reasons=(f"Отклонен: нарушение ярусов v={top_inf_merit.v}",),
            )
        )

    return tuple(alts[:5])


def decide(state: CoreState) -> ArbitrationDecision:
    """
    Главная процедура арбитража детерминированного ядра МАС (§5.10):
    1. Проверка уровня автоматизации (REFUSAL_DATA -> отказ);
    2. Если hold допустим: экономическая оптимизация с проверкой deadband;
    3. Если hold нарушен:
       - Если есть допустимые ходы: выбор чисто по мин. запасу (SUCCESS_CORRECTIVE, экономика выключена);
       - Если допустимых нет: поиск улучшающих ходов восстановления (RECOVERY_ADVISORY);
       - Если улучшающих нет: отказ REFUSAL_NO_SAFE_ACTION.
    """
    ctx = _extract_context(state)
    policy = ctx.policy
    data = ctx.data
    level = data.automation_level if data else AutomationLevel.FULL

    table = merit_table(state)
    hold_sig = signature_of({})
    if hold_sig in table:
        hold_cand, hold_merit = table[hold_sig]
    else:
        hold_cand = hold()
        hold_merit = Merit(v=(0, 0, 0, 0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0)

    # 1. Отказ по качеству данных
    if level == AutomationLevel.REFUSAL_DATA:
        return ArbitrationDecision(
            status=DecisionStatus.REFUSAL_DATA,
            selected=None,
            delta_u={},
            merit=None,
            hold_merit=hold_merit,
            refusal_text=TZ_REFUSAL_DATA_TEXT,
            reason_codes=tuple(data.reasons if data else ()),
        )

    # Допустимые кандидаты с учетом допуска на шум, дрейф калибровки и машинную погрешность (0.05 scale)
    FEASIBILITY_TOL = 0.05
    feasible = [
        (c, m) for c, m in table.values()
        if all(vi <= FEASIBILITY_TOL for vi in m.v) and _corrective_only_admissible(c, level)
    ]
    hold_feasible = all(vi <= FEASIBILITY_TOL for vi in hold_merit.v)

    # 2. Hold полностью допустим -> Экономическая оптимизация
    if hold_feasible:
        allowed = [
            (c, m) for c, m in feasible
            if economic_move_allowed(c, level, ctx)
        ]
        if not any(c.signature == hold_cand.signature for c, _ in allowed):
            allowed.append((hold_cand, hold_merit))

        best_cand, best_merit = max(allowed, key=lambda cm: (cm[1].utility_rub_h, cm[1].min_slack, -cm[1].move_norm))
        front_sigs = pareto_v3_front([c for c, _ in allowed], table, ctx)
        alts = build_alternatives(table, front_sigs, best_cand, hold_cand)

        # Проверка зоны нечувствительности (Deadband Filter)
        is_hold = (best_cand.signature == hold_cand.signature)
        margin_gain = best_merit.utility_rub_h - hold_merit.utility_rub_h
        if is_hold or margin_gain < policy.deadband_rub_h or best_merit.move_norm < policy.min_move_norm:
            return ArbitrationDecision(
                status=DecisionStatus.NO_CHANGE_DEADBAND,
                selected=hold_cand.signature,
                delta_u={},
                merit=hold_merit,
                hold_merit=hold_merit,
                alternatives=alts,
                pareto_front=front_sigs,
            )

        return ArbitrationDecision(
            status=DecisionStatus.SUCCESS,
            selected=best_cand.signature,
            delta_u=best_cand.delta_u,
            merit=best_merit,
            hold_merit=hold_merit,
            alternatives=alts,
            pareto_front=front_sigs,
        )

    # 3. Hold нарушен -> Экономика строго выключена!
    if feasible:
        # Выбираем допустимого кандидата с максимальным запасом (наиболее безопасного)
        best_cand, best_merit = max(feasible, key=lambda cm: (cm[1].min_slack, -cm[1].move_norm))
        alts = build_alternatives(table, (), best_cand, hold_cand)
        return ArbitrationDecision(
            status=DecisionStatus.SUCCESS_CORRECTIVE,
            selected=best_cand.signature,
            delta_u=best_cand.delta_u,
            merit=best_merit,
            hold_merit=hold_merit,
            alternatives=alts,
            pareto_front=(),
        )

    # 4. Hold нарушен и полностью допустимых кандидатов нет -> План восстановления
    top_tier = highest_violated_tier(hold_merit)
    improving = [
        (c, m) for c, m in table.values()
        if m.v[top_tier] <= policy.recovery_rho * hold_merit.v[top_tier]
        and all(m.v[t] <= hold_merit.v[t] for t in range(top_tier))
        and _corrective_only_admissible(c, level)
    ]

    if improving:
        first_cand, first_merit = min(improving, key=lambda cm: (cm[1].v, cm[1].move_norm))
        plan = RecoveryPlanner.plan(first_cand, ctx)
        alts = build_alternatives(table, (), first_cand, hold_cand)
        return ArbitrationDecision(
            status=DecisionStatus.RECOVERY_ADVISORY,
            selected=first_cand.signature,
            delta_u=first_cand.delta_u,
            merit=first_merit,
            hold_merit=hold_merit,
            recovery=plan,
            alternatives=alts,
            pareto_front=(),
        )

    # 5. Нет даже улучшающего действия -> Отказ
    return ArbitrationDecision(
        status=DecisionStatus.REFUSAL_NO_SAFE_ACTION,
        selected=hold_cand.signature,
        delta_u={},
        merit=hold_merit,
        hold_merit=hold_merit,
        refusal_text=TZ_REFUSAL_NO_SAFE_ACTION_TEXT,
        reason_codes=("NO_SAFE_ACTION_FOUND",),
    )


def node_arbitrate(state: CoreState) -> dict:
    """Узел арбитража детерминированного ядра LangGraph."""
    t_start = time.perf_counter()
    decision = decide(state)
    return {
        "decision": decision,
        "timings_ms": {"arbitrate": round((time.perf_counter() - t_start) * 1000, 2)},
    }
