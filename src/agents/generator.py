"""Генератор оптимизационных кандидатов управления (Candidate Generator v3).

Соответствует спецификации §5.6 implementation_plan_v3.md (Задача P3.1):
1. CandidateOrigin: HOLD, LOCAL, GLOBAL, NEAREST_FEASIBLE, REPAIR, REFINE;
2. Дедупликация кандидатов по канонической sha1-сигнатуре вектора Delta u;
3. Локальный трафарет local_stencil с учетом blocked_mvs, уровня CAUTIOUS (шаг * 0.5)
   и связанных ходов (COUPLED_MOVES);
4. Проверка границ и допустимой скорости хода T0 через anti_windup.py;
5. Уточнение сетки refine_half_step вокруг лучшего кандидата шагом 1/2;
6. Функция propose_initial(ctx) для инициализации переговорного пула раунда 0.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.anti_windup import apply_anti_windup
from src.agents.candidates import COUPLED_MOVES, DEFAULT_MVS, MVSpec
from src.agents.contracts import AutomationLevel, Candidate, CandidateOrigin
from src.agents.policy import PolicyConfig


def signature_of(
    delta_u: Mapping[str, float],
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
) -> str:
    """
    Вычисляет каноническую детерминированную sha1-сигнатуру кандидата.
    Значения delta_u округляются до кванта step / 100 для защиты от численного шума.
    """
    mv_dict = {mv.name: mv for mv in mvs}
    parts: List[str] = []
    for k in sorted(delta_u.keys()):
        val = float(delta_u[k])
        step = mv_dict[k].step if k in mv_dict else 1.0
        quantum = max(step / 100.0, 1e-4)
        quantized = round(val / quantum) * quantum
        if abs(quantized) > 1e-6:
            parts.append(f"{k}={quantized:.4f}")

    raw_str = ";".join(parts) if parts else "HOLD"
    return hashlib.sha1(raw_str.encode("utf-8")).hexdigest()


def hold(mvs: Sequence[MVSpec] = DEFAULT_MVS) -> Candidate:
    """Возвращает базового кандидата удержания режима (HOLD, Delta u = 0)."""
    return Candidate(
        signature=signature_of({}, mvs),
        delta_u={},
        origin=CandidateOrigin.HOLD,
        proposed_by="generator",
        parent=None,
        round=0,
    )


def local_stencil(
    u_current: Mapping[str, float],
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
    policy: Optional[PolicyConfig] = None,
    blocked_mvs: Collection[str] = (),
    automation_level: Optional[AutomationLevel] = None,
) -> List[Candidate]:
    """
    Генерирует локальный трафарет ходов:
    - Однопараметрические шаги +/- step с учетом CAUTIOUS (шаг * 0.5);
    - Связанные ходы COUPLED_MOVES;
    - Фильтрация заблокированных управляющих воздействий (blocked_mvs);
    - Ограничение по границам и скорости хода через anti-windup.
    """
    pol = policy or PolicyConfig()
    blocked_set = set(blocked_mvs)

    # Масштабирование шага при режиме CAUTIOUS
    step_scale = 1.0
    if automation_level == AutomationLevel.CAUTIOUS:
        step_scale = pol.cautious_step_scale

    candidates: List[Candidate] = []
    mv_dict = {mv.name: mv for mv in mvs}

    # 1. Однопараметрические шаги +/-
    for mv in mvs:
        if not mv.enabled or mv.name in blocked_set:
            continue

        u0 = float(u_current.get(mv.name, (mv.lo + mv.hi) / 2.0))
        for sign in (+1.0, -1.0):
            req_du = sign * mv.step * step_scale
            eff_du, _ = apply_anti_windup(
                current_u=u0,
                delta_u_calc=req_du,
                max_rate=mv.max_move,
                min_u=mv.lo,
                max_u=mv.hi,
            )
            if abs(eff_du) > 1e-4:
                du_dict = {mv.name: round(eff_du, 4)}
                candidates.append(
                    Candidate(
                        signature=signature_of(du_dict, mvs),
                        delta_u=du_dict,
                        origin=CandidateOrigin.LOCAL,
                        proposed_by="generator",
                        parent=None,
                        round=0,
                    )
                )

    # 2. Связанные ходы (COUPLED_MOVES)
    for cm in COUPLED_MOVES:
        # Если хотя бы один MV заблокирован или выключен, связанный ход пропускается
        if any(mv_name in blocked_set for mv_name in cm.keys()):
            continue
        if any(mv_dict.get(mv_name) is None or not mv_dict[mv_name].enabled for mv_name in cm.keys()):
            continue

        cand_du: Dict[str, float] = {}
        for mv_name, mult in cm.items():
            mv = mv_dict[mv_name]
            u0 = float(u_current.get(mv.name, (mv.lo + mv.hi) / 2.0))
            req_du = mult * mv.step * step_scale
            eff_du, _ = apply_anti_windup(
                current_u=u0,
                delta_u_calc=req_du,
                max_rate=mv.max_move,
                min_u=mv.lo,
                max_u=mv.hi,
            )
            if abs(eff_du) > 1e-4:
                cand_du[mv.name] = round(eff_du, 4)

        if cand_du:
            candidates.append(
                Candidate(
                    signature=signature_of(cand_du, mvs),
                    delta_u=cand_du,
                    origin=CandidateOrigin.LOCAL,
                    proposed_by="generator",
                    parent=None,
                    round=0,
                )
            )

    return dedupe_by_signature(candidates)


def refine_half_step(
    best: Candidate,
    ctx: Any,
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
) -> List[Candidate]:
    """
    Локальное дробление шага вокруг лучшего кандидата:
    - Смещение на +/- 0.5 * step по каждому незаблокированному MV;
    - Если лучший кандидат получен из ремонта (REPAIR), полушаг в сторону предка;
    - Все ходы проверяются на допустимость T0.
    """
    policy = getattr(ctx, "policy", None) or PolicyConfig()
    data = getattr(ctx, "data", None)
    blocked_set = set(data.blocked_mvs) if data else set()
    u_current = getattr(ctx, "u", None) or (ctx.estimate.u_actual if getattr(ctx, "estimate", None) else {})

    mv_dict = {mv.name: mv for mv in mvs}
    best_du = dict(best.delta_u)
    candidates: List[Candidate] = []

    # 1. Шаг 1/2 по координатам вокруг best_du
    for mv in mvs:
        if not mv.enabled or mv.name in blocked_set:
            continue

        u0 = float(u_current.get(mv.name, (mv.lo + mv.hi) / 2.0))
        base_delta = best_du.get(mv.name, 0.0)

        for sign in (+0.5, -0.5):
            req_du = base_delta + sign * mv.step
            eff_du, _ = apply_anti_windup(
                current_u=u0,
                delta_u_calc=req_du,
                max_rate=mv.max_move,
                min_u=mv.lo,
                max_u=mv.hi,
            )
            new_du = dict(best_du)
            if abs(eff_du) > 1e-4:
                new_du[mv.name] = round(eff_du, 4)
            else:
                new_du.pop(mv.name, None)

            if new_du != best_du:
                candidates.append(
                    Candidate(
                        signature=signature_of(new_du, mvs),
                        delta_u=new_du,
                        origin=CandidateOrigin.REFINE,
                        proposed_by="generator",
                        parent=best.signature,
                        round=best.round + 1,
                    )
                )

    # 2. Если кандидат получен из ремонта и известен родитель
    parent_cand = getattr(ctx, "candidates", {}).get(best.parent) if hasattr(ctx, "candidates") and best.parent else None
    if parent_cand is not None and parent_cand.delta_u:
        mid_du: Dict[str, float] = {}
        all_keys = set(best_du.keys()) | set(parent_cand.delta_u.keys())
        for k in all_keys:
            if k in blocked_set:
                continue
            mv = mv_dict.get(k)
            if not mv or not mv.enabled:
                continue
            u0 = float(u_current.get(k, (mv.lo + mv.hi) / 2.0))
            d_mid = 0.5 * (best_du.get(k, 0.0) + parent_cand.delta_u.get(k, 0.0))
            eff_du, _ = apply_anti_windup(u0, d_mid, mv.max_move, mv.lo, mv.hi)
            if abs(eff_du) > 1e-4:
                mid_du[k] = round(eff_du, 4)

        if mid_du and mid_du != best_du:
            candidates.append(
                Candidate(
                    signature=signature_of(mid_du, mvs),
                    delta_u=mid_du,
                    origin=CandidateOrigin.REFINE,
                    proposed_by="generator",
                    parent=best.signature,
                    round=best.round + 1,
                )
            )

    return dedupe_by_signature(candidates)


def dedupe_by_signature(candidates: Sequence[Candidate]) -> List[Candidate]:
    """Дедуплицирует список кандидатов с сохранением порядка первого вхождения."""
    seen = set()
    result: List[Candidate] = []
    for c in candidates:
        if c.signature not in seen:
            seen.add(c.signature)
            result.append(c)
    return result


from src.agents.global_search import GlobalSearchAgent
from src.agents.registry import ALL_SPECS
from src.agents.uncertainty import chance_effective
from src.agents.contracts import Tier


def hold_violates(ctx: Any) -> bool:
    """Проверяет, нарушает ли удержание текущего режима хотя бы одно технологическое ограничение T1/T2."""
    estimate = getattr(ctx, "estimate", None)
    if estimate is None:
        return False
    policy = getattr(ctx, "policy", None) or PolicyConfig()

    for spec in ALL_SPECS:
        if spec.tier in (Tier.T1_EQUIPMENT, Tier.T2_QUALITY):
            val = estimate.value_of(spec.quantity)
            if val is not None:
                v_flt = float(val)
                eff, _, _ = chance_effective(
                    spec=spec,
                    mean_now=v_flt,
                    pred_u=v_flt,
                    pred_u0=v_flt,
                    estimate=estimate,
                    policy=policy,
                )
                if spec.sense == "max" and eff > spec.limit + 1e-4:
                    return True
                elif spec.sense == "min" and eff < spec.limit - 1e-4:
                    return True
    return False


def propose_initial(ctx: Any, mvs: Sequence[MVSpec] = DEFAULT_MVS) -> List[Candidate]:
    """
    Формирует начальный переговорный пул кандидатов раунда 0 (§5.6):
    1. hold() (удержание режима);
    2. local_stencil (локальные однопараметрические и связанные ходы);
    3. GlobalSearchAgent.solve (глобальный ход к оптимуму модели);
    4. GlobalSearchAgent.nearest_feasible (при нарушенном hold - ближайшая допустимая точка);
    5. Дедупликация по sha1-сигнатуре.
    """
    pool = [hold(mvs)]
    u0 = getattr(ctx, "u", None) or (ctx.estimate.u_actual if getattr(ctx, "estimate", None) else {})
    policy = getattr(ctx, "policy", None) or PolicyConfig()
    data = getattr(ctx, "data", None)
    blocked_mvs = data.blocked_mvs if data else ()
    automation_level = data.automation_level if data else None

    # 1. Локальный трафарет
    pool.extend(local_stencil(u0, mvs=mvs, policy=policy, blocked_mvs=blocked_mvs, automation_level=automation_level))

    # 2. Глобальный оптимум установившегося режима (если разрешены экономические ходы)
    if automation_level not in (AutomationLevel.CORRECTIVE_ONLY, AutomationLevel.REFUSAL_DATA):
        target = GlobalSearchAgent.solve(ctx, mvs=mvs)
        if target:
            pool.append(GlobalSearchAgent.move_towards(u0, target.u, mvs=mvs, policy=policy, origin=CandidateOrigin.GLOBAL))

    # 3. Ближайшая допустимая точка при нарушении текущего режима
    if hold_violates(ctx):
        nearest = GlobalSearchAgent.nearest_feasible(ctx, mvs=mvs)
        if nearest:
            pool.append(GlobalSearchAgent.move_towards(u0, nearest.u, mvs=mvs, policy=policy, origin=CandidateOrigin.NEAREST_FEASIBLE))

    return dedupe_by_signature(pool)
