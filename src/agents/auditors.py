"""Агенты аудита технологической безопасности и качества (Reliability & Quality Agents).

Реализуют Стадию 1 двухстадийного гибридного арбитража согласно System_Design.md и implementation_plan_v2.md:
1. ReliabilityAgent: Агент противоаварийной защиты (ПАЗ/ESD).
   Проверяет соблюдение ограничений оборудования на установившемся режиме и траектории
   (HT_DP_MAX_KPA, HT_T_OUT_MAX, HT_GOR_MIN, FEED_TO_AVT) и начисляет лог-барьеры риска.
2. QualityAgent: Агент соблюдения стандарта ГОСТ 32511-2013 (Евро-5).
   Проверяет статистические буферы по сере (ADR-12), температуре вспышки, T95,
   а также допустимость рецепта блендинга через прогноз запасов резервуаров.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.agents.blending import AdditiveSegment, BlendComponent, BlendProblem, solve_blend
from src.agents.constraints import assess_limit, stat_offset
from src.agents.limits import (
    F31_MIN,
    FEED_TO_AVT,
    FLASH_PRODUCT_MIN,
    HT_DP_MAX_KPA,
    HT_GOR_MIN,
    HT_T_OUT_MAX,
    LEGACY_W10_MAX,
    P52_MAX,
    QUALITY_Z,
    SIGMA_FLASH_C,
    SIGMA_S0_PPM,
    SIGMA_T95_C,
    SULFUR_PRODUCT_MAX,
    T55_MAX,
    T95_PRODUCT_MAX,
)
from src.agents.state import ControlCandidate, MasGraphState, SafetyAuditReport
from src.agents.tanks import ComponentTank


class ReliabilityAgent:
    """Агент Надежности: аудит технологических ограничений оборудования и барьеры ПАЗ."""

    # 5% защитные барьеры отсечения Hard-Veto (Таблица 4.1 System_Design.md)
    MAX_COT_T55: float = T55_MAX.hi or 386.40
    MAX_W10: float = LEGACY_W10_MAX
    MAX_P52: float = P52_MAX.hi or 0.077
    MIN_F31: float = F31_MIN.lo or 362.50

    COT_WARNING_ZONE: float = 380.0
    COT_SPAN: float = 12.0
    MU_PENALTY: float = 1000.0

    @classmethod
    def audit(
        cls,
        cand: ControlCandidate,
        hold_cand: Optional[ControlCandidate] = None,
    ) -> SafetyAuditReport:
        """
        Аудит кандидата: оценка динамических траекторий, установившегося режима и барьерных штрафов.
        """
        violated_limits: List[str] = []
        limit_margins: Dict[str, float] = {}
        violation_reasons: List[str] = []
        penalty = 0.0

        hold_traj = hold_cand.trajectory if hold_cand else {}
        ss = cand.steady_state
        traj = cand.trajectory

        # 1. Проверка динамических параметров при наличии steady_state / trajectory
        if ss or traj:
            # HT_DP_KPA (перепад давления на реакторе Р-202)
            dp_ss = ss.get("HT_DP_KPA", cand.expected_dp_kpa or (cand.expected_w10 * 98.0665 if cand.expected_w10 else 177.0))
            dp_traj = traj.get("HT_DP_KPA")
            h_dp_traj = hold_traj.get("HT_DP_KPA")
            ass_dp = assess_limit("HT_DP_KPA", dp_traj, dp_ss, h_dp_traj, HT_DP_MAX_KPA.hi or 454.5, "max")
            limit_margins["HT_DP_KPA"] = ass_dp.margin
            if ass_dp.vetoed:
                violated_limits.append("HT_DP_KPA")
                if ass_dp.reason:
                    violation_reasons.append(ass_dp.reason)

            # HT_T_OUT (температура выхода Р-202, HT_T11)
            tout_ss = ss.get("HT_T_OUT", cand.expected_t_out or 364.0)
            tout_traj = traj.get("HT_T_OUT")
            h_tout_traj = hold_traj.get("HT_T_OUT")
            ass_tout = assess_limit("HT_T_OUT", tout_traj, tout_ss, h_tout_traj, HT_T_OUT_MAX.hi or 390.0, "max")
            limit_margins["HT_T_OUT"] = ass_tout.margin
            if ass_tout.vetoed:
                violated_limits.append("HT_T_OUT")
                if ass_tout.reason:
                    violation_reasons.append(ass_tout.reason)

            # HT_GOR (кратность ВСГ / сырье)
            gor_ss = ss.get("HT_GOR", cand.expected_gor or 360.0)
            gor_traj = traj.get("HT_GOR")
            h_gor_traj = hold_traj.get("HT_GOR")
            ass_gor = assess_limit("HT_GOR", gor_traj, gor_ss, h_gor_traj, HT_GOR_MIN.lo or 300.0, "min")
            limit_margins["HT_GOR"] = ass_gor.margin
            if ass_gor.vetoed:
                violated_limits.append("HT_GOR")
                if ass_gor.reason:
                    violation_reasons.append(ass_gor.reason)

            # FEED_TO_AVT (отношение расхода сырья ГО к дизелю АВТ, [0.81, 1.26])
            favt_ss = ss.get("HT_FEED_TO_AVT", cand.expected_feed_to_avt or 1.0)
            favt_traj = traj.get("HT_FEED_TO_AVT")
            h_favt_traj = hold_traj.get("HT_FEED_TO_AVT")
            ass_favt_max = assess_limit("FEED_TO_AVT_MAX", favt_traj, favt_ss, h_favt_traj, FEED_TO_AVT.hi or 1.26, "max")
            ass_favt_min = assess_limit("FEED_TO_AVT_MIN", favt_traj, favt_ss, h_favt_traj, FEED_TO_AVT.lo or 0.81, "min")
            limit_margins["FEED_TO_AVT"] = min(ass_favt_max.margin, ass_favt_min.margin)
            if ass_favt_max.vetoed:
                violated_limits.append("FEED_TO_AVT_MAX")
                if ass_favt_max.reason:
                    violation_reasons.append(ass_favt_max.reason)
            if ass_favt_min.vetoed:
                violated_limits.append("FEED_TO_AVT_MIN")
                if ass_favt_min.reason:
                    violation_reasons.append(ass_favt_min.reason)

            # Штрафы лог-барьеров
            t55 = cand.expected_t55 or ss.get("AVT_T55", 0.0)
            if t55 > cls.COT_WARNING_ZONE:
                s_marg = max(cls.MAX_COT_T55 - t55, 1e-4)
                penalty += -cls.MU_PENALTY * math.log(s_marg / cls.COT_SPAN)

            if 375.0 < tout_ss < (HT_T_OUT_MAX.hi or 390.0):
                s_marg = max((HT_T_OUT_MAX.hi or 390.0) - tout_ss, 1e-4)
                penalty += -cls.MU_PENALTY * math.log(s_marg / 15.0)

            if 400.0 < dp_ss < (HT_DP_MAX_KPA.hi or 454.5):
                s_marg = max((HT_DP_MAX_KPA.hi or 454.5) - dp_ss, 1e-4)
                penalty += -cls.MU_PENALTY * math.log(s_marg / 54.5)

        # 2. Скалярные проверки (для обратной совместимости со старыми кандидатами)
        if cand.expected_t55 is not None and cand.expected_t55 > cls.MAX_COT_T55:
            violated_limits.append("AVT_T55")
            violation_reasons.append(f"ESD_VETO: T55={cand.expected_t55:.2f}°C превышает 5% защитный порог ПАЗ ({cls.MAX_COT_T55:.2f}°C)")

        if cand.expected_w10 is not None and not cand.trajectory and cand.expected_w10 > cls.MAX_W10:
            violated_limits.append("HT_W10")
            violation_reasons.append(f"ESD_VETO: W10={cand.expected_w10:.3f} кгс/см² превышает предел ({cls.MAX_W10:.3f} кгс/см²)")

        if cand.expected_p52 is not None and cand.expected_p52 > cls.MAX_P52:
            violated_limits.append("AVT_P52")
            violation_reasons.append(f"ESD_VETO: P52={cand.expected_p52:.3f} кгс/см² превышает порог захлебывания ({cls.MAX_P52:.3f} кгс/см²)")

        if cand.expected_f31 is not None and cand.expected_f31 < cls.MIN_F31:
            violated_limits.append("AVT_F31")
            violation_reasons.append(f"ESD_VETO: Расход сырья F31={cand.expected_f31:.1f} м³/ч ниже безопасного ({cls.MIN_F31:.1f} м³/ч)")

        if not (ss or traj) and cand.expected_t55 is not None and cand.expected_t55 > cls.COT_WARNING_ZONE:
            safety_margin = max(cls.MAX_COT_T55 - cand.expected_t55, 1e-4)
            normalized_margin = safety_margin / cls.COT_SPAN
            penalty = -cls.MU_PENALTY * math.log(normalized_margin)

        is_vetoed = len(violated_limits) > 0
        v_reason = "; ".join(violation_reasons) if violation_reasons else None
        penalty = max(0.0, penalty) if not is_vetoed else 0.0

        return SafetyAuditReport(
            candidate_id=cand.candidate_id,
            is_vetoed=is_vetoed,
            risk_penalty_rub_h=round(penalty, 2),
            violation_reason=v_reason,
            agent="reliability",
            violated_limits=violated_limits,
            limit_margins=limit_margins,
        )

    @classmethod
    def audit_candidate(cls, cand: ControlCandidate) -> Tuple[bool, Optional[str], float]:
        """Устаревший скалярный метод для обратной совместимости с тестами step4."""
        report = cls.audit(cand)
        return report.is_vetoed, report.violation_reason, report.risk_penalty_rub_h


class QualityAgent:
    """Агент Качества: аудит соответствия ГОСТ 32511-2013 (Евро-5)."""

    MAX_SULFUR_PPM: float = 9.50
    MIN_DENSITY: float = 821.25
    MAX_DENSITY: float = 843.75

    @classmethod
    def audit(
        cls,
        cand: ControlCandidate,
        hold_cand: Optional[ControlCandidate] = None,
        tags: Optional[Mapping[str, float]] = None,
        lims_age_hours: float = 0.0,
        tanks: Optional[Dict[str, ComponentTank]] = None,
        z: Optional[float] = None,
    ) -> SafetyAuditReport:
        """
        Аудит качества: расчет статистических буферов и проверка рецептуры блендинга.
        """
        violated_limits: List[str] = []
        limit_margins: Dict[str, float] = {}
        violation_reasons: List[str] = []

        z_eff = z if z is not None else QUALITY_Z

        # Определение возраста измерений для статистического запаса
        age_s = lims_age_hours
        if tags and "HT_Q21" in tags:
            q21_val = tags["HT_Q21"]
            if q21_val is not None and not math.isnan(q21_val) and q21_val not in (307.0, 313.0):
                age_s = 0.0

        age_flash = lims_age_hours
        if tags and "HT_T18" in tags:
            t18_val = tags["HT_T18"]
            if t18_val is not None and not math.isnan(t18_val) and t18_val not in (307.0, 313.0):
                age_flash = 0.0

        age_t95 = lims_age_hours

        hold_traj = hold_cand.trajectory if hold_cand else {}
        ss = cand.steady_state
        traj = cand.trajectory

        if ss or traj:
            # 1. Сера с учетом статистического смещения stat_offset
            offset_s = stat_offset(SIGMA_S0_PPM, age_s, z_eff)
            s_ss = ss.get("HT_S_PRODUCT", cand.expected_sulfur or 8.6)
            s_traj = traj.get("HT_S_PRODUCT")
            h_s_traj = hold_traj.get("HT_S_PRODUCT")
            ass_s = assess_limit("HT_S_PRODUCT", s_traj, s_ss, h_s_traj, SULFUR_PRODUCT_MAX.hi or 10.0, "max", offset_s)
            limit_margins["HT_S_PRODUCT"] = ass_s.margin
            if ass_s.vetoed:
                violated_limits.append("HT_S_PRODUCT")
                if ass_s.reason:
                    violation_reasons.append(ass_s.reason)

            # 2. Температура вспышки
            offset_f = stat_offset(SIGMA_FLASH_C, age_flash, z_eff)
            f_ss = ss.get("HT_FLASH", cand.expected_flash or 68.0)
            f_traj = traj.get("HT_FLASH")
            h_f_traj = hold_traj.get("HT_FLASH")
            ass_f = assess_limit("HT_FLASH", f_traj, f_ss, h_f_traj, FLASH_PRODUCT_MIN.lo or 55.0, "min", offset_f)
            limit_margins["HT_FLASH"] = ass_f.margin
            if ass_f.vetoed:
                violated_limits.append("HT_FLASH")
                if ass_f.reason:
                    violation_reasons.append(ass_f.reason)

            # 3. Температура конца перегонки 95% (T95)
            offset_t95 = stat_offset(SIGMA_T95_C, age_t95, z_eff)
            t95_ss = ss.get("HT_T95_PRODUCT", cand.expected_t95 or 347.0)
            t95_traj = traj.get("HT_T95_PRODUCT")
            h_t95_traj = hold_traj.get("HT_T95_PRODUCT")
            ass_t95 = assess_limit("HT_T95_PRODUCT", t95_traj, t95_ss, h_t95_traj, T95_PRODUCT_MAX.hi or 360.0, "max", offset_t95)
            limit_margins["HT_T95_PRODUCT"] = ass_t95.margin
            if ass_t95.vetoed:
                violated_limits.append("HT_T95_PRODUCT")
                if ass_t95.reason:
                    violation_reasons.append(ass_t95.reason)

            # 4. Допустимость рецепта блендинга через прогноз запаса в резервуаре ГО ДТ
            flow_tph = ss.get("HT_FEED_SP", 219.6) * 0.98
            horizon_h = (cand.horizon_steps * 10.0) / 60.0 if cand.horizon_steps else 6.0

            base_tanks = tanks or {}
            godt_tank = base_tanks.get("GODT")
            if godt_tank is None:
                godt_tank = ComponentTank(
                    name="ГО ДТ (резервуар)",
                    stock_t=5000.0,
                    props={"S_ppm": 8.6, "D15": 836.1, "Flash": 68.0, "CFPP": -6.0, "T95": 347.0, "CN": 53.75},
                    prop_sources={"S_ppm": "LIMS", "D15": "LIMS", "Flash": "LIMS", "CFPP": "LIMS", "T95": "LIMS", "CN": "LIMS"},
                )

            ss_props = {
                "S_ppm": s_ss,
                "D15": ss.get("HT_D15_PRODUCT", 836.1),
                "Flash": f_ss,
                "CFPP": ss.get("HT_CFPP_PRODUCT", -6.0),
                "T95": t95_ss,
                "CN": ss.get("HT_CN_PRODUCT", 53.75),
            }
            fc_props = godt_tank.forecast(flow_tph, ss_props, horizon_h)

            c_godt = BlendComponent(
                name="ГО ДТ (резервуар)",
                price_rub_t=60000.0,
                stock_t=godt_tank.stock_t + flow_tph * horizon_h,
                v_min=0.50,
                v_max=1.0,
                s_ppm=fc_props["S_ppm"],
                d15=fc_props["D15"],
                flash_c=fc_props["Flash"],
                cfpp_c=fc_props["CFPP"],
                t95_c=fc_props["T95"],
                cn=fc_props["CN"],
                props_source={},
            )
            kerosene_tank = base_tanks.get("Kerosene")
            c_kero = BlendComponent(
                name="Керосин (гидроочищенный)",
                price_rub_t=85000.0,
                stock_t=kerosene_tank.stock_t if kerosene_tank else 800.0,
                v_min=0.0,
                v_max=0.20,
                s_ppm=2.0,
                d15=785.0,
                flash_c=42.0,
                cfpp_c=-45.0,
                t95_c=240.0,
                cn=42.0,
                props_source={},
            )
            gasoil_tank = base_tanks.get("Gasoil")
            c_gasoil = BlendComponent(
                name="Газойль (гидроочищенный)",
                price_rub_t=50000.0,
                stock_t=gasoil_tank.stock_t if gasoil_tank else 1500.0,
                v_min=0.0,
                v_max=0.20,
                s_ppm=8.0,
                d15=860.0,
                flash_c=90.0,
                cfpp_c=0.0,
                t95_c=365.0,
                cn=45.0,
                props_source={},
            )
            additives = (
                AdditiveSegment(name="Депрессорная А_1", max_kg_t=0.5, effect_per_kg_t=12.0, price_rub_t=1500000.0, target="cfpp"),
                AdditiveSegment(name="Депрессорная А_2", max_kg_t=1.0, effect_per_kg_t=3.76, price_rub_t=1500000.0, target="cfpp"),
                AdditiveSegment(name="Цетаноповышающая B_1", max_kg_t=0.5, effect_per_kg_t=6.0, price_rub_t=1200000.0, target="cn"),
                AdditiveSegment(name="Цетаноповышающая B_2", max_kg_t=1.0, effect_per_kg_t=2.0, price_rub_t=1200000.0, target="cn"),
            )
            blend_prob = BlendProblem(
                components=(c_godt, c_kero, c_gasoil),
                additives=additives,
                batch_t=2000.0,
                target_cfpp=-15.0,
            )
            blend_res = solve_blend(blend_prob)
            if not blend_res.is_feasible:
                violated_limits.append("BLEND_FEASIBILITY")
                violation_reasons.append(f"GOST_VETO_BLEND_INFEASIBLE: {blend_res.infeasibility_reason}")

        else:
            # Скалярный путь старых кандидатов
            if cand.expected_sulfur is not None and cand.expected_sulfur > cls.MAX_SULFUR_PPM:
                violated_limits.append("HT_S_PRODUCT")
                violation_reasons.append(f"GOST_VETO: Сера гидрогенизата {cand.expected_sulfur:.2f} ppm > 5% предела ГОСТ ({cls.MAX_SULFUR_PPM:.2f} ppm)")

            if cand.expected_density is not None:
                if cand.expected_density < cls.MIN_DENSITY or cand.expected_density > cls.MAX_DENSITY:
                    violated_limits.append("HT_D15_PRODUCT")
                    violation_reasons.append(f"GOST_VETO: Плотность {cand.expected_density:.1f} кг/м³ вне допуска ГОСТ [{cls.MIN_DENSITY:.2f}, {cls.MAX_DENSITY:.2f}]")

        is_vetoed = len(violated_limits) > 0
        v_reason = "; ".join(violation_reasons) if violation_reasons else None

        return SafetyAuditReport(
            candidate_id=cand.candidate_id,
            is_vetoed=is_vetoed,
            risk_penalty_rub_h=0.0,
            violation_reason=v_reason,
            agent="quality",
            violated_limits=violated_limits,
            limit_margins=limit_margins,
        )

    @classmethod
    def audit_candidate(cls, cand: ControlCandidate) -> Tuple[bool, Optional[str]]:
        """Устаревший метод для обратной совместимости с тестами step4."""
        report = cls.audit(cand)
        return report.is_vetoed, report.violation_reason


# =========================================================================
# Узлы LangGraph (Параллельный Fan-Out)
# =========================================================================

def node_reliability_agent(state: MasGraphState) -> Dict[str, Any]:
    """Узел LangGraph: аудит безопасности оборудования Агентом Надежности."""
    candidates = state.get("candidates", [])
    hold_cand = next((c for c in candidates if c.is_hold), None)
    vetoed: List[str] = []
    penalties: Dict[str, float] = {}
    reports: List[SafetyAuditReport] = []

    for cand in candidates:
        report = ReliabilityAgent.audit(cand, hold_cand=hold_cand)
        reports.append(report)
        if report.is_vetoed:
            vetoed.append(cand.candidate_id)
        elif report.risk_penalty_rub_h > 0.0:
            penalties[cand.candidate_id] = report.risk_penalty_rub_h

    return {
        "vetoed_candidates": vetoed,
        "risk_penalties": penalties,
        "audit_reports": reports,
    }


def node_quality_agent(state: MasGraphState) -> Dict[str, Any]:
    """Узел LangGraph: аудит соответствия ГОСТ Агентом Качества."""
    candidates = state.get("candidates", [])
    hold_cand = next((c for c in candidates if c.is_hold), None)
    tags = state.get("tags")
    lims_age = 0.0
    if "raw_telemetry" in state and hasattr(state["raw_telemetry"], "lims_age_hours"):
        lims_age = state["raw_telemetry"].lims_age_hours
    elif tags and "lims_age_hours" in tags:
        lims_age = float(tags["lims_age_hours"])
    elif "confidence" in state and "lims_age_hours" in state["confidence"]:
        lims_age = float(state["confidence"]["lims_age_hours"])

    vetoed: List[str] = []
    reports: List[SafetyAuditReport] = []

    for cand in candidates:
        report = QualityAgent.audit(cand, hold_cand=hold_cand, tags=tags, lims_age_hours=lims_age)
        reports.append(report)
        if report.is_vetoed:
            vetoed.append(cand.candidate_id)

    return {
        "vetoed_candidates": vetoed,
        "audit_reports": reports,
    }
