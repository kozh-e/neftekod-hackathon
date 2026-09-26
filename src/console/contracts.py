"""Pydantic v2 frozen контракты данных для пульта старшего оператора.

Соответствует спецификации agents/console_tz/01_CONTRACT.md.
Все контракты неизменяемы (frozen=True, extra="forbid").
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field

from src.agents.contracts import DecisionStatus


class Frozen(BaseModel):
    """Базовая неизменяемая модель с запретом лишних полей."""
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


# Управляющие воздействия (MV)
SP = Literal["HT_FEED_SP", "HT_TIN_SP", "HT_P_SP", "HT_GOR_SP", "AVT_T55_SP"]

# Причины автоматического выхода из режима АВТОМАТ
AutoExitCode = Literal[
    "REFUSAL_DATA",
    "RECOVERY_ADVISORY",
    "NEAR_LIMIT",
    "LOW_CONFIDENCE",
    "LIMS_STALE",
    "OPERATOR",
    "KERNEL_REJECT",
]


class ClockInfo(Frozen):
    """Модельные и реальные часы пульта."""
    now: str  # ISO 8601 в UTC, модельное «СЕЙЧАС»
    tick: int
    tick_minutes: int = 10
    seconds_per_tick: float  # реальное время на такт; 0 = пауза
    next_tick_in_s: Optional[float] = None


class LastAutoExit(Frozen):
    """Сведения о последнем выходе из режима АВТОМАТ."""
    at: str
    reason_code: AutoExitCode
    text: str


class ModeInfo(Frozen):
    """Информация о текущем режиме управления пультом."""
    current: Literal["ADVISORY", "AUTO"]
    auto_available: bool
    auto_unavailable_reason: Optional[str] = None  # человекочитаемо, по-русски
    auto_since: Optional[str] = None
    last_auto_exit: Optional[LastAutoExit] = None


class Alarm(Frozen):
    """Тревога T0/T1 с квитированием."""
    id: str
    tier: Literal["T0", "T1"]
    tag: str
    text: str
    value: float
    limit: float
    at: str
    acknowledged: bool


class Banner(Frozen):
    """Информационный или аварийный баннер под статус-баром."""
    id: str
    kind: Literal["AUTO_EXIT", "REFUSAL"]
    title: str
    text: str
    at: str
    ack_required: bool


class Point(Frozen):
    """Точка временного ряда с оценкой качества данных."""
    t: str
    v: Optional[float] = None
    quality: Literal["GOOD", "SUSPECT", "BAD", "MISSING"]


class Limit(Frozen):
    """Граница ограничения из реестра."""
    value: float
    sense: Literal["max", "min"]
    label: str
    tier: Literal["T1", "T2"]
    source: str  # "registry.py:<spec id>"


class Chip(Frozen):
    """Индикатор состояния параметра на панели."""
    text: str
    severity: Literal["ok", "warn", "alarm", "nodata"]


class CvSeries(Frozen):
    """Временной ряд регулируемой переменной (CV)."""
    key: Literal["sulfur", "flash", "dp"]
    tag: str  # "HT_Q21" | "HT_FLASH" | "HT_P8"
    title: str  # "СЕРА"
    unit: str
    limit: Limit
    history: List[Point]  # 12 ч назад … now, шаг 10 мин, 73 точки; v=null при отсутствии
    chip: Chip
    note: Optional[str] = None  # «без изменений пробьёт 10 ppm через 1 ч 50 мин»
    y_range: List[float]  # [min, max] рекомендованный диапазон оси


class Corridor(Frozen):
    """Коридор допустимого шага и диапазона MV из реестра."""
    max_step_per_tick: Optional[float] = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    source: Optional[str] = None


class MvLine(Frozen):
    """Линия технологического предела на мини-графике MV."""
    value: float
    kind: Literal["T0", "T1", "warn"]
    label: str


class MvSeries(Frozen):
    """Временной ряд управляющей переменной (MV)."""
    sp: SP
    tag: str
    title: str
    unit: str
    decimals: int
    sp_history: List[Point]
    pv_history: List[Point]
    corridor: Corridor
    lines: List[MvLine]
    frozen: bool


class Band(Frozen):
    """Квантили прогноза в точке времени t."""
    t: str
    p10: Optional[float] = None
    p50: float
    p90: Optional[float] = None


class Risk(Frozen):
    """Оценка риска нарушения ограничения."""
    cv: Literal["sulfur", "flash", "dp"]
    probability: float
    first_breach_at: Optional[str] = None
    limit: float


class Trajectory(Frozen):
    """Траектория динамического прогноза процессов комплекса."""
    kind: Literal["hold", "recommendation", "alternative", "operator", "auto_plan"]
    u_target: Dict[str, float]  # абсолютные уставки
    cv: Dict[str, List[Band]]  # "sulfur", "flash", "dp" -> 25 точек
    mv_plan: Dict[str, List[Point]]  # ступеньки будущих уставок
    risk: List[Risk]
    sigma_source: Optional[str] = None


class Change(Frozen):
    """Предлагаемое или примененное изменение уставки."""
    sp: SP
    tag: str
    title: str
    unit: str
    current: float
    target: float
    delta: float
    decimals: int


class KernelCheckDTO(Frozen):
    """Проверка независимого ядра безопасности."""
    name: str
    passed: bool
    detail: str


class LimitedStep(Frozen):
    """Срез шага уставки ядром или коридором."""
    sp: str
    requested: float
    allowed: float
    reason: str


class KernelInfo(Frozen):
    """Сводный вердикт проверок ядра безопасности."""
    passed: bool
    checks: List[KernelCheckDTO]
    limited: List[LimitedStep] = Field(default_factory=list)


class Effect(Frozen):
    """Ожидаемый технологический и экономический эффект."""
    sulfur_4h: Optional[float] = None
    flash_margin_c: Optional[float] = None
    margin_delta_rub_h: Optional[float] = None


class AgentVote(Frozen):
    """Позиция специализированного агента в переговорах."""
    agent: Literal["optimization", "reliability", "quality", "supply", "kernel"]
    stance: Literal["for", "against", "veto", "limit"]
    text: str


class Alternative(Frozen):
    """Парето-альтернатива к базовому решению."""
    choice: Literal["max_margin", "balanced", "max_safety"]
    label: Literal["Макс. маржа", "Сбалансировано", "Макс. запас"]
    available: bool
    changes: List[Change] = Field(default_factory=list)
    effect: Effect
    trajectory: Optional[Trajectory] = None


class ParetoObjective(Frozen):
    """Метрика многокритериального сравнения кандидатов (ось Парето-фронта)."""
    key: str
    label: str
    unit: str
    sense: Literal["max", "min"]
    limit: Optional[float] = None


class ParetoPointDTO(Frozen):
    """Кандидат управления в пространстве метрик для отображения на пульте."""
    candidate_id: str
    is_hold: bool
    is_recommendation: bool
    status: Literal["pareto", "dominated", "vetoed", "incomplete"]
    metrics: Dict[str, Optional[float]]
    delta_u: Dict[str, float] = Field(default_factory=dict)
    veto_reasons: List[str] = Field(default_factory=list)
    dominated_by: List[str] = Field(default_factory=list)


class ParetoFrontDTO(Frozen):
    """Парето-фронт допустимых режимов такта (Критерий 4, п. 6.5 ТЗ)."""
    objectives: List[ParetoObjective]
    points: List[ParetoPointDTO]
    ideal: Dict[str, float] = Field(default_factory=dict)
    nadir: Dict[str, float] = Field(default_factory=dict)
    hold_id: Optional[str] = None
    selected_id: Optional[str] = None


class NegotiationEventDTO(Frozen):
    """Событие протокола переговоров МАС (полный, некупированный журнал для XAI)."""
    round: int
    kind: str
    actor: str
    candidate: Optional[str] = None
    detail: str = ""


class XaiInfo(Frozen):
    """Расширенное объяснение решения такта: полный лог переговоров."""
    cycle_id: str
    events: List[NegotiationEventDTO]


class Refusal(Frozen):
    """Сведения об отказе системы от выдачи рекомендаций."""
    reason_code: str
    text: str
    checklist: List[str]
    auto_resume_condition: str


class Recommendation(Frozen):
    """Рекомендация мультиагентной системы."""
    cycle_id: str
    status: DecisionStatus
    created_at: str
    valid_until: str
    narrative: str
    changes: List[Change]
    effect: Effect
    kernel: KernelInfo
    agents: List[AgentVote]
    trajectory: Optional[Trajectory] = None
    alternatives: List[Alternative] = Field(default_factory=list)
    refusal: Optional[Refusal] = None


class AutoNextStep(Frozen):
    """Следующий шаг, запланированный автоматом."""
    at: str
    in_s: float
    changes: List[Change]
    narrative: str


class AutoPlanStep(Frozen):
    """Шаг многошагового плана автомата."""
    at: str
    changes: List[Change]


class AutoInfo(Frozen):
    """Информация для карточки режима АВТОМАТ."""
    next_step: Optional[AutoNextStep] = None
    plan: List[AutoPlanStep] = Field(default_factory=list)
    corridor: Dict[str, Corridor]
    skipped_next: bool = False


class FeedItem(Frozen):
    """Запись в ленте действий и событий."""
    id: str
    t: str
    kind: Literal["ok", "shield", "veto", "warn", "info"]
    text: str
    source: str


class Scenario(Frozen):
    """Текущий сценарий симулятора."""
    id: Optional[str] = None
    title: Optional[str] = None


class Margin(Frozen):
    """Экономические показатели маржи."""
    value_rub_h: Optional[float] = None
    delta_rub_h: Optional[float] = None
    delta_label: Optional[str] = None


class Series(Frozen):
    """Набор временных рядов CV и MV."""
    cv: List[CvSeries]
    mv: List[MvSeries]


class ConfidenceInfo(Frozen):
    """Индекс уверенности во входных данных такта (зеркало data_guard.py:379-385)."""
    score: float  # 0..1, data_guard.confidence["score"]
    level: Literal["HIGH", "MEDIUM", "LOW"]
    n_filled_critical: int
    q21_unavailable: bool
    lims_age_hours: float


class SensorSeries(Frozen):
    """Временной ряд справочного датчика двойника (не CV/MV, без обязательного лимита)."""
    key: str
    tag: str
    title: str
    unit: str
    history: List[Point]
    limit: Optional[Limit] = None


class ConsoleState(Frozen):
    """Полный снимок состояния пульта оператора."""
    schema_version: Literal["1.0"] = "1.0"
    session_id: str
    scenario: Scenario
    clock: ClockInfo
    mode: ModeInfo
    margin: Margin
    alarms: List[Alarm] = Field(default_factory=list)
    banner: Optional[Banner] = None
    series: Series
    hold: Trajectory
    recommendation: Optional[Recommendation] = None
    auto: Optional[AutoInfo] = None
    feed: List[FeedItem] = Field(default_factory=list)
    confidence: Optional[ConfidenceInfo] = None
    sensors: List[SensorSeries] = Field(default_factory=list)
    fixture_: Optional[bool] = Field(default=None, alias="_fixture")


class PreviewRequest(Frozen):
    """Запрос расчета последствий операторской правки «что если»."""
    session_id: str
    cycle_id: Optional[str] = None
    u_target: Dict[str, float]


class CorridorViolation(Frozen):
    """Нарушение коридора допустимого шага."""
    sp: str
    requested_step: float
    max_step: Optional[float] = None
    text: str


class PreviewResult(Frozen):
    """Результат расчета preview «что если»."""
    trajectory: Trajectory
    kernel: KernelInfo
    corridor_ok: bool
    corridor_violations: List[CorridorViolation] = Field(default_factory=list)
    effect: Effect
    can_commit: bool
    can_commit_with_ack: bool = False
    blocking_reason: Optional[str] = None
    quality_warning: Optional[str] = None
    compute_ms: float


class CommitRequest(Frozen):
    """Запрос на применение уставок."""
    session_id: str
    cycle_id: Optional[str] = None
    u_target: Dict[str, float]
    source: Literal["recommendation", "alternative", "operator_edit"]
    choice: Optional[Literal["max_margin", "balanced", "max_safety"]] = None


class CommitResult(Frozen):
    """Результат применения уставок в двойник."""
    ok: Literal[True] = True
    applied: List[Change]
    at: str
    feed_item: FeedItem
    quality_warning: Optional[str] = None


class ConsoleErrorDetail(Frozen):
    """Сведения об ошибке 409 при проверке ядром или коридором."""
    code: str
    text: str
    checks: List[KernelCheckDTO] = Field(default_factory=list)


# =============================================================================
# Ф3. Песочница блендинга (agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md §5)
# DTO ниже описывают только форму данных для ролей B2/F2 — сам расчёт остаётся
# в src/console/blending.py (владение роли B2), здесь не создаётся.
# =============================================================================

class BlendComponentDTO(Frozen):
    """Танк компонента блендинга (зеркало ComponentTank из src/agents/tanks.py)."""
    id: str
    label: str
    stock_t: float
    props: Dict[str, float] = Field(default_factory=dict)


class BlendSpecMetricDTO(Frozen):
    """Строка сертификата: показатель качества смеси, значение по рецепту и ГОСТ-лимит.

    Лимиты берутся из src/agents/limits.py, если там есть явная константа спецификации
    ГОСТ 32511-2013 (не из буферных ASSUMPTION-порогов LP-задачи BLEND_*, которые уже
    заложены в build_blend_problem/node_blending_agent с запасом). Источник не найден —
    limit/sense/source остаются None (см. agents/console_tz/QUESTIONS.md).
    """
    key: Literal["sulfur", "t95", "cetane", "cfpp", "flash"]
    label: str
    unit: str
    value: Optional[float] = None  # ожидаемое по рецепту, BlendingResult.expected_*
    limit: Optional[float] = None  # порог спецификации; None, если источник не найден
    sense: Optional[Literal["max", "min"]] = None
    source: Optional[str] = None  # "limits.py:<CONST>"; None, если источник не найден


class BlendCertDTO(Frozen):
    """Сертификат смеси: показатели по рецепту в сопоставлении с лимитами (certify_blend)."""
    status: Literal["FEASIBLE", "INFEASIBLE_ELASTIC", "FAILED"]
    cost_per_ton: Optional[float] = None
    metrics: List[BlendSpecMetricDTO] = Field(default_factory=list)
    error_message: Optional[str] = None


class BlendRecipeDTO(Frozen):
    """Рассчитанная рецептура блендинга (зеркало BlendingResult, src/agents/blending.py)."""
    success: bool
    status: Literal["FEASIBLE", "INFEASIBLE_ELASTIC", "FAILED"] = "FEASIBLE"
    v_diesel: float = 0.0
    v_kerosene: float = 0.0
    v_ddp_ppm: float = 0.0
    expected_cfpp: float = 0.0
    expected_flash: float = 0.0
    expected_sulfur: float = 0.0
    cost_per_ton: float = 0.0
    fbi_blend: float = 0.0
    shares: Dict[str, float] = Field(default_factory=dict)
    additive_doses_kg_t: Dict[str, float] = Field(default_factory=dict)
    expected_density: Optional[float] = None
    expected_t95: Optional[float] = None
    expected_e360: Optional[float] = None
    expected_cetane: Optional[float] = None
    binding_constraints: List[str] = Field(default_factory=list)
    blocked_components: List[str] = Field(default_factory=list)
    elastic_violations: Dict[str, float] = Field(default_factory=dict)
    error_message: Optional[str] = None


class BlendingStateDTO(Frozen):
    """Текущее пересчитанное состояние песочницы блендинга (рецепт + танки + сертификат)."""
    recipe: BlendRecipeDTO
    components: List[BlendComponentDTO] = Field(default_factory=list)
    cert: BlendCertDTO


class BlendingPreviewDTO(Frozen):
    """Результат предпросмотра «что если» в песочнице блендинга (ничего не применяет)."""
    recipe: BlendRecipeDTO
    components: List[BlendComponentDTO] = Field(default_factory=list)
    cert: BlendCertDTO
    feasible: bool
    error_message: Optional[str] = None


class BlendingPreviewRequest(Frozen):
    """Запрос предпросмотра «что если» песочницы блендинга (роль B2, ничего не применяет).

    `tank_overrides` — правки поверх копии резервуаров, ключ = id танка (`GODT`/`Kerosene`/`Gasoil`),
    значение — словарь свойств (`stock_t` и/или показатели качества из `ComponentTank.props`).
    `price_overrides` — правки цен компонентов/присадок (ключи `EconomicsParams`/`BlendEconomics`,
    например `price_godt`, `price_kerosene`, `price_gasoil`, `additive_a_price_rub_t`).
    """
    session_id: str
    tank_overrides: Dict[str, Any] = Field(default_factory=dict)
    price_overrides: Dict[str, float] = Field(default_factory=dict)


# =============================================================================
# Ф4. Цены рынка/тарифов во вкладке «Константы» (03_STREAMLIT_MIGRATION_PLAN.md §6)
# =============================================================================

class EconomicsOverrideDTO(Frozen):
    """Текущие активные значения цен рынка/тарифов (зеркало EconomicsParams, src/twin/params.py).

    is_override=True, если оператор менял значение через POST /api/console/economics;
    False — значение читается напрямую из config/twin_params.json.
    """
    price_godt: float
    price_straight_run: float
    price_crude_oil: float
    price_kerosene: float
    price_gasoil: float
    is_override: bool


class EconomicsUpdateRequest(Frozen):
    """Запрос оператора на правку цен рынка/тарифов live-сессии (POST /api/console/economics).

    `prices` — частичный словарь ключей `EconomicsParams` (src/twin/params.py), например
    `price_godt`/`price_straight_run`/`price_crude_oil`/`price_kerosene`/`price_gasoil`.
    Смёрджится поверх `session.economics_override` (новые значения поверх старых), не заменяя
    его целиком. Неизвестный ключ (не атрибут `EconomicsParams`) — HTTP 422, см.
    `src/console/api.py::post_economics`.
    """
    session_id: str
    prices: Dict[str, float]
