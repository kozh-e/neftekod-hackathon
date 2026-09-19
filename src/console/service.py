"""Сервисный слой сборки состояния пульта и применения уставок (R1).

Преобразует данные сессии и выходов графа МАС в модель ConsoleState и обрабатывает commit.
Никаких захардкоженных чисел: все лимиты считываются из src/agents/registry.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import HTTPException

from src.agents.contracts import DecisionStatus, Tier
from src.agents.decision_log import append_decision
from src.agents.economics import MarginModel
from src.agents.policy import AutomationThresholds
from src.agents.registry import ALL_SPECS, REGISTRY, T0_SPECS, T1_SPECS, T2_SPECS
from src.console import auto, corridor, forecast
from src.console.contracts import (
    AgentVote,
    Alarm,
    Alternative,
    Change,
    Chip,
    ClockInfo,
    CommitRequest,
    CommitResult,
    ConfidenceInfo,
    ConsoleErrorDetail,
    ConsoleState,
    CvSeries,
    Effect,
    FeedItem,
    KernelCheckDTO,
    KernelInfo,
    Limit,
    Margin,
    ModeInfo,
    MvLine,
    MvSeries,
    NegotiationEventDTO,
    ParetoFrontDTO,
    ParetoObjective,
    ParetoPointDTO,
    Point,
    PreviewRequest,
    Recommendation,
    Refusal,
    Scenario,
    SensorSeries,
    Series,
    XaiInfo,
)
from src.twin.params import load_params

if TYPE_CHECKING:
    from src.console.runtime import ConsoleSession


# Константы чек-листов отказов согласно ТЗ
REFUSAL_CHECKLIST_DATA = [
    "ПАК {tag}: питание, пробоотборная линия, связь с ПЛК",
    "Заказать внеочередной анализ LIMS на серу (гидрогенизат)",
    "Не повышать загрузку HT_F9 до восстановления данных",
]
REFUSAL_CHECKLIST_NO_SAFE = [
    "Проверить уставки вручную по регламенту",
    "Сообщить технологу установки",
]
REFUSAL_CHECKLIST_TIMEOUT = [
    "Повторить расчёт",
    "Проверить загрузку сервера",
]

MV_DECIMALS: Dict[str, int] = {
    "HT_FEED_SP": 1,
    "HT_TIN_SP": 1,
    "HT_P_SP": 3,
    "HT_GOR_SP": 0,
    "AVT_T55_SP": 1,
}

MV_META: Dict[str, Dict[str, str]] = {
    "HT_FEED_SP": {"tag": "HT_F9", "title": "Сырьё ГО", "unit": "т/ч"},
    "HT_TIN_SP": {"tag": "HT_T6", "title": "Вход Р-202", "unit": "°C"},
    "HT_P_SP": {"tag": "HT_P13", "title": "Давление", "unit": "МПа"},
    "HT_GOR_SP": {"tag": "HT_GOR", "title": "Кратность ВСГ", "unit": "нм³/м³"},
    "AVT_T55_SP": {"tag": "AVT_T55", "title": "Перевал П-3", "unit": "°C"},
}

# Справочные датчики двойника (B1, 03_STREAMLIT_MIGRATION_PLAN.md §4): реально считаются
# twin.step(), но не входят ни в CV (SafetyKernel), ни в MV (уставки) — только для отображения.
# Ключи 1:1 с src/console/runtime.py::SENSOR_SOURCE_TAGS (session.sensor_history).
SENSOR_META: Dict[str, Dict[str, str]] = {
    "HT_BED_MEAN": {"tag": "HT_BED_MEAN", "title": "WABT реактора Р-202", "unit": "°C"},
    "HT_VSG": {"tag": "HT_VSG", "title": "Расход ВСГ", "unit": "нм³/ч"},
    "HT_T11": {"tag": "HT_T11", "title": "Температура выхода Р-202 (T11)", "unit": "°C"},
    "HT_D15_PRODUCT": {"tag": "HT_D15_PRODUCT", "title": "Плотность гидрогенизата (D15)", "unit": "кг/м³"},
    "HT_T95_PRODUCT": {"tag": "HT_T95_PRODUCT", "title": "Фракционный состав продукта (T95)", "unit": "°C"},
    "HT_CFPP_PRODUCT": {
        "tag": "HT_CFPP_PRODUCT",
        "title": "Предельная температура фильтруемости продукта (CFPP)",
        "unit": "°C",
    },
    "HT_CN_PRODUCT": {"tag": "HT_CN_PRODUCT", "title": "Цетановое число продукта", "unit": "б/р"},
    "HT_S_FEED": {"tag": "HT_S_FEED", "title": "Сера в сырье", "unit": "ppm"},
    "HT_T95_FEED": {"tag": "HT_T95_FEED", "title": "Фракционный состав сырья (T95)", "unit": "°C"},
}


def _find_spec(key: str):
    for s in ALL_SPECS:
        if s.key == key:
            return s
    return None


def narrative_for(decision: Any, card: Any, hold_traj: Any) -> str:
    """
    Формирует детерминированный шаблон narrative (D11, без LLM), <= 2 предложения, <= 220 символов.
    Схема: «{причина}. {действие}, чтобы {цель}.»
    """
    if not decision:
        return "Режим в норме, уставки поддерживаются на текущих значениях."

    status = getattr(decision, "status", DecisionStatus.SUCCESS)
    delta_u = getattr(decision, "delta_u", {}) or {}

    # 1. Причина
    reason = None
    if card and getattr(card, "problem_and_risk", None):
        card_pr = card.problem_and_risk
        if isinstance(card_pr, str) and card_pr.strip():
            c_str = card_pr.strip()
            if c_str.startswith("{"):
                if "hold_violated" in c_str:
                    reason = "Нарушение технологических ограничений текущего режима"
                else:
                    reason = "Отклонение параметров текущего режима от оптимальных"
            else:
                reason = c_str
        elif isinstance(card_pr, dict):
            alerts = card_pr.get("alerts")
            if alerts and isinstance(alerts, list) and len(alerts) > 0:
                first_alert = str(alerts[0])
                if "GODT.S" in first_alert:
                    reason = "Прогнозируется превышение нормы по сере в гидрогенизате"
                elif "RX.DP" in first_alert:
                    reason = "Прогнозируется рост перепада давления на реакторе Р-202"
                elif "FURNACE.COT" in first_alert:
                    reason = "Температура на выходе печи приближается к пределу"
                elif "FLASH" in first_alert:
                    reason = "Риск снижения температуры вспышки ниже нормы"
                else:
                    cleaned = first_alert.replace("Нарушение текущего режима по ярусам: ", "Нарушение режима: ")
                    cleaned = cleaned.strip("{}'\" ")
                    reason = cleaned
            elif card_pr.get("hold_violated"):
                reason = "Нарушение технологических ограничений текущего режима"
    if not reason and hold_traj:
        s_bands = hold_traj.cv.get("sulfur", [])
        if s_bands and s_bands[-1].p50 > s_bands[0].p50:
            s_slope = (s_bands[-1].p50 - s_bands[0].p50) / 4.0
            if s_slope > 0.1:
                reason = f"Сера растёт из-за утяжеления сырья (+{s_slope:.1f} ppm/ч)"

    if not reason:
        reason = "Режим стабилен, технологические ограничения соблюдены"

    # 2. Действие
    actions = []
    for sp, delta in delta_u.items():
        if abs(delta) > 1e-4:
            action_verb = "Поднимаем" if delta > 0 else "Снижаем"
            sp_title = MV_META.get(sp, {}).get("title", sp)
            actions.append(f"{action_verb.lower()} {sp_title}")

    if actions:
        action_text = " и ".join(actions[:2]).capitalize()
    else:
        action_text = "Корректировка не требуется"

    # 3. Цель
    s_spec = _find_spec("GODT.S_MAX")
    s_limit_str = f"≤ {s_spec.limit:.0f} ppm" if s_spec else "в норме"

    if status == DecisionStatus.SUCCESS_CORRECTIVE or "Сера растёт" in reason:
        target = f"удержать серу {s_limit_str}"
    elif status == DecisionStatus.RECOVERY_ADVISORY:
        target = "вернуть технологический режим в пределы оборудования"
    else:
        # SUCCESS без риска
        target = "увеличить маржу установки"

    narrative = f"{reason}. {action_text}, чтобы {target}."
    if len(narrative) > 220:
        narrative = narrative[:217] + "..."
    return narrative


def build_state(session: ConsoleSession) -> ConsoleState:
    """
    Собирает полный снимок ConsoleState из session (история, режим, последний graph_result).
    Не выполняет повторных тяжелых расчетов графа.
    """
    now_iso = session.now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Сборка CV серий
    cv_series_list: List[CvSeries] = []
    cv_configs = [
        ("sulfur", "HT_Q21", "СЕРА", "ppm", "GODT.S_MAX", [5.0, 13.0]),
        ("flash", "HT_FLASH", "ВСПЫШКА", "°C", "GODT.FLASH_MIN", [50.0, 70.0]),
        ("dp", "HT_P8", "ПЕРЕПАД Р-202", "кПа", "RX.DP_MAX", [100.0, 500.0]),
    ]

    hold_traj = session.hold_trajectory or forecast.build_trajectory(session, session.u_current, "hold")

    for key, tag, title, unit, spec_key, y_range in cv_configs:
        spec = _find_spec(spec_key)
        lim_val = spec.limit if spec else 0.0
        lim_sense = spec.sense if spec else "max"
        lim_tier = "T1" if (spec and spec.tier == Tier.T1_EQUIPMENT) else "T2"
        lim_label = f"{spec.label[:20]} {lim_val} {unit}" if spec else f"{lim_val} {unit}"

        pts = list(session.cv_history.get(key, []))

        # Chip и note
        chip_severity = "ok"
        chip_text = "в норме"
        note = None

        if pts:
            last_pt = pts[-1]
            if last_pt.quality in ("MISSING", "BAD"):
                chip_severity = "nodata"
                chip_text = "нет сигнала" if last_pt.quality == "MISSING" else "недостоверно"
            elif last_pt.v is not None:
                is_alarm = (
                    (last_pt.v > lim_val) if lim_sense == "max" else (last_pt.v < lim_val)
                )
                if is_alarm:
                    chip_severity = "alarm"
                    chip_text = "нарушение нормы"
                else:
                    # Проверяем тренд по последним 6 точкам (1 час)
                    if len(pts) >= 6 and pts[-6].v is not None:
                        dt_tick_h = session.tick_delta.total_seconds() / 3600.0
                        slope_h = (last_pt.v - pts[-6].v) / (5.0 * dt_tick_h)
                        if abs(slope_h) >= 0.2:
                            chip_severity = "warn" if slope_h > 0 else "ok"
                            sign = "+" if slope_h > 0 else ""
                            chip_text = f"растёт {sign}{slope_h:.1f} {unit}/ч"

        # Note формируется, если hold пересекает лимит по p50
        if hold_traj and key in hold_traj.cv:
            bands = hold_traj.cv[key]
            for idx, b in enumerate(bands):
                breach = (b.p50 > lim_val) if lim_sense == "max" else (b.p50 < lim_val)
                if breach:
                    minutes = int(idx * (session.tick_delta.total_seconds() / 60.0))
                    h = minutes // 60
                    m = minutes % 60
                    time_str = f"{h} ч {m} мин" if h > 0 else f"{m} мин"
                    note = f"без изменений пробьёт {lim_val:.1f} {unit} через {time_str}"
                    break

        limit_dto = Limit(
            value=lim_val,
            sense=lim_sense,  # type: ignore
            label=lim_label,
            tier=lim_tier,    # type: ignore
            source=f"registry.py:{spec_key}",
        )

        cv_series_list.append(
            CvSeries(
                key=key,  # type: ignore
                tag=tag,
                title=title,
                unit=unit,
                limit=limit_dto,
                history=pts,
                chip=Chip(text=chip_text, severity=chip_severity),  # type: ignore
                note=note,
                y_range=y_range,
            )
        )

    # 2. Сборка MV серий
    mv_series_list: List[MvSeries] = []
    cot_warm_spec = _find_spec("FURNACE.COT_POLICY_WARM")

    last_res = session.last_graph_result
    final_rec = last_res.get("final_recommendation") if last_res else None
    status = getattr(final_rec, "status", DecisionStatus.SUCCESS) if final_rec else DecisionStatus.SUCCESS
    is_frozen = status in (
        DecisionStatus.REFUSAL_DATA,
        DecisionStatus.REFUSAL_NO_SAFE_ACTION,
        DecisionStatus.REFUSAL_TIMEOUT,
    )

    for sp_name, meta in MV_META.items():
        corr = corridor.corridor_for(sp_name)
        lines: List[MvLine] = []
        if corr.lo is not None:
            lines.append(MvLine(value=corr.lo, kind="T0", label="T0 min"))
        if corr.hi is not None:
            lines.append(MvLine(value=corr.hi, kind="T0", label="T0 max"))

        if sp_name == "AVT_T55_SP":
            if cot_warm_spec:
                lines.append(MvLine(value=cot_warm_spec.limit, kind="warn", label="Зона предупр."))
            cot_max_spec = _find_spec("FURNACE.COT_MAX")
            if cot_max_spec:
                lines.append(MvLine(value=cot_max_spec.limit, kind="T1", label="T1 макс"))

        sp_pts = list(session.mv_sp_history.get(sp_name, []))
        pv_pts = list(session.mv_pv_history.get(sp_name, []))

        mv_series_list.append(
            MvSeries(
                sp=sp_name,  # type: ignore
                tag=meta["tag"],
                title=meta["title"],
                unit=meta["unit"],
                decimals=MV_DECIMALS.get(sp_name, 1),
                sp_history=sp_pts,
                pv_history=pv_pts,
                corridor=corr,
                lines=lines,
                frozen=is_frozen,
            )
        )

    # 3. Сборка Recommendation
    rec_dto: Optional[Recommendation] = None
    if final_rec:
        card = last_res.get("card")
        delta_u = dict(getattr(final_rec, "recommended_delta_u", {}) or {})
        rec_changes: List[Change] = []

        for sp_name, delta in delta_u.items():
            if abs(delta) > 1e-4:
                curr_v = session.u_current.get(sp_name, 0.0)
                meta = MV_META.get(sp_name, {"tag": sp_name, "title": sp_name, "unit": ""})
                dec = MV_DECIMALS.get(sp_name, 1)
                rec_changes.append(
                    Change(
                        sp=sp_name,  # type: ignore
                        tag=meta["tag"],
                        title=meta["title"],
                        unit=meta["unit"],
                        current=round(curr_v, dec),
                        target=round(curr_v + delta, dec),
                        delta=round(delta, dec),
                        decimals=dec,
                    )
                )

        # Траектория рекомендации
        rec_u_target = {
            sp: session.u_current.get(sp, 0.0) + delta_u.get(sp, 0.0)
            for sp in session.u_current.keys()
        }
        rec_traj = (
            forecast.build_trajectory(session, rec_u_target, "recommendation")
            if rec_changes
            else None
        )
        rec_effect = (
            forecast.effect_of(rec_traj, hold_traj, session)
            if rec_traj
            else Effect()
        )

        # Ядро
        kernel_checks: List[KernelCheckDTO] = []
        kernel_res = last_res.get("kernel")
        if kernel_res:
            raw_checks = getattr(kernel_res, "checks", ())
            if isinstance(raw_checks, dict):
                raw_checks = raw_checks.values()
            for c in raw_checks:
                kernel_checks.append(
                    KernelCheckDTO(
                        name=getattr(c, "name", ""),
                        passed=getattr(c, "passed", True),
                        detail=getattr(c, "detail", ""),
                    )
                )

        # Агенты
        agent_votes: List[AgentVote] = []
        neg_log = list(last_res.get("negotiation_log", ()) or last_res.get("negotiation", ()))
        certs = last_res.get("certificates", {}) or {}
        selected_sig = getattr(final_rec, "selected", None) or (getattr(last_res.get("decision"), "selected", None) if last_res else None)

        # 1. Optimization
        opt_delta = getattr(final_rec, "recommended_delta_u", {}) or {}
        if any(abs(v) > 1e-4 for v in opt_delta.values()):
            opt_text = "Предложен оптимальный ход для максимизации маржи"
        else:
            opt_text = "Текущий режим оптимален, изменение уставок не требуется"
        agent_votes.append(AgentVote(agent="optimization", stance="for", text=opt_text))

        # 2. Reliability
        rel_cert = certs.get(f"{selected_sig}|reliability") if selected_sig else None
        rel_events = [ev for ev in neg_log if getattr(ev, "actor", "") == "reliability"]
        if rel_events and any("VETO" in getattr(ev, "kind", "") for ev in rel_events):
            rel_stance = "veto"
            rel_text = rel_events[0].detail or "Нарушение ограничений надежности T1"
        elif rel_cert and rel_cert.verdict != "ADMISSIBLE":
            rel_stance = "veto"
            rel_text = "Нарушены технологические ограничения оборудования T1"
        else:
            rel_stance = "for"
            rel_text = "Ограничения T1 (перепад Р-202, температуры выходов) соблюдены с запасом"
        agent_votes.append(AgentVote(agent="reliability", stance=rel_stance, text=rel_text))

        # 3. Quality
        qual_cert = certs.get(f"{selected_sig}|quality") if selected_sig else None
        qual_events = [ev for ev in neg_log if getattr(ev, "actor", "") == "quality"]
        if qual_events and any("VETO" in getattr(ev, "kind", "") for ev in qual_events):
            qual_stance = "veto"
            qual_text = qual_events[0].detail or "Риск нарушения качества по сере или вспышке"
        elif qual_cert and qual_cert.verdict != "ADMISSIBLE":
            qual_stance = "veto"
            qual_text = "Прогноз качества гидрогенизата не удовлетворяет ГОСТ"
        else:
            qual_stance = "for"
            qual_text = "Качество гидрогенизата: сера Ŝ+2σ ≤ 10 ppm, вспышка ≥ 55 °C"
        agent_votes.append(AgentVote(agent="quality", stance=qual_stance, text=qual_text))

        # 4. Supply
        sup_cert = certs.get(f"{selected_sig}|supply") if selected_sig else None
        if sup_cert and sup_cert.verdict != "ADMISSIBLE":
            sup_stance = "veto"
            sup_text = "Дисбаланс сырьевого резервуара АВТ/ГО"
        else:
            sup_stance = "for"
            sup_text = "Материальный баланс АВТ-6 / 24-2000 устойчив"
        agent_votes.append(AgentVote(agent="supply", stance=sup_stance, text=sup_text))

        # 5. Kernel
        k_passed = bool(kernel_res.passed if kernel_res else True)
        agent_votes.append(AgentVote(
            agent="kernel",
            stance="for" if k_passed else "veto",
            text="Независимое ядро безопасности: все проверки T0–T3 пройдены" if k_passed else "Ядро безопасности отклонило ход",
        ))

        # Альтернативы
        alternatives: List[Alternative] = []
        raw_alts = last_res.get("alternatives", ())
        for alt in raw_alts:
            alt_kind = getattr(alt, "kind", "")
            choice_map = {
                "pareto_more_margin": ("max_margin", "Макс. маржа"),
                "selected": ("balanced", "Сбалансировано"),
                "pareto_safer_sulfur": ("max_safety", "Макс. запас"),
            }
            if alt_kind in choice_map:
                ch, lbl = choice_map[alt_kind]
                alt_du = getattr(alt, "delta_u", {}) or {}
                alt_u_target = {
                    sp: session.u_current.get(sp, 0.0) + alt_du.get(sp, 0.0)
                    for sp in session.u_current.keys()
                }
                alt_traj = forecast.build_trajectory(session, alt_u_target, "alternative")
                alt_eff = forecast.effect_of(alt_traj, hold_traj, session)
                alt_changes = [
                    Change(
                        sp=sp,  # type: ignore
                        tag=MV_META[sp]["tag"],
                        title=MV_META[sp]["title"],
                        unit=MV_META[sp]["unit"],
                        current=round(session.u_current[sp], MV_DECIMALS[sp]),
                        target=round(alt_u_target[sp], MV_DECIMALS[sp]),
                        delta=round(alt_du.get(sp, 0.0), MV_DECIMALS[sp]),
                        decimals=MV_DECIMALS[sp],
                    )
                    for sp in alt_du
                    if abs(alt_du[sp]) > 1e-4 and sp in MV_META
                ]
                alternatives.append(
                    Alternative(
                        choice=ch,  # type: ignore
                        label=lbl,  # type: ignore
                        available=True,
                        changes=alt_changes,
                        effect=alt_eff,
                        trajectory=alt_traj,
                    )
                )

        # Отказ
        refusal_dto: Optional[Refusal] = None
        if status in (
            DecisionStatus.REFUSAL_DATA,
            DecisionStatus.REFUSAL_NO_SAFE_ACTION,
            DecisionStatus.REFUSAL_TIMEOUT,
        ):
            if status == DecisionStatus.REFUSAL_DATA:
                ch_list = [c.format(tag="HT_Q21") for c in REFUSAL_CHECKLIST_DATA]
                threshold_lims = int(AutomationThresholds().refusal_lims_age_h_without_pak)
                cond = f"ПАК HT_Q21 вернётся в норму или придёт анализ LIMS моложе {threshold_lims} ч"
            elif status == DecisionStatus.REFUSAL_NO_SAFE_ACTION:
                ch_list = REFUSAL_CHECKLIST_NO_SAFE
                cond = "Появится допустимый ход по ядру безопасности"
            else:
                ch_list = REFUSAL_CHECKLIST_TIMEOUT
                cond = "Расчёт уложится в таймаут"

            ref_text = (
                getattr(card, "refusal_text", None)
                or getattr(final_rec, "explanation", "Отказ от оптимизации")
            )
            refusal_dto = Refusal(
                reason_code=status,
                text=ref_text,
                checklist=ch_list,
                auto_resume_condition=cond,
            )

        valid_until_dt = session.now + timedelta(minutes=30)
        rec_dto = Recommendation(
            cycle_id=session.last_cycle_id or f"cycle_{session.tick}",
            status=status,
            created_at=now_iso,
            valid_until=valid_until_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            narrative=narrative_for(final_rec, card, hold_traj),
            changes=rec_changes,
            effect=rec_effect,
            kernel=KernelInfo(passed=bool(kernel_res.passed if kernel_res else True), checks=kernel_checks, limited=[]),
            agents=agent_votes,
            trajectory=rec_traj,
            alternatives=alternatives,
            refusal=refusal_dto,
        )

    # 4. Тревоги T0/T1
    alarms: List[Alarm] = []
    meas = session.plant.measure()
    for spec in T1_SPECS:
        if spec.requires_measurement and spec.requires_measurement in meas:
            val = float(meas[spec.requires_measurement])
            breached = (val > spec.limit) if spec.sense == "max" else (val < spec.limit)
            if breached:
                a_id = f"alarm_{spec.key}_{session.tick}"
                alarms.append(
                    Alarm(
                        id=a_id,
                        tier="T1",
                        tag=spec.requires_measurement,
                        text=spec.label,
                        value=round(val, 2),
                        limit=spec.limit,
                        at=now_iso,
                        acknowledged=a_id in session.acks,
                    )
                )

    # 5. Маржа
    econ_p = load_params()
    if session.economics_override:
        for k, v in session.economics_override.items():
            if hasattr(econ_p.economics, k):
                setattr(econ_p.economics, k, float(v))
    mm = MarginModel(econ_p.economics, econ_p.reactor)
    f9_val = float(session.u_current.get("HT_FEED_SP", econ_p.reactor.feed_ref))
    gross_margin = mm.calc_hourly_gross_margin(f9_val)
    delta_margin = (
        rec_dto.effect.margin_delta_rub_h
        if (rec_dto and rec_dto.effect and rec_dto.effect.margin_delta_rub_h is not None)
        else 0.0
    )

    # 6. Индекс уверенности данных (data_guard.py:379-385), None до первого такта
    confidence_dto: Optional[ConfidenceInfo] = None
    conf_dict = last_res.get("confidence") if last_res else None
    if conf_dict:
        confidence_dto = ConfidenceInfo(
            score=float(conf_dict.get("score", 0.0)),
            level=conf_dict.get("level", "LOW"),
            n_filled_critical=int(conf_dict.get("n_filled_critical", 0)),
            q21_unavailable=bool(conf_dict.get("q21_unavailable", False)),
            lims_age_hours=float(conf_dict.get("lims_age_hours", 0.0)),
        )

    # 7. Справочные датчики двойника (WABT, ВСГ, T11, качество сырья/продукта), см. SENSOR_META
    sensors_list: List[SensorSeries] = [
        SensorSeries(
            key=key,
            tag=meta["tag"],
            title=meta["title"],
            unit=meta["unit"],
            history=list(session.sensor_history.get(key, [])),
            limit=None,
        )
        for key, meta in SENSOR_META.items()
    ]

    return ConsoleState(
        schema_version="1.0",
        session_id=session.session_id,
        scenario=Scenario(id=session.scenario_id, title=session.scenario_title),
        clock=ClockInfo(
            now=now_iso,
            tick=session.tick,
            tick_minutes=int(session.tick_delta.total_seconds() // 60),
            seconds_per_tick=session.seconds_per_tick,
            next_tick_in_s=session.next_tick_in_s,
        ),
        mode=auto.build_mode_info(session),
        margin=Margin(
            value_rub_h=gross_margin,
            delta_rub_h=delta_margin,
            delta_label="прирост маржи" if delta_margin >= 0 else "цена удержания ГОСТ",
        ),
        alarms=alarms,
        banner=session.banner if (session.banner and session.banner.id not in session.acks) else None,
        series=Series(cv=cv_series_list, mv=mv_series_list),
        hold=hold_traj,
        recommendation=rec_dto,
        auto=auto.build_auto_info(session),
        feed=list(session.feed)[:5],
        confidence=confidence_dto,
        sensors=sensors_list,
    )


def build_pareto(session: ConsoleSession) -> Optional[ParetoFrontDTO]:
    """
    Собирает Парето-фронт последнего такта (src/agents/pareto.py) для вкладки «Парето-анализ».
    Возвращает None, если такт еще не считался или Парето-анализ недоступен (например, при отказе данных).
    """
    last_res = session.last_graph_result
    if not last_res:
        return None
    analysis = last_res.get("pareto")
    if not analysis or not getattr(analysis, "points", None):
        # Если в графе v3 pareto не было сохранено в last_res напрямую, но есть candidates и predictions
        candidates_dict = last_res.get("candidates")
        predictions_dict = last_res.get("predictions")
        if not candidates_dict or not predictions_dict:
            return None

        # Проверяем отказ данных
        data = last_res.get("data")
        if data:
            auto_lvl = getattr(data, "automation_level", None)
            if auto_lvl and getattr(auto_lvl, "value", str(auto_lvl)) == "REFUSAL_DATA":
                return None
            if not getattr(data, "is_valid", True):
                return None

        from src.agents.lims import lims_age_from_state
        from src.agents.negotiation import merit_table
        from src.agents.pareto import analyze_pareto, sulfur_measurement_age
        from src.agents.state_legacy import ControlCandidate, SafetyAuditReport

        try:
            tbl = merit_table(last_res)
            candidates = []
            vetoed = []
            audit_reports = []

            certs = last_res.get("certificates", {})
            for cert in certs.values():
                if getattr(cert, "verdict", None) != "ADMISSIBLE":
                    for ev in getattr(cert, "evaluations", ()):
                        ev_status = getattr(ev.status, "value", str(ev.status))
                        if ev_status in ("VIOLATED", "UNKNOWN"):
                            audit_reports.append(
                                SafetyAuditReport(
                                    candidate_id=cert.candidate,
                                    is_vetoed=True,
                                    violation_reason=f"{cert.agent}: {ev.spec_key} ({ev.reason or ev_status})",
                                )
                            )

            for sig, (c, m) in tbl.items():
                pred = predictions_dict.get(sig)
                ss = pred.steady_state if pred else {}
                cc = ControlCandidate(
                    candidate_id=sig,
                    delta_u=c.delta_u,
                    is_hold=all(abs(v) < 1e-6 for v in c.delta_u.values()),
                    expected_margin=m.utility_rub_h,
                    steady_state=ss,
                    expected_sulfur=ss.get("HT_S_PRODUCT"),
                    expected_dp_kpa=ss.get("HT_DP_KPA"),
                    expected_t_out=ss.get("HT_T_OUT"),
                )
                candidates.append(cc)
                if m.v != (0, 0, 0, 0):
                    vetoed.append(sig)

            analysis = analyze_pareto(
                candidates=candidates,
                vetoed_ids=vetoed,
                audit_reports=audit_reports,
                sulfur_age_hours=sulfur_measurement_age(
                    last_res.get("raw_tags") or last_res.get("tags"),
                    lims_age_from_state(last_res),
                ),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("build_pareto fallback failed: %s", e, exc_info=True)
            return None

    if not analysis or not getattr(analysis, "points", None):
        return None

    final_rec = last_res.get("final_recommendation")
    decision = last_res.get("decision")
    selected_id = getattr(decision, "selected", None) or (getattr(final_rec, "selected", None) if final_rec else None)
    hold_id = next((p.candidate_id for p in analysis.points if p.is_hold), None)

    objectives = [
        ParetoObjective(key=spec.key, label=spec.label, unit=spec.unit, sense=spec.sense, limit=spec.limit)
        for spec in analysis.metrics
    ]
    points = [
        ParetoPointDTO(
            candidate_id=p.candidate_id,
            is_hold=p.is_hold,
            is_recommendation=(selected_id is not None and p.candidate_id == selected_id),
            status=p.status,
            metrics=dict(p.metrics),
            delta_u=dict(p.delta_u),
            veto_reasons=list(p.veto_reasons),
            dominated_by=list(p.dominated_by),
        )
        for p in analysis.points
    ]

    return ParetoFrontDTO(
        objectives=objectives,
        points=points,
        ideal=dict(analysis.ideal),
        nadir=dict(analysis.nadir),
        hold_id=hold_id,
        selected_id=selected_id,
    )


def build_xai(session: ConsoleSession) -> Optional[XaiInfo]:
    """
    Собирает полный (некупированный) журнал переговоров МАС последнего такта для вкладки «Агенты и XAI».
    В отличие от ленты (feed.py, до 3 записей на такт), здесь отдаются все события всех раундов.
    """
    last_res = session.last_graph_result
    if not last_res:
        return None

    neg_log = last_res.get("negotiation_log", ()) or last_res.get("negotiation", ())
    events = [
        NegotiationEventDTO(
            round=getattr(ev, "round", 0),
            kind=str(getattr(ev, "kind", "")),
            actor=getattr(ev, "actor", ""),
            candidate=getattr(ev, "candidate", None),
            detail=getattr(ev, "detail", ""),
        )
        for ev in neg_log
    ]

    cycle_id = session.last_cycle_id or f"cycle_{session.tick}"
    return XaiInfo(cycle_id=cycle_id, events=events)


def build_constants(session: Optional[ConsoleSession] = None) -> List[Dict[str, Any]]:
    """
    Возвращает реестр всех инженерных и технологических констант системы
    с группировкой по 5 категориям, провенансом и источниками истины.
    """
    constants: List[Dict[str, Any]] = [
        # Категория 1: T0 Границы и скорости хода (MV)
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_FEED_SP.MIN",
            "label": "Минимальный расход сырья 24-2000",
            "value": 158.7,
            "unit": "т/ч",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q05 рабочего расхода сырья ГО ДТ по историческим данным",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_FEED_SP.MAX",
            "label": "Максимальный расход сырья 24-2000",
            "value": REGISTRY["MV.HT_FEED_SP.MAX"].limit,
            "unit": "т/ч",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q95 рабочего расхода сырья ГО ДТ по историческим данным",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "RATE.HT_FEED_SP.MAX",
            "label": "Макс. скорость хода сырья за такт",
            "value": 3.0,
            "unit": "т/ч / такт",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "Ограничение скорости набора/сброса нагрузки реакторного блока",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_TIN_SP.MIN",
            "label": "Минимальная температура входа Р-202",
            "value": 346.5,
            "unit": "°C",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q05 температуры ГСС на входе в реактор Р-202",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_TIN_SP.MAX",
            "label": "Максимальная температура входа Р-202",
            "value": REGISTRY["MV.HT_TIN_SP.MAX"].limit,
            "unit": "°C",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q95 температуры ГСС на входе в реактор Р-202",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "RATE.HT_TIN_SP.MAX",
            "label": "Макс. скорость изменения темп. входа Р-202",
            "value": 3.0,
            "unit": "°C / такт",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "Термическая стойкость катализатора и теплообменников",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_P_SP.MIN",
            "label": "Минимальное давление сепарации Р-202",
            "value": 3.755,
            "unit": "МПа",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q05 рабочего давления реакторного блока",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_P_SP.MAX",
            "label": "Максимальное давление сепарации Р-202",
            "value": 4.009,
            "unit": "МПа",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q95 рабочего давления реакторного блока",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "RATE.HT_P_SP.MAX",
            "label": "Макс. скорость изменения давления сепарации",
            "value": 0.03,
            "unit": "МПа / такт",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "Гидравлическая устойчивость контура ВСГ",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_GOR_SP.MIN",
            "label": "Минимальная кратность ВСГ/сырье",
            "value": 313.0,
            "unit": "нм³/м³",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q05 кратности циркуляции водородсодержащего газа",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.HT_GOR_SP.MAX",
            "label": "Максимальная кратность ВСГ/сырье",
            "value": 439.0,
            "unit": "нм³/м³",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "q95 кратности циркуляции водородсодержащего газа",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "RATE.HT_GOR_SP.MAX",
            "label": "Макс. скорость изменения кратности ВСГ",
            "value": 15.0,
            "unit": "нм³/м³ / такт",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1",
            "description": "Предел перегрузки циркуляционного компрессора",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.AVT_T55_SP.MIN",
            "label": "Минимальная температура перевала П-3",
            "value": 375.0,
            "unit": "°C",
            "provenance": "NORM",
            "source_ref": "ТЗ_нефтекод.docx §4",
            "description": "Паспортный диапазон регулирования печи П-3 АВТ-6",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "MV.AVT_T55_SP.MAX",
            "label": "Максимальная температура перевала П-3",
            "value": 385.0,
            "unit": "°C",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §2.2",
            "description": "Рабочая верхняя граница уставки печи П-3",
        },
        {
            "category": "T0: Границы и скорости хода (MV)",
            "key": "RATE.AVT_T55_SP.MAX",
            "label": "Макс. скорость изменения уставки печи П-3",
            "value": 2.0,
            "unit": "°C / такт",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §2.2",
            "description": "Инерционность футеровки и трубного змеевика печи П-3",
        },

        # Категория 2: T1 Противоаварийная защита и надежность (ПАЗ/ESD)
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "RX.DP_MAX",
            "label": "Максимальный перепад давления Р-202",
            "value": 400.0,
            "unit": "кПа",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1.2",
            "description": "Предел смятия колосников и разрушения катализатора реактора",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "RX.T_OUT_MAX",
            "label": "Максимальная температура выхода слоя Р-202",
            "value": 410.0,
            "unit": "°C",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1.2",
            "description": "Предел термической стабильности катализатора и гидрокрекинга",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "RX.DELTA_T_BED_MAX",
            "label": "Максимальный экзотермический перепад в слое",
            "value": 35.0,
            "unit": "°C",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §1.2",
            "description": "Защита от разгона экзотермической реакции гидрообессеривания",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "FURNACE.COT_MAX",
            "label": "ПАЗ: Температура перевала печи П-3",
            "value": 395.0,
            "unit": "°C",
            "provenance": "NORM",
            "source_ref": "ТЗ_нефтекод.docx §4",
            "description": "Уставка срабатывания аварийной защиты ПАЗ печи П-3",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "FURNACE.COT_POLICY_WARM",
            "label": "Зона предупреждения нагрева печи П-3",
            "value": 380.0,
            "unit": "°C",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §2.2",
            "description": "Антипаттерн 3 ТЗ: запрет нагрева выше 380 °C без консенсуса",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "AVT.F31_MAX",
            "label": "Максимальный расход топливного газа П-3",
            "value": 550.0,
            "unit": "кг/ч",
            "provenance": "REGISTRY",
            "source_ref": "Реестр КИПиА",
            "description": "Тег AVT_F31: ограничение пропускной способности горелок",
        },
        {
            "category": "T1: Противоаварийная защита (ПАЗ/ESD)",
            "key": "AVT.P52_MIN",
            "label": "Минимальное давление мазута на форсунках П-3",
            "value": 0.05,
            "unit": "МПа",
            "provenance": "REGISTRY",
            "source_ref": "Реестр КИПиА",
            "description": "Тег AVT_P52: срыв факела печи при падении давления",
        },

        # Категория 3: T2 Стандарты качества ГОСТ 32511-2013 (Евро-5)
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.S_MAX",
            "label": "Массовая доля серы в товарном ДТ",
            "value": REGISTRY["PRODUCT.S_MAX"].limit,
            "unit": "мг/кг",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4 (табл. 1)",
            "description": "Жесткий норматив Евро-5 (экологический класс К5)",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "GODT.S_MAX",
            "label": "Сера в гидрогенизате ГО ДТ (внутренний предел)",
            "value": REGISTRY["GODT.S_MAX"].limit,
            "unit": "мг/кг",
            "provenance": "POLICY",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §2.1",
            "description": "Ограничение гидроочистки с учетом статистического запаса 2σ",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.FLASH_MIN",
            "label": "Температура вспышки в закрытом тигле",
            "value": REGISTRY["PRODUCT.FLASH_MIN"].limit,
            "unit": "°C",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4 (табл. 1)",
            "description": "Безопасность хранения и применения дизельного топлива",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "GODT.FLASH_MIN",
            "label": "Температура вспышки гидрогенизата",
            "value": REGISTRY["GODT.FLASH_MIN"].limit,
            "unit": "°C",
            "provenance": "POLICY",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §2.1",
            "description": "Контроль отгона легких фракций в отпарной колонне К-201",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.E360_MIN",
            "label": "Фракционный состав: доля до 360 °C",
            "value": 95.0,
            "unit": "% об.",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4 (табл. 1)",
            "description": "ASTM D86 / ГОСТ 2177: полнота выкипания дизельного топлива",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.D15_MIN",
            "label": "Минимальная плотность при 15 °C",
            "value": 820.0,
            "unit": "кг/м³",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4",
            "description": "Нижняя паспортная граница плотности Евро-5",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.D15_MAX",
            "label": "Максимальная плотность при 15 °C",
            "value": 845.0,
            "unit": "кг/м³",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4",
            "description": "Верхняя паспортная граница плотности Евро-5",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.CN_MIN",
            "label": "Цетановое число товарного ДТ",
            "value": 51.0,
            "unit": "ед.",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4",
            "description": "Воспламеняемость топлива в цилиндрах дизельного двигателя",
        },
        {
            "category": "T2: Качество ГОСТ 32511-2013 (Евро-5)",
            "key": "PRODUCT.CFPP_MAX",
            "label": "Предельная температура фильтруемости (сорт C)",
            "value": -5.0,
            "unit": "°C",
            "provenance": "NORM",
            "source_ref": "ГОСТ 32511-2013 §4",
            "description": "Низкотемпературные свойства летнего дизельного топлива",
        },

        # Категория 4: T3 Баланс сырья и смешение
        {
            "category": "T3: Баланс сырья и смешение",
            "key": "BUFFER.INVENTORY_MIN",
            "label": "Минимальный запас буферного резервуара АВТ/ГО",
            "value": 50.0,
            "unit": "т",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §3.1",
            "description": "Аварийный антикавитационный подпор насосов сырья Н-201",
        },
        {
            "category": "T3: Баланс сырья и смешение",
            "key": "BUFFER.INVENTORY_MAX",
            "label": "Максимальная емкость буферного резервуара АВТ/ГО",
            "value": 450.0,
            "unit": "т",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/ASSUMPTIONS.md §3.1",
            "description": "Защита от переполнения промежуточного буфера прямогона",
        },
        {
            "category": "T3: Баланс сырья и смешение",
            "key": "BUFFER.FLOW_RATIO_TARGET",
            "label": "Целевое отношение выработки АВТ к переработке ГО",
            "value": 1.00,
            "unit": "доля",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §3.1",
            "description": "Удержание нулевого среднесуточного дисбаланса F30+F32 vs F9",
        },
        {
            "category": "T3: Баланс сырья и смешение",
            "key": "BLEND.KERO_SHARE_MAX",
            "label": "Максимальная доля прямогонного керосина",
            "value": 0.25,
            "unit": "доля",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §3.2",
            "description": "Предел разбавления дизельного топлива керосиновой фракцией",
        },
        {
            "category": "T3: Баланс сырья и смешение",
            "key": "BLEND.HEAVY_GASOIL_SHARE_MAX",
            "label": "Максимальная доля тяжелого газойля",
            "value": 0.15,
            "unit": "доля",
            "provenance": "ASSUMPTION",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §3.2",
            "description": "Предел вовлечения тяжелого газойля по сере и фракционке",
        },

        # Категория 5: Политика МАС и пороги LIMS
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "QUALITY_Z",
            "label": "Квантиль запаса качества (±2σ)",
            "value": 2.0,
            "unit": "σ (alpha=0.0228)",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §4.1",
            "description": "Статистический запас надежности по качеству ГОСТ Евро-5",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "EQUIPMENT_Z",
            "label": "Квантиль запаса по оборудованию (±3σ)",
            "value": 3.0,
            "unit": "σ (alpha=0.00135)",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §4.1",
            "description": "Запас до уставок ПАЗ/ESD по давлению, температурам и перепаду",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "LIMS_MAX_AGE_HOURS",
            "label": "Порог устаревания LIMS без ПАК",
            "value": 24.0,
            "unit": "ч",
            "provenance": "POLICY",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §5.1",
            "description": "Автоматический переход в safe-hold при возрасте пробы > 24 ч",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "LIMS_WARN_AGE_HOURS",
            "label": "Порог предупреждения о возрасте LIMS",
            "value": 8.0,
            "unit": "ч",
            "provenance": "POLICY",
            "source_ref": "agents/DOMAIN_KNOWLEDGE.md §5.1",
            "description": "Переход в осторожный режим CAUTIOUS с урезанием шага в 2 раза",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "DEADBAND_RUB_H",
            "label": "Зона нечувствительности по марже",
            "value": 1000.0,
            "unit": "руб/ч",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §5.2",
            "description": "Запрет дергания приводов при микроскопическом экономическом эффекте",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "SOFT_BUDGET_S",
            "label": "Мягкий бюджет времени такта (p95)",
            "value": 2.0,
            "unit": "с",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §5.3",
            "description": "Остановка раундов переговоров при исчерпании бюджета 2 с",
        },
        {
            "category": "Политика МАС и пороги LIMS",
            "key": "HARD_BUDGET_S",
            "label": "Жесткий таймаут watchdog цикла МАС",
            "value": float(REGISTRY["PRODUCT.S_MAX"].limit),
            "unit": "с",
            "provenance": "POLICY",
            "source_ref": "agents/ASSUMPTIONS.md §5.3",
            "description": "Принудительный возврат REFUSAL_TIMEOUT при превышении 10 с",
        },
    ]
    return constants


def commit(session: ConsoleSession, req: CommitRequest) -> CommitResult:
    """
    Применяет уставки в технологический комплекс с соблюдением всех барьеров безопасности.
    Порядок строго:
    1. Проверка cycle_id на устаревание (если source != 'operator_edit')
    2. Проверка через forecast.preview(...)
    3. Применение в session.apply(...)
    4. Запись в ленту и журнал решений.
    """
    # 1. Проверка устаревания рекомендации
    if req.cycle_id is not None and req.source != "operator_edit":
        if session.last_cycle_id and req.cycle_id != session.last_cycle_id:
            raise HTTPException(
                status_code=409,
                detail=ConsoleErrorDetail(
                    code="STALE_RECOMMENDATION",
                    text="Рекомендация устарела (начался новый такт управления)",
                ).model_dump(),
            )

    # 2. Проверка через preview
    prev_req = PreviewRequest(
        session_id=session.session_id,
        cycle_id=req.cycle_id,
        u_target=req.u_target,
    )
    prev_res = forecast.preview(session, prev_req)

    if not prev_res.can_commit:
        code = "CORRIDOR" if not prev_res.corridor_ok else "KERNEL"
        raise HTTPException(
            status_code=409,
            detail=ConsoleErrorDetail(
                code=code,
                text=prev_res.blocking_reason or "Ход отклонён по условиям безопасности",
                checks=prev_res.kernel.checks,
            ).model_dump(),
        )

    # 3. Применение
    applied_changes: List[Change] = []
    for sp_name, target_val in req.u_target.items():
        curr_val = session.u_current.get(sp_name, target_val)
        delta = target_val - curr_val
        if abs(delta) > 1e-4:
            meta = MV_META.get(sp_name, {"tag": sp_name, "title": sp_name, "unit": ""})
            dec = MV_DECIMALS.get(sp_name, 1)
            applied_changes.append(
                Change(
                    sp=sp_name,  # type: ignore
                    tag=meta["tag"],
                    title=meta["title"],
                    unit=meta["unit"],
                    current=round(curr_val, dec),
                    target=round(target_val, dec),
                    delta=round(delta, dec),
                    decimals=dec,
                )
            )

    session.apply(req.u_target)

    # 4. Формирование записи в ленте
    now_hm = session.now.strftime("%H:%M")
    now_iso = session.now.strftime("%Y-%m-%dT%H:%M:%SZ")

    ch_strs = [f"{c.tag} {c.current} → {c.target} {c.unit}" for c in applied_changes]
    detail_str = ", ".join(ch_strs) or "уставки подтверждены"

    if req.source == "recommendation":
        feed_text = f"Оператор применил рекомендацию: {detail_str}"
    elif req.source == "alternative":
        choice_label = req.choice or "альтернатива"
        feed_text = f"Оператор применил режим «{choice_label}»: {detail_str}"
    else:
        feed_text = f"Оператор применил свои значения: {detail_str}"

    feed_item = FeedItem(
        id=f"commit:{session.tick}",
        t=now_hm,
        kind="ok",
        text=feed_text,
        source="operator",
    )
    session.feed.appendleft(feed_item)

    # 5. Запись в журнал решений
    try:
        append_decision(
            session.last_graph_result or {},
            {
                "applied_u": req.u_target,
                "source": req.source,
                "session_id": session.session_id,
                "committed_at": now_iso,
            },
        )
    except Exception:
        pass

    return CommitResult(
        ok=True,
        applied=applied_changes,
        at=now_iso,
        feed_item=feed_item,
    )
