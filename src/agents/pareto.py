"""Алгоритмическое выделение и визуализация Парето-фронта кандидатов управления (Критерий 4, п. 6.5 ТЗ).

Реализует Шаг 7 восьмишагового цикла ТЗ (§2.3) — многокритериальное сравнение кандидатов,
прошедших Hard-Veto Агентов Надежности и Качества. Решение принимает арбитраж (Net Utility);
фронт объясняет выбор: рекомендация — Парето-оптимальное (компромиссное) решение в допустимой зоне.

1. Цели доминирования — три оси п. 6.5 ТЗ «себестоимость ↔ качество ↔ износ катализатора»:
   - net_margin       [max, руб/ч] = ΔMargin_hold − барьерный штраф ПАЗ (та же Net Utility, что в арбитраже);
   - sulfur_giveaway  [min, ppm]   = max(0, S_gv − Ŝ_ss), S_gv = 10 − GIVEAWAY_Z·σ_S(age): переочистка
     сверх экономически обоснованной границы (стратегия минимизации giveaway, tz:73). Сторона превышения
     закрыта вето Агента Качества «Ŝ + 2σ ≤ 10» (tz:598), поэтому ось штрафует только переочистку.
     GIVEAWAY_Z — минимакс сожаления по неизвестной цене брака (Savage L.J. The theory of statistical
     decision // J. Amer. Statist. Assoc. 1951. V. 46. P. 55–67), scripts/estimate_quality_uncertainty.py;
   - bed_temperature  [min, °C]    = WABT = HT_BED_MEAN = (HT_T6 + HT_T11)/2 — тепловой стресс катализатора.
   Контролируемые метрики (не цели): sulfur_ucb = Ŝ + 2σ и P(S > 10), перепад Р-202 (HT_P8).
   Перепад учтен в Net Utility лог-барьером ReliabilityAgent; при M ≥ 4 целях доля недоминируемых точек
   стремится к 100% (Ishibuchi H., Tsukamoto N., Nojima Y. // IEEE CEC. 2008. P. 2419–2426).
2. Safety Ladder (ПАЗ ≻ Качество ≻ Экономика): ветированный кандидат исключается ДО ранжирования.
3. Быстрая недоминируемая сортировка (Deb K. et al. // IEEE Trans. Evol. Comput. 2002. V. 6, № 2.
   P. 182–197) по отношению доминирования (Kung H.T. et al. // J. ACM. 1975. V. 22, № 4. P. 469–476).
4. Цена компромисса для оператора: для рекомендации ищутся ближайшие альтернативы на фронте —
   «безопаснее по сере» (ниже P(S > 10)) и «бережнее к катализатору» (ниже WABT) с наименьшей потерей маржи.
5. Фигуры Plotly (3D, 2D-проекция, параллельные координаты) для пульта оператора.

Физическое обоснование оси износа: коксовая дезактивация Co-Mo/Ni-Mo ускоряется ростом температуры слоя
(Furimsky E., Massoth F.E. // Catal. Today. 1999. V. 52. P. 381–495; Jiang H. et al. // ACS Omega. 2025.
V. 10. P. 30501, doi:10.1021/acsomega.5c02328); EOR = 390 °C при ≈ 1.9 °C/мес (Stratiev D. et al. // Oil & Gas
Journal. 2006). Аррениусовская k_d монотонна по WABT при любой E_d > 0, поэтому E_d не вводится.
Ограничение: защитный эффект давления H₂ и кратности ВСГ на дезактивацию не учитывается (нет числа
из проверенного источника) — ходы давлением на фронте выглядят хуже, чем в действительности.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from src.agents.constraints import stat_offset
from src.agents.limits import (
    GIVEAWAY_Z,
    HT_DP_MAX_KPA,
    HT_T_OUT_MAX,
    QUALITY_Z,
    SIGMA_S0_PPM,
    SULFUR_PRODUCT_MAX,
)
from src.agents.lims import lims_age_from_state
from src.agents.state import ControlCandidate, SafetyAuditReport

Sense = Literal["max", "min"]
PointStatus = Literal["pareto", "dominated", "vetoed", "incomplete"]

# Аппаратные значения клампинга КИП (ТЗ, Callout 2), при которых онлайн-анализатор считается недостоверным
_CLAMPED_VALUES = (307.0, 313.0)


# =============================================================================
# Контракты данных
# =============================================================================

class ObjectiveSpec(BaseModel):
    """Спецификация метрики многокритериального сравнения."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    label: str
    unit: str
    sense: Sense
    limit: Optional[float] = Field(default=None, description="Норматив / предел оборудования для отображения")
    source: str = ""


