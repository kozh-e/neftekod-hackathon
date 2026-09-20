"""Pydantic v2 frozen контракты данных для ядра мультиагентной системы.

Соответствует спецификации §4.1–§4.7 implementation_plan_v3.md.
Все контракты неизменяемы (frozen=True, extra="forbid").
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    """Базовая неизменяемая модель с запретом лишних полей."""
    model_config = ConfigDict(frozen=True, extra="forbid")


# =============================================================================
# 4.1. Провенанс, измерения, качество данных
# =============================================================================

class ProvenanceKind(StrEnum):
    NORM = "NORM"              # официальное ТЗ, PDF установки, ГОСТ
    REGISTRY = "REGISTRY"      # new_data: реестр тегов, формулы ВАК
    DATA = "DATA"              # оценка по архивам (train <= 2025-06-30)
    ASSUMPTION = "ASSUMPTION"  # инженерное допущение
    POLICY = "POLICY"          # решение владельца политики (технолог)


class Provenance(Frozen):
    kind: ProvenanceKind
    ref: str                   # "initial_data/ТЗ_нефтекод.docx §4", "agents/ASSUMPTIONS.md §2.1"
    note: str = ""


class SignalSource(StrEnum):
    DCS = "DCS"
    PAK = "PAK"
    LIMS = "LIMS"
    VAK = "VAK"
    MODEL = "MODEL"


class SignalQuality(StrEnum):
    GOOD = "GOOD"
    SUSPECT = "SUSPECT"        # залипание, скачок, расхождение с моделью > k·sigma
    BAD = "BAD"                # NaN, Inf, клампинг 307/313, вне физического диапазона
    MISSING = "MISSING"


class Measurement(Frozen):
    tag: str
    value: float | None
    unit: str
    source: SignalSource
    sampled_at: datetime
    available_at: datetime | None = None   # ЛИМС: момент появления результата
    quality: SignalQuality
    flags: tuple[str, ...] = ()            # CLAMPED, FROZEN, RATE, RANGE, NAN, MODEL_MISMATCH


class AutomationLevel(StrEnum):
    FULL = "FULL"
    CAUTIOUS = "CAUTIOUS"
    CORRECTIVE_ONLY = "CORRECTIVE_ONLY"
    REFUSAL_DATA = "REFUSAL_DATA"


class DataAssessment(Frozen):
    measurements: dict[str, Measurement]
    automation_level: AutomationLevel
    reasons: tuple[str, ...]
    blocked_mvs: frozenset[str] = frozenset()   # MV, ходы по которым запрещены из-за данных
    unknown_specs: frozenset[str] = frozenset() # ограничения без достоверных данных


# =============================================================================
# 4.2. Оценка состояния и неопределенность
# =============================================================================

QualityProp = Literal["S", "FLASH", "E360", "T95", "CN", "CFPP", "D15"]


class QualityEstimate(Frozen):
    stream: Literal["GODT", "PRODUCT"]
    prop: QualityProp
    value: float                           # в линейных единицах (ppm, °C, % об., кг/м³)
    domain: Literal["linear", "log"]       # домен, в котором задана sigma
    sigma_meas: float                      # повторяемость источника
    sigma_calib: float                     # растет с возрастом последней пробы ЛИМС
    calib_age_h: float
    anchor: Literal["PAK+bias", "MODEL+bias", "LIMS"]
    last_lims_sampled_at: datetime | None = None
    provenance: Provenance


class PlantEstimate(Frozen):
    t: datetime
    u_actual: dict[str, float]             # сверенные положения MV
    manual_changes: tuple[str, ...] = ()   # MV, измененные вне рекомендаций
    disturbances: dict[str, float]         # AVT_F30, AVT_F32, AVT_F31, AVT_P52, HT_F14, ...
    measured_constraints: dict[str, float] # измеренные T55, DP, T_out, GOR, F31, P52
    quality: dict[str, QualityEstimate]    # ключ "GODT.S", "GODT.FLASH", ...
    factors: dict[str, float]              # "HT_DP_KPA": коэффициент засорения, "GODT.S": множитель к модели
    twin_steps_advanced: int               # шаги двойника с прошлого такта (по меткам времени)

    def value_of(self, quantity: str) -> float | None:
        """Вспомогательный метод получения текущего значения величины (для сертификации)."""
        if quantity in self.measured_constraints:
            return self.measured_constraints[quantity]
        if quantity in self.u_actual:
            return self.u_actual[quantity]
        if quantity in self.disturbances:
            return self.disturbances[quantity]
        if quantity in self.quality:
            return self.quality[quantity].value
        return None


# =============================================================================
# 4.3. Ограничения и сертификаты
# =============================================================================

class Tier(IntEnum):
    T0_BOUNDS = 0          # границы и скорость хода MV — модельные ограничения ТЗ
    T1_EQUIPMENT = 1
    T2_QUALITY = 2
    T3_OPERATIONAL = 3


# Alias for semantic clarity
ConstraintTier = Tier


class ConstraintSpec(Frozen):
    key: str                               # "RX.DP_MAX", "GODT.S_MAX", "PRODUCT.E360_MIN"
    label: str
    owner: Literal["reliability", "quality", "blending", "supply", "kernel"]
    tier: Tier
    quantity: str                          # выход прогноза: "HT_DP_KPA", "GODT.S", "PRODUCT.E360", "BUFFER.INVENTORY_T"
    sense: Literal["max", "min"]
    limit: float
    unit: str
    scale: float                           # нормировка запаса для функции качества
    domain: Literal["linear", "log"] = "linear"
    chance: bool = False                   # mean +/- z(alpha)·sigma
    transient: Literal["ss", "not_worse_than_hold", "strict"] = "ss"
    # Домен оценки Prediction.value(): "steady" — только установившийся режим (steady_state);
    # "extrema" — худшая точка траектории (trajectory_extrema). Раньше это решалось тем же
    # полем transient ("strict" => extrema) — разведено (аудит 2026-09-20, RX.DP_MAX), т.к.
    # transient также определяет отдельную семантику лазейки "not_worse_than_hold" в
    # _apply_transient_rule, и совмещение двух смыслов в одном поле не позволяло независимо
    # включить обе семантики для одной спецификации.
    chance_domain: Literal["steady", "extrema"] = "steady"
    depends_on: frozenset[str] = frozenset() # MV, от которых величина зависит структурно
    requires_measurement: str | None = None  # тег-предусловие (AVT_F31, AVT_P52)
    trip_ref: float | None = None          # уставка блокировки для объяснения запаса, если известна
    provenance: Provenance


class ConstraintStatus(StrEnum):
    SATISFIED = "SATISFIED"
    ACTIVE = "ACTIVE"                      # 0 <= запас < epsilon_active
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"                    # данных нет, а ограничение применимо -> fail-closed
    NOT_APPLICABLE = "NOT_APPLICABLE"      # ход не затрагивает depends_on


class ConstraintEvaluation(Frozen):
    spec_key: str
    tier: Tier
    status: ConstraintStatus
    mean: float | None = None
    sigma: float | None = None
    z: float | None = None
    effective: float | None = None         # mean +/- z·sigma в домене ограничения
    slack: float | None = None             # >= 0 — выполнено; в единицах scale
    slack_hold: float | None = None
    gradient: dict[str, float] = Field(default_factory=dict)  # d(slack)/d(Delta u_j)
    worst_step: int | None = None          # шаг траектории с минимальным запасом
    scenario_pass_fraction: float | None = None               # ансамбль печи (ADR-19)
    reason: str | None = None


class RepairProposal(Frozen):
    by: str                                # агент или "coordinator"
    target: str                            # сигнатура исходного кандидата
    delta_u: dict[str, float]
    active_specs: tuple[str, ...]
    predicted_slacks: dict[str, float]     # линейный прогноз запасов после ремонта
    distance: float                        # ||W·(Delta u' - Delta u)||
    feasible_linear: bool                  # False -> «наименьшее нарушение»


class ConstraintCertificate(Frozen):
    agent: Literal["reliability", "quality", "supply", "blending"]
    candidate: str
    round: int
    evaluations: tuple[ConstraintEvaluation, ...]
    verdict: Literal["ADMISSIBLE", "VIOLATED", "UNKNOWN"]
    violation_by_tier: dict[int, float]    # V_t = Sigma max(0, -slack_i) по ярусу
    repair: RepairProposal | None = None
    requirements: tuple[str, ...] = ()     # требования агента для карточки
    forecasts: dict[str, QualityEstimate] = Field(default_factory=dict)  # quality -> blending
    compute_ms: float = 0.0


# =============================================================================
# 4.4. Кандидаты, прогнозы, переговоры
# =============================================================================

class CandidateOrigin(StrEnum):
    HOLD = "HOLD"
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"
    NEAREST_FEASIBLE = "NEAREST_FEASIBLE"
    REPAIR = "REPAIR"
    REFINE = "REFINE"


class Candidate(Frozen):
    signature: str                         # sha1 от отсортированных Delta u, округленных до step/100
    delta_u: dict[str, float]
    origin: CandidateOrigin
    proposed_by: str
    parent: str | None = None
    round: int = 0


class Prediction(Frozen):
    candidate: str
    horizon_steps: int
    steady_state: dict[str, float]
    trajectory_extrema: dict[str, tuple[float, float, int]]   # (min, max, шаг худшего значения)
    trajectory: dict[str, tuple[float, ...]] | None = None    # хранится для hold и 5 лучших

    def value(self, quantity: str, domain: str = "steady", sense: str = "max") -> float | None:
        """Извлекает прогнозируемое значение величины: "steady" — установившийся режим,
        "extrema" — худшая точка траектории. Раньше домен оценки решался значением поля
        spec.transient ("strict" => extrema) — теперь это отдельный spec.chance_domain
        (аудит 2026-09-20); вызывающая сторона должна передавать spec.chance_domain."""
        alias_map = {
            "GODT.S": "HT_S_PRODUCT",
            "GODT.FLASH": "HT_FLASH",
            "GODT.T95": "HT_T95_PRODUCT",
            "GODT.D15": "HT_D15_PRODUCT",
            "GODT.CFPP": "HT_CFPP_PRODUCT",
            "GODT.CN": "HT_CN_PRODUCT",
            "HT_T_OUT": "HT_T11",
            "HT_T11": "HT_T_OUT",
        }
        keys = [quantity]
        if quantity in alias_map:
            keys.append(alias_map[quantity])

        for k in keys:
            if domain == "extrema" and k in self.trajectory_extrema:
                min_v, max_v, _ = self.trajectory_extrema[k]
                return max_v if sense == "max" else min_v
            if k in self.steady_state:
                return self.steady_state[k]
        return None


class NegotiationEvent(Frozen):
    round: int
    kind: Literal["PROPOSED", "CERTIFIED", "REPAIR_PROPOSED", "REPAIR_COMPOSED",
                  "BEST_UPDATED", "CONVERGED", "NO_NEW_CANDIDATES", "BUDGET_EXHAUSTED",
                  "KERNEL_OVERRIDE"]
    actor: str
    candidate: str | None = None
    detail: str = ""


class Merit(Frozen):
    v: tuple[float, float, float, float]   # нарушения ярусов T0..T3 (UNKNOWN в применимом ярусе -> inf)
    utility_rub_h: float                   # маржа + ценовой член блендинга - стоимость хода
    min_slack: float                       # минимальный нормированный запас T1–T3
    move_norm: float


# =============================================================================
# 4.5. Блендинг и баланс сырья
# =============================================================================

class BlendPrice(Frozen):
    prop: str                              # "GODT.S", "GODT.E360", "GODT.FLASH", "GODT.CFPP", "GODT.CN", "GODT.D15", "STOCK.GODT"
    rub_per_unit_per_t: float              # изменение себестоимости тонны товарного ДТ на единицу свойства
    method: Literal["finite_difference", "dual"]
    step: float                            # шаг конечной разности


class BlendingCertificate(Frozen):
    candidate: str
    round: int
    status: Literal["FEASIBLE", "INFEASIBLE_ELASTIC"]
    cost_rub_per_t: float
    elastic_violation: dict[str, float] = Field(default_factory=dict)  # спецификация -> нарушение
    binding: tuple[str, ...] = ()
    shares: dict[str, float] = Field(default_factory=dict)
    product_evaluations: tuple[ConstraintEvaluation, ...] = ()
    prices: tuple[BlendPrice, ...] = ()
    utility_delta_rub_h: float = 0.0       # вклад в полезность хода ГО относительно hold


# =============================================================================
# 4.6. Решение, восстановление, ядро безопасности, трасса
# =============================================================================

class DecisionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NO_CHANGE_DEADBAND = "NO_CHANGE_DEADBAND"
    SUCCESS_CORRECTIVE = "SUCCESS_CORRECTIVE"
    RECOVERY_ADVISORY = "RECOVERY_ADVISORY"
    REFUSAL_DATA = "REFUSAL_DATA"
    REFUSAL_NO_SAFE_ACTION = "REFUSAL_NO_SAFE_ACTION"
    REFUSAL_TIMEOUT = "REFUSAL_TIMEOUT"


class RecoveryStep(Frozen):
    k: int
    delta_u: dict[str, float]
    predicted_violation: tuple[float, float, float, float]
    key_values: dict[str, float]


class RecoveryPlan(Frozen):
    steps: tuple[RecoveryStep, ...]
    reaches_feasibility: bool
    expected_cycles: int
    target_u: dict[str, float] | None = None


class Alternative(Frozen):
    candidate: str
    kind: Literal["pareto_safer_sulfur", "pareto_gentler_catalyst", "pareto_more_margin",
                  "rejected_best_margin", "global_target"]
    delta_u: dict[str, float]
    utility_rub_h: float | None
    reasons: tuple[str, ...]


class ArbitrationDecision(Frozen):
    status: DecisionStatus
    selected: str | None
    delta_u: dict[str, float]
    merit: Merit | None
    hold_merit: Merit
    alternatives: tuple[Alternative, ...] = ()
    pareto_front: tuple[str, ...] = ()
    recovery: RecoveryPlan | None = None
    refusal_text: str | None = None
    reason_codes: tuple[str, ...] = ()


class KernelCheck(Frozen):
    name: str
    passed: bool
    detail: str


class KernelVerdict(Frozen):
    passed: bool
    checks: tuple[KernelCheck, ...]
    overridden_status: DecisionStatus | None = None
    kernel_version: str = "1.0.0"


class DecisionTrace(Frozen):
    cycle_id: str
    t: datetime
    code_version: str
    policy_version: str
    inputs_hash: str
    data: DataAssessment
    estimate: PlantEstimate
    candidates: tuple[Candidate, ...]
    predictions: tuple[Prediction, ...]
    certificates: tuple[ConstraintCertificate, ...]
    blending: tuple[BlendingCertificate, ...]
    negotiation: tuple[NegotiationEvent, ...]
    decision: ArbitrationDecision
    kernel: KernelVerdict
    recipe_shares: dict[str, float] | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
