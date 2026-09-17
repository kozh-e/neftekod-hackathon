"""Модуль двухстадийного гибридного арбитража (Two-Stage Hybrid Arbitration).

Реализует требования Шага 4 дорожной карты и implementation_plan_v2.md:
1. Стадия 1 (Hard-Veto Gate): Полное отсечение кандидатов, нарушающих границы ПАЗ и ГОСТ.
   Если hold нарушает ограничения, активируется режим SUCCESS_CORRECTIVE без deadband по марже (ADR-9).
   Если допустимых кандидатов нет -> переход в Safe Hold с дословным текстом ТЗ и списком нарушений.
2. Стадия 2 (Economic Clearing): Выбор оптимума по Net Utility (Маржа - Лог-барьеры риска).
3. Стадия 3 (Deadband Filter): Фильтр зоны нечувствительности по марже (1000 руб/ч) и
   нормированной норме вектора уставок (порог 0.05).
4. Формирование списка альтернатив для оператора (Explainable AI / XAI).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.candidates import move_scales
from src.agents.state import (
    ControlCandidate,
    FinalRecommendation,
    MasGraphState,
    SafetyAuditReport,
)


class ArbitrationNode:
    """Узел Двухстадийного Гибридного Арбитража (Topological Arbitrator)."""

    @staticmethod
    def calculate_norm(
        delta_u: Dict[str, float], scales: Optional[Dict[str, float]] = None
    ) -> float:
        """Норма вектора изменения технологических уставок (нормированная при наличии scales)."""
        if not delta_u:
            return 0.0
        if scales:
            return math.sqrt(
                sum((val / max(scales.get(k, 1.0), 1e-6)) ** 2 for k, val in delta_u.items())
            )
        return math.sqrt(sum(val**2 for val in delta_u.values()))

    @classmethod
    def execute(
        cls,
        candidates: List[ControlCandidate],
        vetoed_ids: List[str],
        risk_penalties: Dict[str, float],
        min_margin_improvement: float = 1000.0,
        min_delta_norm: float = 0.05,
        scales: Optional[Dict[str, float]] = None,
        audit_reports: Optional[List[SafetyAuditReport]] = None,
        automation_level: str = "FULL",
        blocked_mvs: Optional[Sequence[str]] = None,
    ) -> Tuple[FinalRecommendation, Optional[ControlCandidate]]:
        """
        Выполняет двухстадийный арбитраж с проверкой зоны нечувствительности.
        """
        if automation_level == "REFUSAL_DATA":
            return FinalRecommendation(
                status="REFUSAL_DATA",
                explanation="Отказ ТЗ: недостоверность или отсутствие критических данных КИПиА/ЛИМС.",
                recommended_delta_u={},
            ), None

        vetoed_set = set(vetoed_ids)
        blocked_set = set(blocked_mvs or ())

        # 1. Поиск кандидата удержания (hold) и определение нарушений текущего режима
        hold_cand = next((c for c in candidates if c.is_hold), None)
        hold_offspec: List[str] = []
        if hold_cand is not None and hold_cand.candidate_id in vetoed_set:
            if audit_reports:
                for rep in audit_reports:
                    if rep.candidate_id == hold_cand.candidate_id and rep.is_vetoed:
                        hold_offspec.extend(rep.violated_limits)
            if not hold_offspec:
                hold_offspec.append("Качество/ПАЗ")

        # Допустимые кандидаты управления (кроме ветированных, кроме hold и без заблокированных MV)
        u_admissible = [
            c for c in candidates
            if c.candidate_id not in vetoed_set
            and not c.is_hold
            and not any(k in blocked_set for k in c.delta_u)
        ]

        # В режиме CORRECTIVE_ONLY запрещено наращивать расход сырья (HT_FEED_SP > 0)
        if automation_level == "CORRECTIVE_ONLY":
            u_admissible = [
                c for c in u_admissible
                if c.delta_u.get("HT_FEED_SP", 0.0) <= 0.0
            ]

        # Если допустимых управляющих решений нет
        if not u_admissible:
            # Если текущий режим нарушает ограничения (hold_offspec не пуст):
            # Замораживание в нарушении (SAFE_HOLD с Δu=0) — антипаттерн ТЗ.
            # Формируем восстанавливающий ход (RECOVERY_ADVISORY) для строгого уменьшения нарушений.
            if hold_offspec:
                best_rec_cand: Optional[ControlCandidate] = None
                best_rec_score = -float("inf")

                is_t55_bad = "AVT_T55" in hold_offspec or (hold_cand and (hold_cand.expected_t55 or 0.0) > 380.0)
                is_load_bad = "HT_DP_KPA" in hold_offspec or "AVT_T55" in hold_offspec or "HT_S_PRODUCT" in hold_offspec

                for cand in candidates:
                    if cand.is_hold:
                        continue
                    if any(k in blocked_set for k in cand.delta_u):
                        continue
                    if automation_level == "CORRECTIVE_ONLY" and cand.delta_u.get("HT_FEED_SP", 0.0) > 0.0:
                        continue

                    t55_move = cand.delta_u.get("AVT_T55_SP", 0.0)
                    feed_move = cand.delta_u.get("HT_FEED_SP", 0.0)

                    # Запрет усугубления критических параметров в восстановительном режиме
                    if is_t55_bad and t55_move > 0.0:
                        continue
                    if is_load_bad and feed_move > 0.0:
                        continue

                    score = 0.0
                    if is_t55_bad and t55_move < 0.0:
                        score += 1000.0 * (-t55_move)
                    if is_load_bad and feed_move < 0.0:
                        score += 200.0 * (-feed_move)
                    if "HT_S_PRODUCT" in hold_offspec:
                        tin_move = cand.delta_u.get("HT_TIN_SP", 0.0)
                        if tin_move > 0.0:
                            score += 50.0 * tin_move
                        p_move = cand.delta_u.get("HT_P_SP", 0.0)
                        if p_move > 0.0:
                            score += 100.0 * p_move
                        if cand.expected_sulfur is not None and hold_cand and hold_cand.expected_sulfur is not None:
                            s_diff = hold_cand.expected_sulfur - cand.expected_sulfur
                            if s_diff > 0:
                                score += 50.0 * s_diff

                    score += 0.001 * cand.expected_margin

                    if score > best_rec_score:
                        best_rec_score = score
                        best_rec_cand = cand

                if best_rec_cand is not None and best_rec_score > 0.0:
                    changes_str = ", ".join([f"{k} на {v:+.2f}" for k, v in best_rec_cand.delta_u.items()])
                    limits_str = ", ".join(sorted(set(hold_offspec)))
                    rec_explanation = (
                        f"Восстанавливающее воздействие (RECOVERY_ADVISORY) для снижения нарушений ({limits_str}): "
                        f"рекомендуется изменить уставки: {changes_str}."
                    )
                    final_rec = FinalRecommendation(
                        status="RECOVERY_ADVISORY",
                        explanation=rec_explanation,
                        recommended_delta_u=best_rec_cand.delta_u,
                    )
                    return final_rec, best_rec_cand

            explanation = (
                "Надёжной рекомендации нет: доступные варианты "
                "либо нарушают ограничение по качеству, "
                "либо выходят за заданный модельный диапазон."
            )
            if hold_offspec:
                limits_str = ", ".join(sorted(set(hold_offspec)))
                explanation += f" Нарушенные пределы: {limits_str}."

            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation=explanation,
                recommended_delta_u={},
            )
            return final_rec, None

        # 2. Выбор оптимума по Net Utility
        best_candidate: Optional[ControlCandidate] = None
        best_net_utility = -float("inf")

        for cand in u_admissible:
            penalty = float(risk_penalties.get(cand.candidate_id, 0.0))
            net_utility = cand.expected_margin - penalty
            if net_utility > best_net_utility:
                best_net_utility = net_utility
                best_candidate = cand

        if best_candidate is None:
            final_rec = FinalRecommendation(
                status="SAFE_HOLD_EMPTY_ADMISSIBLE",
                explanation="Ошибка выбора: допустимые кандидаты отсутствуют.",
                recommended_delta_u={},
            )
            return final_rec, None

        # 3. Корректирующий режим (ADR-9): если hold нарушает нормативы, deadband по марже не применяется
        if hold_offspec:
            changes_str = ", ".join([f"{k} на {v:+.2f}" for k, v in best_candidate.delta_u.items()])
            chosen_penalty = risk_penalties.get(best_candidate.candidate_id, 0.0)
            limits_str = ", ".join(sorted(set(hold_offspec)))
            explanation = (
                f"Корректирующее действие для устранения нарушений ({limits_str}): "
                f"рекомендуется изменить уставки: {changes_str}. "
                f"Ожидаемый экономический эффект: {best_candidate.expected_margin:.2f} руб/ч. "
                f"Штрафной риск ПАЗ: {chosen_penalty:.2f} руб/ч. "
                f"Чистая полезность: {best_net_utility:.2f} руб/ч."
            )
            final_rec = FinalRecommendation(
                status="SUCCESS_CORRECTIVE",
                explanation=explanation,
                recommended_delta_u=best_candidate.delta_u,
            )
            return final_rec, best_candidate

        # В режиме CORRECTIVE_ONLY при отсутствии нарушений на hold плановая оптимизация запрещена
        if automation_level == "CORRECTIVE_ONLY":
            final_rec = FinalRecommendation(
                status="NO_CHANGE_DEADBAND",
                explanation="Режим CORRECTIVE_ONLY: плановая экономическая оптимизация отключена из-за возраста калибровки LIMS (>16ч). Уставки сохранены без изменений.",
                recommended_delta_u={},
            )
            return final_rec, None

        # 4. Фильтры зоны нечувствительности (Deadband Filter) при штатной работе
        # 4.1. Порог экономической маржи (1000 руб/ч)
        if best_net_utility < min_margin_improvement:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_LOW_MARGIN",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) дает чистый "
                    f"прирост {best_net_utility:.2f} руб/ч, что ниже порога "
                    f"нечувствительности ({min_margin_improvement:.1f} руб/ч). "
                    "Режим оставлен без изменений для сбережения ресурса арматуры."
                ),
                recommended_delta_u={},
            )
            return final_rec, None

        # 4.2. Порог нормированного перемещения приводов (0.05)
        delta_norm = cls.calculate_norm(best_candidate.delta_u, scales=scales)
        if delta_norm < min_delta_norm:
            final_rec = FinalRecommendation(
                status="DEADBAND_REJECT_SMALL_STEP",
                explanation=(
                    f"Оптимальный режим ({best_candidate.candidate_id}) требует "
                    f"изменения уставок с нормой вектора {delta_norm:.4f}, что ниже "
                    f"аппаратной точности позиционеров ({min_delta_norm:.2f}). "
                    "Режим оставлен без изменений."
                ),
                recommended_delta_u={},
            )
            return final_rec, None

        # 5. Успешная оптимизационная рекомендация
        changes_str = ", ".join([f"{k} на {v:+.2f}" for k, v in best_candidate.delta_u.items()])
        chosen_penalty = risk_penalties.get(best_candidate.candidate_id, 0.0)
        explanation = (
            f"Рекомендуется изменить уставки: {changes_str}. "
            f"Ожидаемый экономический эффект: {best_candidate.expected_margin:.2f} руб/ч. "
            f"Штрафной риск ПАЗ: {chosen_penalty:.2f} руб/ч. "
            f"Чистая полезность: {best_net_utility:.2f} руб/ч."
        )

        final_rec = FinalRecommendation(
            status="SUCCESS",
            explanation=explanation,
            recommended_delta_u=best_candidate.delta_u,
        )
        return final_rec, best_candidate

    @classmethod
    def build_alternatives(
        cls,
        candidates: List[ControlCandidate],
        vetoed_ids: List[str],
        risk_penalties: Dict[str, float],
        audit_reports: Optional[List[SafetyAuditReport]] = None,
        selected_candidate: Optional[ControlCandidate] = None,
    ) -> List[Dict[str, Any]]:
        """Формирует список до 3 лучших допустимых и до 3 отклоненных альтернатив."""
        vetoed_set = set(vetoed_ids)
        reasons_map: Dict[str, List[str]] = {}
        if audit_reports:
            for rep in audit_reports:
                if rep.is_vetoed and rep.violation_reason:
                    reasons_map.setdefault(rep.candidate_id, []).append(rep.violation_reason)

        admissible = [c for c in candidates if c.candidate_id not in vetoed_set and not c.is_hold]
        admissible_sorted = sorted(
            admissible,
            key=lambda c: c.expected_margin - float(risk_penalties.get(c.candidate_id, 0.0)),
            reverse=True,
        )

        # Сначала самые выгодные из отклоненных: оператор видит, какую экономику запретили вето
        vetoed = sorted(
            (c for c in candidates if c.candidate_id in vetoed_set),
            key=lambda c: c.expected_margin,
            reverse=True,
        )

        alternatives: List[Dict[str, Any]] = []

        # До 3 лучших допустимых
        for c in admissible_sorted[:3]:
            pen = float(risk_penalties.get(c.candidate_id, 0.0))
            is_sel = selected_candidate is not None and c.candidate_id == selected_candidate.candidate_id
            alternatives.append({
                "candidate_id": c.candidate_id,
                "status": "admissible",
                "delta_u": c.delta_u,
                "expected_margin": c.expected_margin,
                "net_utility": round(c.expected_margin - pen, 2),
                "risk_penalty": round(pen, 2),
                "is_selected": is_sel,
                "reasons": None,
            })

        # До 3 отклоненных
        for c in vetoed[:3]:
            r_list = reasons_map.get(c.candidate_id, ["Нарушение технологических ограничений"])
            alternatives.append({
                "candidate_id": c.candidate_id,
                "status": "vetoed",
                "delta_u": c.delta_u,
                "expected_margin": c.expected_margin,
                "net_utility": None,
                "risk_penalty": None,
                "is_selected": False,
                "reasons": "; ".join(r_list),
            })

        return alternatives


def node_arbitration(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел LangGraph: Двухстадийный гибридный арбитраж (Fan-In после аудиторов).
    """
    candidates = state.get("candidates", [])
    vetoed_ids = state.get("vetoed_candidates", [])
    risk_penalties = state.get("risk_penalties", {})
    audit_reports = state.get("audit_reports", [])

    from src.twin.params import load_params
    twin_p = state.get("twin_params") or load_params()
    min_margin = float(getattr(twin_p.economics, "min_margin_improvement", 1000.0))
    econ_state = state.get("economics")
    if isinstance(econ_state, dict):
        if "min_margin_improvement" in econ_state:
            min_margin = float(econ_state["min_margin_improvement"])
        elif "min_margin" in econ_state:
            min_margin = float(econ_state["min_margin"])
    elif hasattr(econ_state, "min_margin_improvement"):
        min_margin = float(econ_state.min_margin_improvement)

    data_assessment = state.get("data_assessment")
    data_quality = state.get("data_quality")
    automation_level = "FULL"
    blocked_mvs = None

    if data_assessment is not None:
        if hasattr(data_assessment, "automation_level"):
            automation_level = str(getattr(data_assessment.automation_level, "value", data_assessment.automation_level))
        if hasattr(data_assessment, "blocked_mvs"):
            blocked_mvs = list(data_assessment.blocked_mvs)
    elif data_quality is not None and hasattr(data_quality, "automation_level") and data_quality.automation_level:
        automation_level = str(data_quality.automation_level)

    final_rec, selected_cand = ArbitrationNode.execute(
        candidates=candidates,
        vetoed_ids=vetoed_ids,
        risk_penalties=risk_penalties,
        min_margin_improvement=min_margin,
        min_delta_norm=0.05,
        scales=move_scales(),
        audit_reports=audit_reports,
        automation_level=automation_level,
        blocked_mvs=blocked_mvs,
    )

    alternatives = ArbitrationNode.build_alternatives(
        candidates=candidates,
        vetoed_ids=vetoed_ids,
        risk_penalties=risk_penalties,
        audit_reports=audit_reports,
        selected_candidate=selected_cand,
    )

    # Фиксация рекомендованного воздействия в сессионном хранилище для контроля отклика оборудования
    sid = state.get("session_id")
    events_out: List[str] = []
    if sid:
        from src.twin.session import TWIN_STORE
        if final_rec and final_rec.recommended_delta_u:
            TWIN_STORE.record_recommended_move(sid, final_rec.recommended_delta_u)
        events_out.extend(TWIN_STORE.get_events(sid))

    # Карточка XAI: для рекомендации хода и для решения «сохранить режим» в зоне нечувствительности (tz:998)
    card_cand = selected_cand
    if final_rec.status.startswith("DEADBAND"):
        card_cand = next((c for c in candidates if c.is_hold), None)

    if card_cand is not None and (
        final_rec.status.startswith("SUCCESS")
        or final_rec.status.startswith("DEADBAND")
        or final_rec.status.startswith("RECOVERY")
    ):
        try:
            from src.xai.narrative import XAIGenerator

            base_dict = {}
            if state.get("tags"):
                base_dict.update(state["tags"])
            raw_telemetry = state.get("raw_telemetry")
            if raw_telemetry is not None:
                if hasattr(raw_telemetry, "model_dump"):
                    base_dict.update(raw_telemetry.model_dump())
                elif isinstance(raw_telemetry, dict):
                    base_dict.update(raw_telemetry)

            lims_age = float(
                base_dict.get(
                    "lims_age_hours",
                    state.get("confidence", {}).get("lims_age_hours", 0.0),
                )
            )

            report = XAIGenerator.generate_explanation(
                best_candidate=card_cand,
                base_state=base_dict,
                risk_penalties=risk_penalties,
                lims_age_hours=lims_age,
                hold_prediction=state.get("hold_prediction"),
                alternatives=alternatives,
                confidence=state.get("confidence"),
                blending_recipe=state.get("blending_recipe"),
                pareto=state.get("pareto"),
                audit_reports=audit_reports,
            )
            if card_cand is not selected_cand:
                report = f"**Решение арбитража ({final_rec.status}):** {final_rec.explanation}\n\n{report}"
            final_rec.markdown_report = report
        except Exception:
            pass

    res: Dict[str, Any] = {
        "final_recommendation": final_rec,
        "selected_candidate": selected_cand,
        "alternatives": alternatives,
    }
    if events_out:
        res["events"] = events_out
    return res


# =============================================================================
# Детерминированный Лексикографический Арбитраж v3 (ADR-15, ADR-16, §5.10)
# =============================================================================

import time
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
    feasible = [(c, m) for c, m in table.values() if all(vi <= FEASIBILITY_TOL for vi in m.v)]
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