NET_MARGIN = ObjectiveSpec(
    key="net_margin",
    label="Чистая маржа к hold",
    unit="руб/ч",
    sense="max",
    source="MarginModel.evaluate() − барьерный штраф ReliabilityAgent (Net Utility арбитража)",
)
SULFUR_GIVEAWAY = ObjectiveSpec(
    key="sulfur_giveaway",
    label="Переочистка по сере",
    unit="ppm",
    sense="min",
    source="max(0, 10 − GIVEAWAY_Z·σ_S(age) − Ŝ): стратегия минимизации giveaway (tz:73), минимакс сожаления",
)
BED_TEMPERATURE = ObjectiveSpec(
    key="bed_temperature",
    label="WABT слоя Р-202",
    unit="°C",
    sense="min",
    limit=HT_T_OUT_MAX.hi,
    source="HT_BED_MEAN=(T6+T11)/2; скорость дезактивации монотонна по WABT (Furimsky & Massoth, 1999)",
)
SULFUR_UCB = ObjectiveSpec(
    key="sulfur_ucb",
    label="Сера Ŝ + 2σ",
    unit="ppm",
    sense="min",
    limit=SULFUR_PRODUCT_MAX.hi,
    source="Правило Агента Качества «прогноз + 2σ» (tz:598); ГОСТ 32511 ≤ 10 мг/кг (NORM)",
)
REACTOR_DP = ObjectiveSpec(
    key="reactor_dp",
    label="Перепад Р-202 (HT_P8)",
    unit="кПа",
    sense="min",
    limit=HT_DP_MAX_KPA.hi,
    source="Модифицированное уравнение Эргуна двойника; предел 454.5 кПа (ASSUMPTION)",
)

ALL_METRICS: Tuple[ObjectiveSpec, ...] = (NET_MARGIN, SULFUR_GIVEAWAY, BED_TEMPERATURE, SULFUR_UCB, REACTOR_DP)
DEFAULT_OBJECTIVES: Tuple[ObjectiveSpec, ...] = (NET_MARGIN, SULFUR_GIVEAWAY, BED_TEMPERATURE)


class ParetoPoint(BaseModel):
    """Кандидат управления в пространстве метрик."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    delta_u: Dict[str, float] = Field(default_factory=dict)
    is_hold: bool = False
    status: PointStatus
    rank: Optional[int] = Field(default=None, description="Номер недоминируемого слоя (1 — Парето-фронт)")
    metrics: Dict[str, Optional[float]] = Field(default_factory=dict)
    sulfur_forecast_ppm: Optional[float] = Field(default=None, description="Прогноз серы Ŝ на установившемся режиме")
    dominated_by: List[str] = Field(default_factory=list)
    p_offspec: Optional[float] = Field(default=None, description="P(S > 10 ppm) на установившемся режиме")
    veto_reasons: List[str] = Field(default_factory=list)


class ParetoAnalysis(BaseModel):
    """Результат Парето-анализа одного такта управления."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    objectives: List[ObjectiveSpec] = Field(description="Цели, по которым вычислено доминирование")
    metrics: List[ObjectiveSpec] = Field(description="Все рассчитанные метрики (цели + контролируемые)")
    points: List[ParetoPoint] = Field(default_factory=list)
    front_ids: List[str] = Field(default_factory=list)
    ideal: Dict[str, float] = Field(default_factory=dict, description="Лучшие значения целей на фронте")
    nadir: Dict[str, float] = Field(default_factory=dict, description="Худшие значения целей на фронте")
    sulfur_sigma_ppm: float = 0.0
    sulfur_offset_ppm: float = Field(default=0.0, description="Запас вето z·σ_S(age)")
    giveaway_boundary_ppm: float = Field(default=0.0, description="Переочистка, если Ŝ ниже этой границы")
    sulfur_age_hours: float = 0.0

    @property
    def n_admissible(self) -> int:
        return sum(1 for p in self.points if p.status in ("pareto", "dominated"))

    def point(self, candidate_id: Optional[str]) -> Optional[ParetoPoint]:
        return next((p for p in self.points if p.candidate_id == candidate_id), None)

    def is_on_front(self, candidate_id: Optional[str]) -> bool:
        return candidate_id is not None and candidate_id in self.front_ids

    def metric(self, key: str) -> ObjectiveSpec:
        for spec in self.metrics:
            if spec.key == key:
                return spec
        raise KeyError(f"Метрика '{key}' не рассчитана: {[m.key for m in self.metrics]}")


class ParetoAlternative(BaseModel):
    """Ближайшая альтернатива на фронте и цена перехода к ней от рекомендации."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["safer_sulfur", "gentler_catalyst"]
    candidate_id: str
    delta_margin_rub_h: float
    delta_sulfur_ppm: Optional[float] = None
    risk_from_pct: Optional[float] = None
    risk_to_pct: Optional[float] = None
    delta_wabt_c: Optional[float] = None


# =============================================================================
# Ядро: доминирование и недоминируемая сортировка
# =============================================================================

def to_minimization(values: Sequence[float], senses: Sequence[Sense]) -> Tuple[float, ...]:
    """Приводит вектор целей к задаче минимизации (цели max меняют знак)."""
    return tuple(-float(v) if s == "max" else float(v) for v, s in zip(values, senses))


def dominates(a: Sequence[float], b: Sequence[float], tol: float = 0.0) -> bool:
    """
    a доминирует b (минимизация): a не хуже b по всем целям и строго лучше хотя бы по одной.
    tol — зона неразличимости по каждой цели (0.0 — строгое отношение: транзитивно и ациклично).
    """
    strictly_better = False
    for ai, bi in zip(a, b):
        if ai > bi + tol:
            return False
        if ai < bi - tol:
            strictly_better = True
    return strictly_better


def non_dominated_sort(vectors: Sequence[Sequence[float]], tol: float = 0.0) -> List[List[int]]:
    """
    Быстрая недоминируемая сортировка (Deb et al., 2002), O(M·N²).
    Возвращает слои индексов: [0] — Парето-фронт, [1] — фронт после его удаления и т.д.
    """
    n = len(vectors)
    if n == 0:
        return []

    dominated_sets: List[List[int]] = [[] for _ in range(n)]
    domination_count = [0] * n
    for p in range(n):
        for q in range(p + 1, n):
            if dominates(vectors[p], vectors[q], tol):
                dominated_sets[p].append(q)
                domination_count[q] += 1
            elif dominates(vectors[q], vectors[p], tol):
                dominated_sets[q].append(p)
                domination_count[p] += 1

    fronts: List[List[int]] = []
    current = [i for i in range(n) if domination_count[i] == 0]
    assigned = set(current)
    while current:
        fronts.append(sorted(current))
        nxt: List[int] = []
        for p in current:
            for q in dominated_sets[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    nxt.append(q)
        assigned.update(nxt)
        current = nxt

    # При tol > 0 отношение может стать нетранзитивным: зацикленные точки уходят в последний слой
    leftovers = [i for i in range(n) if i not in assigned]
    if leftovers:
        fronts.append(leftovers)
    return fronts


def pareto_front_indices(vectors: Sequence[Sequence[float]], tol: float = 0.0) -> List[int]:
    """Индексы недоминируемых точек (минимизация)."""
    fronts = non_dominated_sort(vectors, tol)
    return fronts[0] if fronts else []


# =============================================================================
# Доменный слой: извлечение метрик из кандидатов и аудитов
# =============================================================================

def _finite(*values: Any) -> Optional[float]:
    """Первое конечное числовое значение из списка."""
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


def _p_exceed(value: float, limit: float, sigma: float) -> float:
    """P(X > limit) для X ~ N(value, sigma²)."""
    if sigma <= 0.0:
        return 1.0 if value > limit else 0.0
    return 0.5 * math.erfc((limit - value) / (sigma * math.sqrt(2.0)))


def sulfur_sigma(age_hours: float) -> float:
    """σ_S(age) = σ_S0·√(1 + age/12) — тот же закон, что в stat_offset() Агента Качества."""
    return stat_offset(SIGMA_S0_PPM, age_hours, 1.0)


def giveaway_boundary(age_hours: float) -> float:
    """Граница переочистки по прогнозу серы: Ŝ < 10 − GIVEAWAY_Z·σ_S(age)."""
    return (SULFUR_PRODUCT_MAX.hi or 10.0) - GIVEAWAY_Z * sulfur_sigma(age_hours)


def candidate_metrics(
    cand: ControlCandidate,
    risk_penalty: float = 0.0,
    sulfur_age_hours: float = 0.0,
    z: float = QUALITY_Z,
) -> Dict[str, Optional[float]]:
    """
    Значения ALL_METRICS для кандидата в натуральных единицах (None — нет данных).
    Сера и WABT берутся на установившемся режиме: переходный процесс уже защищен вето
    «не хуже hold» (assess_limit), а максимум траектории одинаков у всех ходов из-за запаздывания.
    """
    ss = cand.steady_state or {}

    margin = _finite(cand.expected_margin)
    net_margin = None if margin is None else margin - float(risk_penalty)

    s_ss = _finite(ss.get("HT_S_PRODUCT"), cand.expected_sulfur)
    sulfur_ucb = None if s_ss is None else s_ss + stat_offset(SIGMA_S0_PPM, sulfur_age_hours, z)
    giveaway = None if s_ss is None else max(0.0, giveaway_boundary(sulfur_age_hours) - s_ss)

    t_in, t_out = _finite(ss.get("HT_T_IN")), _finite(ss.get("HT_T_OUT"))
    wabt = _finite(ss.get("HT_BED_MEAN"), (t_in + t_out) / 2.0 if t_in is not None and t_out is not None else None)

    w10 = _finite(cand.expected_w10)
    dp = _finite(ss.get("HT_DP_KPA"), cand.expected_dp_kpa, w10 * 98.0665 if w10 is not None else None)

    return {
        NET_MARGIN.key: net_margin,
        SULFUR_GIVEAWAY.key: giveaway,
        BED_TEMPERATURE.key: wabt,
        SULFUR_UCB.key: sulfur_ucb,
        REACTOR_DP.key: dp,
    }


def sulfur_measurement_age(tags: Optional[Mapping[str, Any]], lims_age_hours: float) -> float:
    """Возраст измерения серы как в QualityAgent: 0 при достоверном онлайн-анализаторе HT_Q21, иначе возраст ЛИМС."""
    q21 = _finite((tags or {}).get("HT_Q21"))
    if q21 is not None and q21 not in _CLAMPED_VALUES:
        return 0.0
    return max(0.0, float(lims_age_hours))


def analyze_pareto(
    candidates: Sequence[ControlCandidate],
    vetoed_ids: Iterable[str] = (),
    risk_penalties: Optional[Mapping[str, float]] = None,
    audit_reports: Optional[Sequence[SafetyAuditReport]] = None,
    sulfur_age_hours: float = 0.0,
    objectives: Sequence[ObjectiveSpec] = DEFAULT_OBJECTIVES,
    z: float = QUALITY_Z,
    tol: float = 0.0,
) -> ParetoAnalysis:
    """
    Парето-анализ такта управления:
    ветированные → vetoed (вне ранжирования); нет значения хотя бы одной цели → incomplete;
    допустимые → недоминируемая сортировка, фронт, ideal/nadir фронта.
    """
    unknown = [o.key for o in objectives if o.key not in {m.key for m in ALL_METRICS}]
    if not objectives or unknown:
        raise ValueError(f"Недопустимый набор целей Парето-анализа: {unknown or 'пустой'}")

    penalties = risk_penalties or {}
    vetoed_set = set(vetoed_ids)
    sigma = sulfur_sigma(sulfur_age_hours)
    s_limit = SULFUR_PRODUCT_MAX.hi or 10.0
    senses = [o.sense for o in objectives]

    reasons: Dict[str, List[str]] = {}
    for rep in audit_reports or []:
        if rep.is_vetoed and rep.violation_reason:
            reasons.setdefault(rep.candidate_id, []).append(rep.violation_reason)

    entries: List[Dict[str, Any]] = []
    ranked: List[int] = []  # позиции entries, участвующие в сортировке
    vectors: List[Tuple[float, ...]] = []
    seen: set = set()
    for cand in candidates:
        if cand.candidate_id in seen:
            continue
        seen.add(cand.candidate_id)

        values = candidate_metrics(cand, penalties.get(cand.candidate_id, 0.0), sulfur_age_hours, z)
        s_ss = _finite((cand.steady_state or {}).get("HT_S_PRODUCT"), cand.expected_sulfur)
        p_off = None if s_ss is None else round(_p_exceed(s_ss, s_limit, sigma), 6)

        if cand.candidate_id in vetoed_set:
            status: PointStatus = "vetoed"
        elif any(values[o.key] is None for o in objectives):
            status = "incomplete"
        else:
            status = "dominated"  # уточняется после сортировки
            ranked.append(len(entries))
            vectors.append(to_minimization([values[o.key] for o in objectives], senses))

        entries.append({
            "candidate_id": cand.candidate_id,
            "delta_u": dict(cand.delta_u),
            "is_hold": cand.is_hold,
            "status": status,
            "metrics": {k: (round(v, 4) if v is not None else None) for k, v in values.items()},
            "sulfur_forecast_ppm": None if s_ss is None else round(s_ss, 4),
            "p_offspec": p_off,
            "veto_reasons": reasons.get(cand.candidate_id, []) if status == "vetoed" else [],
        })

    fronts = non_dominated_sort(vectors, tol)
    for layer_no, layer in enumerate(fronts, start=1):
        for j in layer:
            entry = entries[ranked[j]]
            entry["rank"] = layer_no
            if layer_no == 1:
                entry["status"] = "pareto"
            else:
                entry["dominated_by"] = [
                    entries[ranked[i]]["candidate_id"] for i in range(len(vectors)) if dominates(vectors[i], vectors[j], tol)
                ]

    front = fronts[0] if fronts else []
    ideal: Dict[str, float] = {}
    nadir: Dict[str, float] = {}
    for k, obj in enumerate(objectives):
        if not front:
            break
        vals = [vectors[j][k] for j in front]
        best, worst = min(vals), max(vals)
        if obj.sense == "max":
            best, worst = -best, -worst
        ideal[obj.key] = round(best, 4)
        nadir[obj.key] = round(worst, 4)

    return ParetoAnalysis(
        objectives=list(objectives),
        metrics=list(ALL_METRICS),
        points=[ParetoPoint(**entry) for entry in entries],
        front_ids=[entries[ranked[j]]["candidate_id"] for j in front],
        ideal=ideal,
        nadir=nadir,
        sulfur_sigma_ppm=round(sigma, 4),
        sulfur_offset_ppm=round(z * sigma, 4),
        giveaway_boundary_ppm=round(giveaway_boundary(sulfur_age_hours), 4),
        sulfur_age_hours=round(float(sulfur_age_hours), 3),
    )


def trade_off_alternatives(analysis: ParetoAnalysis, reference_id: Optional[str]) -> List[ParetoAlternative]:
    """
    Ближайшие альтернативы на фронте для рекомендации (или hold, если рекомендации нет):
    по каждой оси отдельно — точка фронта, улучшающая ось и теряющая меньше всего маржи (без весов осей).
    """
    ref = analysis.point(reference_id)
    if ref is None or ref.status in ("vetoed", "incomplete"):
        return []
    front = [p for p in analysis.points if p.status == "pareto" and p.candidate_id != ref.candidate_id]
    ref_margin = ref.metrics.get(NET_MARGIN.key) or 0.0
    result: List[ParetoAlternative] = []

    def cheapest(pool: List[ParetoPoint]) -> Optional[ParetoPoint]:
        return max(pool, key=lambda p: (p.metrics.get(NET_MARGIN.key) or 0.0), default=None)

    if ref.p_offspec is not None:
        safer = cheapest([p for p in front if p.p_offspec is not None and p.p_offspec < ref.p_offspec - 1e-9])
        if safer is not None:
            result.append(ParetoAlternative(
                kind="safer_sulfur",
                candidate_id=safer.candidate_id,
                delta_margin_rub_h=round((safer.metrics.get(NET_MARGIN.key) or 0.0) - ref_margin, 2),
                delta_sulfur_ppm=round((safer.sulfur_forecast_ppm or 0.0) - (ref.sulfur_forecast_ppm or 0.0), 3),
                risk_from_pct=round(ref.p_offspec * 100.0, 3),
                risk_to_pct=round((safer.p_offspec or 0.0) * 100.0, 3),
            ))

    ref_wabt = ref.metrics.get(BED_TEMPERATURE.key)
    if ref_wabt is not None:
        gentler = cheapest([p for p in front if (p.metrics.get(BED_TEMPERATURE.key) or math.inf) < ref_wabt - 1e-9])
        if gentler is not None:
            result.append(ParetoAlternative(
                kind="gentler_catalyst",
                candidate_id=gentler.candidate_id,
                delta_margin_rub_h=round((gentler.metrics.get(NET_MARGIN.key) or 0.0) - ref_margin, 2),
                delta_wabt_c=round((gentler.metrics.get(BED_TEMPERATURE.key) or 0.0) - ref_wabt, 3),
            ))
    return result


# =============================================================================
# Объяснение для XAI
# =============================================================================

# node_pareto (узел LangGraph, Fan-In после аудиторов) удалён (аудит 2026-09-20): работал
# на легаси MasGraphState/vetoed_candidates/audit_reports и не был подключён к
# build_core_graph() — вызывался только из мёртвого MVP-графа. analyze_pareto() ниже
# по-прежнему живой (console/service.py::build_pareto как fallback-реконструкция).


def _fmt_point(point: ParetoPoint) -> str:
    m = point.metrics
    parts = []
    if m.get(NET_MARGIN.key) is not None:
        parts.append(f"{m[NET_MARGIN.key]:+,.0f} руб/ч")
    if point.sulfur_forecast_ppm is not None:
        risk = f", P(S>10)={point.p_offspec * 100:.2f}%" if point.p_offspec is not None else ""
        parts.append(f"Ŝ {point.sulfur_forecast_ppm:.2f} ppm{risk}")
    if m.get(SULFUR_GIVEAWAY.key):
        parts.append(f"переочистка {m[SULFUR_GIVEAWAY.key]:.2f} ppm")
    if m.get(BED_TEMPERATURE.key) is not None:
        parts.append(f"WABT {m[BED_TEMPERATURE.key]:.1f} °C")
    return " | ".join(parts)


def _fmt_alternative(alt: ParetoAlternative) -> str:
    if alt.kind == "safer_sulfur":
        return (
            f"безопаснее по сере — `{alt.candidate_id}`: {alt.delta_margin_rub_h:+,.0f} руб/ч за {alt.delta_sulfur_ppm:+.2f} ppm серы "
            f"(риск {alt.risk_from_pct:.2f}% → {alt.risk_to_pct:.2f}%)"
        )
    return f"бережнее к катализатору — `{alt.candidate_id}`: {alt.delta_margin_rub_h:+,.0f} руб/ч за {alt.delta_wabt_c:+.1f} °C WABT"


def format_pareto_summary(
    analysis: Optional[ParetoAnalysis],
    selected_id: Optional[str] = None,
    max_rows: int = 5,
) -> str:
    """Markdown-раздел XAI: фронт, положение рекомендации, цена компромисса и доминируемые альтернативы."""
    if analysis is None or not analysis.points:
        return "• Парето-анализ не выполнялся: кандидаты управления отсутствуют."

    arrows = {"max": "↑", "min": "↓"}
    goals = ", ".join(f"{arrows[o.sense]} {o.label} ({o.unit})" for o in analysis.objectives)
    lines = [
        f"• Цели: {goals}. Переочистка — прогноз серы ниже {analysis.giveaway_boundary_ppm:.2f} ppm; "
        f"запас вето по сере 2σ = {analysis.sulfur_offset_ppm:.2f} ppm.",
        f"• Допустимых кандидатов: {analysis.n_admissible} из {len(analysis.points)}; "
        f"на Парето-фронте: **{len(analysis.front_ids)}** (ветированные варианты в сравнение не допускаются).",
    ]
    if analysis.n_admissible == 0:
        lines.append("• Допустимых кандидатов нет: Парето-фронт пуст.")
        return "  \n".join(lines)

    sel = analysis.point(selected_id)
    if sel is not None and sel.status == "pareto":
        lines.append(
            f"• Рекомендация `{selected_id}` — **Парето-оптимальное (компромиссное) решение в допустимой зоне**: "
            "максимум маржи среди безопасных режимов, которые не доминируются другими."
        )
    elif sel is not None and sel.is_hold and sel.dominated_by:
        lines.append(
            f"• Текущий режим сохранен: доминирующие его варианты ({', '.join(f'`{d}`' for d in sel.dominated_by[:3])}) "
            "дают прирост ниже порога нечувствительности арбитража."
        )
    elif sel is not None and sel.dominated_by:
        lines.append(f"• ⚠️ Рекомендация `{selected_id}` доминируется: {', '.join(f'`{d}`' for d in sel.dominated_by)}.")

    reference = selected_id if sel is not None else next((p.candidate_id for p in analysis.points if p.is_hold), None)
    alternatives = trade_off_alternatives(analysis, reference)
    if alternatives:
        lines.append("• Цена компромисса (ближайшие альтернативы на фронте):")
        lines.extend(f"  - {_fmt_alternative(a)}" for a in alternatives)
    elif reference is not None:
        lines.append("• Альтернатив на фронте, безопаснее по сере или бережнее к катализатору, нет.")

    front_points = sorted(
        (p for p in analysis.points if p.status == "pareto"),
        key=lambda p: -(p.metrics.get(NET_MARGIN.key) or 0.0),
    )
    lines.append("• Точки фронта:")
    for p in front_points[:max_rows]:
        mark = " ⭐" if p.candidate_id == selected_id else ""
        lines.append(f"  - `{p.candidate_id}`{mark}: {_fmt_point(p)}")
    if len(front_points) > max_rows:
        lines.append(f"  - … ещё {len(front_points) - max_rows} точек фронта")

    for p in [p for p in analysis.points if p.status == "dominated"][:3]:
        dominators = ", ".join(f"`{d}`" for d in p.dominated_by[:3])
        lines.append(f"• `{p.candidate_id}` исключен как доминируемый (не лучше ни по одной цели): {dominators}.")
    # Два пробела в конце строки — жесткий перенос Markdown: пункты «•» не склеиваются в абзац
    return "  \n".join(lines)


# =============================================================================
# Визуализация (Plotly импортируется лениво: ядро алгоритма не зависит от графики)
# =============================================================================

STATUS_STYLE: Dict[str, Dict[str, Any]] = {
    "pareto": {"name": "Парето-фронт", "color": "#2E7D32", "symbol": "circle"},
    "dominated": {"name": "Доминируемые (допустимые)", "color": "#90A4AE", "symbol": "circle-open"},
    "vetoed": {"name": "Отклонены вето ПАЗ/ГОСТ", "color": "#C62828", "symbol": "x"},
}
HOLD_COLOR = "#263238"
SELECTED_COLOR = "#F9A825"
ALTERNATIVE_COLOR = "#1565C0"


def _axis_title(spec: ObjectiveSpec) -> str:
    return f"{'↑' if spec.sense == 'max' else '↓'} {spec.label}, {spec.unit}"


def _hover(point: ParetoPoint, analysis: ParetoAnalysis) -> str:
    du = ", ".join(f"{k} {v:+g}" for k, v in point.delta_u.items()) or "hold (без изменений)"
    rows = [f"<b>{point.candidate_id}</b>", f"Δu: {du}", f"Статус: {STATUS_STYLE.get(point.status, {}).get('name', point.status)}"]
    if point.sulfur_forecast_ppm is not None:
        rows.append(f"Прогноз серы Ŝ: {point.sulfur_forecast_ppm:.2f} ppm")
    for spec in analysis.metrics:
        val = point.metrics.get(spec.key)
        if val is not None:
            rows.append(f"{spec.label}: {val:,.2f} {spec.unit}")
    if point.p_offspec is not None:
        rows.append(f"P(S > 10 ppm): {point.p_offspec * 100:.2f}%")
    if point.dominated_by:
        rows.append("Доминируется: " + ", ".join(point.dominated_by[:4]))
    if point.veto_reasons:
        rows.append("Вето: " + "; ".join(point.veto_reasons)[:180])
    return "<br>".join(rows)


def _plottable(analysis: ParetoAnalysis, keys: Sequence[str]) -> List[ParetoPoint]:
    return [p for p in analysis.points if p.status != "incomplete" and all(p.metrics.get(k) is not None for k in keys)]


def _highlights(analysis: ParetoAnalysis, points: Sequence[ParetoPoint], selected_id: Optional[str]) -> List[Tuple[ParetoPoint, str, str]]:
    """Выделенные точки: (точка, подпись, цвет) — hold, альтернативы на фронте, рекомендация арбитража."""
    by_id = {p.candidate_id: p for p in points}
    hold_id = next((p.candidate_id for p in points if p.is_hold), None)
    marks: List[Tuple[Optional[str], str, str]] = [(hold_id, "Текущий режим (hold)", HOLD_COLOR)]
    names = {"safer_sulfur": "Альтернатива: безопаснее по сере", "gentler_catalyst": "Альтернатива: бережнее к катализатору"}
    for alt in trade_off_alternatives(analysis, selected_id):
        marks.append((alt.candidate_id, names[alt.kind], ALTERNATIVE_COLOR))
    marks.append((selected_id, "Рекомендация арбитража", SELECTED_COLOR))
    return [(by_id[cid], name, color) for cid, name, color in marks if cid in by_id]


def build_pareto_3d_figure(
    analysis: ParetoAnalysis,
    selected_id: Optional[str] = None,
    axes: Tuple[str, str, str] = (SULFUR_GIVEAWAY.key, BED_TEMPERATURE.key, NET_MARGIN.key),
):
    """3D-диаграмма компромиссов «качество ↔ износ катализатора ↔ маржа» с точкой арбитража."""
    import plotly.graph_objects as go

    specs = [analysis.metric(k) for k in axes]
    points = _plottable(analysis, axes)
    fig = go.Figure()

    def coords(group: Sequence[ParetoPoint]) -> Dict[str, List[float]]:
        return {name: [p.metrics[k] for p in group] for name, k in zip(("x", "y", "z"), axes)}

    for status, style in STATUS_STYLE.items():
        group = [p for p in points if p.status == status]
        if group:
            fig.add_trace(go.Scatter3d(
                **coords(group), mode="markers", name=style["name"],
                marker={"size": 6, "color": style["color"], "symbol": style["symbol"], "opacity": 0.9},
                text=[_hover(p, analysis) for p in group], hoverinfo="text",
            ))

    symbols = {HOLD_COLOR: ("diamond-open", 9), ALTERNATIVE_COLOR: ("circle-open", 12), SELECTED_COLOR: ("diamond", 11)}
    for p, name, color in _highlights(analysis, points, selected_id):
        symbol, size = symbols[color]
        fig.add_trace(go.Scatter3d(
            **coords([p]), mode="markers", name=name,
            marker={"size": size, "color": color, "symbol": symbol, "line": {"width": 2, "color": color}},
            text=[_hover(p, analysis)], hoverinfo="text",
        ))

    fig.update_layout(
        title="Парето-фронт допустимых режимов",
        scene={
            axis: {"title": {"text": _axis_title(spec), "font": {"size": 11}}}
            for axis, spec in zip(("xaxis", "yaxis", "zaxis"), specs)
        },
        legend={"orientation": "h", "y": -0.05},
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )
    return fig


def build_pareto_2d_figure(
    analysis: ParetoAnalysis,
    selected_id: Optional[str] = None,
    x: str = SULFUR_GIVEAWAY.key,
    y: str = NET_MARGIN.key,
):
    """
    2D-проекция: цвет точки — статус во всем пространстве целей; пунктир — недоминируемая граница
    самой проекции (по двум выбранным метрикам); вертикаль — норматив / предел оси X.
    """
    import plotly.graph_objects as go

    sx, sy = analysis.metric(x), analysis.metric(y)
    points = _plottable(analysis, (x, y))
    fig = go.Figure()

    admissible = [p for p in points if p.status in ("pareto", "dominated")]
    if admissible:
        vecs = [to_minimization([p.metrics[x], p.metrics[y]], [sx.sense, sy.sense]) for p in admissible]
        border = sorted((admissible[i] for i in pareto_front_indices(vecs)), key=lambda p: p.metrics[x])
        fig.add_trace(go.Scatter(
            x=[p.metrics[x] for p in border], y=[p.metrics[y] for p in border],
            mode="lines", name="Граница проекции (2 метрики)",
            line={"color": STATUS_STYLE["pareto"]["color"], "dash": "dot"}, hoverinfo="skip",
        ))

    for status, style in STATUS_STYLE.items():
        group = [p for p in points if p.status == status]
        if group:
            fig.add_trace(go.Scatter(
                x=[p.metrics[x] for p in group], y=[p.metrics[y] for p in group], mode="markers", name=style["name"],
                marker={"size": 10, "color": style["color"], "symbol": style["symbol"], "line": {"width": 1.5, "color": style["color"]}},
                text=[_hover(p, analysis) for p in group], hoverinfo="text",
            ))

    symbols = {HOLD_COLOR: ("diamond-open", 14), ALTERNATIVE_COLOR: ("circle-open", 20), SELECTED_COLOR: ("star", 20)}
    for p, name, color in _highlights(analysis, points, selected_id):
        symbol, size = symbols[color]
        fig.add_trace(go.Scatter(
            x=[p.metrics[x]], y=[p.metrics[y]], mode="markers", name=name,
            marker={"size": size, "color": color, "symbol": symbol, "line": {"width": 2, "color": color}},
            text=[_hover(p, analysis)], hoverinfo="text",
        ))

    if sx.limit is not None:
        fig.add_vline(
            x=sx.limit, line_dash="dash", line_color=STATUS_STYLE["vetoed"]["color"],
            annotation_text=f"предел {sx.limit:g} {sx.unit}", annotation_position="top left",
        )

    fig.update_layout(
        title=f"Компромисс: {sx.label} ↔ {sy.label}",
        xaxis_title=_axis_title(sx),
        yaxis_title=_axis_title(sy),
        legend={"orientation": "h", "y": -0.2},
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
    )
    return fig


def build_parallel_coordinates_figure(analysis: ParetoAnalysis, selected_id: Optional[str] = None):
    """Параллельные координаты по всем метрикам: цвет — вето / доминируемый / фронт / рекомендация."""
    import plotly.graph_objects as go

    keys = [m.key for m in analysis.metrics]
    points = _plottable(analysis, keys)
    code = {"vetoed": 0, "dominated": 1, "pareto": 2}
    colors = [3 if p.candidate_id == selected_id else code[p.status] for p in points]
    objective_keys = {o.key for o in analysis.objectives}

    colorscale = [
        [0.0, STATUS_STYLE["vetoed"]["color"]], [0.25, STATUS_STYLE["vetoed"]["color"]],
        [0.25, STATUS_STYLE["dominated"]["color"]], [0.5, STATUS_STYLE["dominated"]["color"]],
        [0.5, STATUS_STYLE["pareto"]["color"]], [0.75, STATUS_STYLE["pareto"]["color"]],
        [0.75, SELECTED_COLOR], [1.0, SELECTED_COLOR],
    ]
    dims = [
        {
            "label": _axis_title(spec) if spec.key in objective_keys else f"{spec.label}, {spec.unit} (контроль)",
            "values": [p.metrics[spec.key] for p in points],
        }
        for spec in analysis.metrics
    ]
    fig = go.Figure(go.Parcoords(
        line={"color": colors, "colorscale": colorscale, "cmin": -0.5, "cmax": 3.5},
        dimensions=dims,
    ))
    fig.update_layout(
        title="Кандидаты по всем метрикам: красный — вето, серый — доминируемые, зеленый — фронт, желтый — рекомендация",
        margin={"l": 60, "r": 60, "t": 80, "b": 20},
    )
    return fig
