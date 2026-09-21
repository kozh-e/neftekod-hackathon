"""Независимое ядро безопасности (Safety Kernel, ADR-22, §5.12).

Изолированный верификатор решений арбитража.
СТРОГОЕ ТРЕБОВАНИЕ АРХИТЕКТУРЫ: НЕ импортирует negotiation, arbitration, repair,
generator, global_search, recovery, auditors.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import scipy.stats as stats

from src.agents.contracts import (
    ArbitrationDecision,
    AutomationLevel,
    ConstraintSpec,
    ConstraintStatus,
    DataAssessment,
    DecisionStatus,
    KernelCheck,
    KernelVerdict,
    PlantEstimate,
    Tier,
)
from src.agents.policy import PolicyConfig

KERNEL_VERSION = "1.0.0"

# Независимые априорные стандартные отклонения КИПиА
_KERNEL_SIGMA: Dict[str, float] = {
    "GODT.S": 0.0965,     # лог-домен
    "HT_DP_KPA": 0.05,    # лог-домен
    "GODT.FLASH": 4.78,   # линейный (°C)
    "GODT.T95": 3.27,     # линейный (°C)
    "GODT.D15": 1.20,     # линейный (кг/м3)
    "AVT_T55": 1.0,       # линейный (°C)
    "HT_T11": 1.5,        # линейный (°C)
    "HT_GOR": 10.0,       # линейный (нм3/м3)
    "AVT_F31": 10.0,      # линейный (т/ч)
    "AVT_P52": 0.005,     # линейный (кгс/см2)
    "BUFFER.INVENTORY_T": 15.0, # линейный (т)
}


def _kernel_z(alpha: float) -> float:
    """Независимый расчет квантиля нормального распределения."""
    clamped = max(1e-6, min(0.5, float(alpha)))
    return float(stats.norm.ppf(1.0 - clamped))


def independent_effective_value(
    spec: ConstraintSpec,
    estimate: Optional[PlantEstimate],
    val: float,
    policy: PolicyConfig,
) -> float:
    """Собственная независимая реализация расчета эффективного шансового значения."""
    sigma = _KERNEL_SIGMA.get(spec.quantity, 0.0)
    if estimate and spec.quantity in estimate.quality:
        q_est = estimate.quality[spec.quantity]
        sigma = math.sqrt(q_est.sigma_meas ** 2 + q_est.sigma_calib ** 2)

    alpha = policy.alpha_equipment if spec.tier == Tier.T1_EQUIPMENT else policy.alpha_quality
    z = _kernel_z(alpha)

    if spec.domain == "log":
        if spec.sense == "max":
            return val * math.exp(z * sigma)
        else:
            return val * math.exp(-z * sigma)
    else:
        if spec.sense == "max":
            return val + z * sigma
        else:
            return val - z * sigma


def check(name: str, passed: bool, detail: str = "") -> KernelCheck:
    return KernelCheck(name=name, passed=passed, detail=detail or ("PASS" if passed else "FAIL"))


class SafetyKernel:
    """Независимое ядро безопасности (Fail-Closed Gatekeeper)."""

    @classmethod
    def verify(
        cls,
        decision: ArbitrationDecision,
        estimate: Optional[PlantEstimate],
        data: Optional[DataAssessment],
        registry: Any,
        policy: Optional[PolicyConfig] = None,
        twin_factory: Optional[Callable[..., Any]] = None,
    ) -> KernelVerdict:
        pol = policy or PolicyConfig()
        checks: List[KernelCheck] = []

        u0 = estimate.u_actual if estimate else {}
        u1 = {k: u0.get(k, 0.0) + decision.delta_u.get(k, 0.0) for k in (set(u0.keys()) | set(decision.delta_u.keys()))}

        # 1. Проверка T0.bounds: пределы перемещения органов управления.
        # Fail-closed (аудит 2026-09-20): раньше отсутствие registry.mv_lo/mv_hi целиком или
        # для конкретного MV молча трактовалось как "ограничения нет" (bounds_ok оставался
        # True) — для "Fail-Closed Gatekeeper" правильно требовать возможность проверки, а
        # не тихо пропускать её. Оба живых вызова (graph.py, console/forecast.py) всегда
        # передают полный RegistryAdapter, так что здесь не должно ничего измениться.
        bounds_ok = True
        bounds_err = []
        if registry:
            if not (hasattr(registry, "mv_lo") and hasattr(registry, "mv_hi")):
                bounds_ok = False
                bounds_err.append("registry не предоставляет mv_lo/mv_hi — независимая проверка границ невозможна")
            else:
                for k, val in u1.items():
                    if k not in registry.mv_lo or k not in registry.mv_hi:
                        bounds_ok = False
                        bounds_err.append(f"{k}: нет границ в registry.mv_lo/mv_hi")
                        continue
                    lo, hi = registry.mv_lo[k], registry.mv_hi[k]
                    if val < lo - 1e-4 or val > hi + 1e-4:
                        bounds_ok = False
                        bounds_err.append(f"{k}={val:.2f} вне [{lo}, {hi}]")
        checks.append(check("T0.bounds", bounds_ok, "; ".join(bounds_err)))

        # 2. Проверка T0.rate: скорость изменения за такт.
        # Fail-closed только на уровне объекта registry (нет атрибута mv_max_move вовсе —
        # это малоформный/неверный registry). Отсутствие КОНКРЕТНОГО MV в mv_max_move —
        # НЕ повод для fail-closed: по паспорту оборудования у HT_P_SP/HT_GOR_SP сознательно
        # нет ограничения скорости хода (см. registry.py, tests/console/test_forecast_
        # corridor.py::test_corridor_specs) — это легитимное "проверено, лимита нет", а не
        # "неизвестно". Первая попытка (аудит 2026-09-20) fail-closed'ила и по конкретному
        # MV тоже, добавив для этого два выдуманных RATE-спека по значениям из
        # candidates.py::DEFAULT_MVS.max_move (шаг генерации кандидатов, а не физический
        # лимит) — сломала console corridor-тесты и была отменена.
        rate_ok = True
        rate_err = []
        if registry:
            if not hasattr(registry, "mv_max_move"):
                rate_ok = False
                rate_err.append("registry не предоставляет mv_max_move — независимая проверка скорости хода невозможна")
            else:
                for k, delta in decision.delta_u.items():
                    if abs(delta) <= 1e-9:
                        continue
                    if k not in registry.mv_max_move:
                        continue
                    max_m = registry.mv_max_move[k]
                    if abs(delta) > max_m + 1e-4:
                        rate_ok = False
                        rate_err.append(f"|{k}={delta}| > {max_m}")
        checks.append(check("T0.rate", rate_ok, "; ".join(rate_err)))

        # 3. Проверка соответствия уровня данных и статуса решения
        level = data.automation_level if data else AutomationLevel.FULL
        status_ok = True
        status_err = ""
        if level == AutomationLevel.REFUSAL_DATA and decision.status != DecisionStatus.REFUSAL_DATA:
            status_ok = False
            status_err = "При REFUSAL_DATA допустим только статус REFUSAL_DATA"
        elif level == AutomationLevel.CORRECTIVE_ONLY and decision.status == DecisionStatus.SUCCESS:
            status_ok = False
            status_err = "В режиме CORRECTIVE_ONLY экономические ходы SUCCESS запрещены"
        checks.append(check("data.level_permits_status", status_ok, status_err))

        # 4. Проверка заблокированных MV (data.blocked_mvs)
        blocked_mvs = set(data.blocked_mvs) if data else set()
        violated_blocked = set(decision.delta_u.keys()) & blocked_mvs
        checks.append(check("data.blocked_mvs", not bool(violated_blocked), f"Затронуты заблокированные MV: {violated_blocked}" if violated_blocked else ""))

        # 5. Независимая проверка ограничений оборудования и качества.
        # Прогоняется и для HOLD (delta_u пуст) — иначе ядро (единственная независимая от
        # агентов перепроверка) не заметит, что уже действующее состояние тихо нарушает
        # предел (см. аудит: RX.DP_MAX мог остаться ACTIVE при ошибке в reliability.py,
        # и ядро это никак не перепроверяло для NO_CHANGE_DEADBAND).
        if twin_factory is not None and estimate is not None:
            try:
                twin = twin_factory(estimate)
                pred_ss = twin.steady_state(u1)
                if decision.delta_u:
                    specs = registry.applicable(decision.delta_u) if hasattr(registry, "applicable") else ()
                else:
                    # applicable() фильтрует по изменившимся MV — для HOLD это всегда [].
                    # Для независимой проверки уже действующего состояния берём весь реестр.
                    specs = getattr(registry, "specs", ())
                for sp in specs:
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
                    val = pred_ss.get(sp.quantity)
                    if val is None and sp.quantity in alias_map:
                        val = pred_ss.get(alias_map[sp.quantity])
                    if val is None:
                        val = u1.get(sp.quantity)

                    if val is not None:
                        eff_val = independent_effective_value(sp, estimate, float(val), pol)
                        if decision.status in (DecisionStatus.SUCCESS, DecisionStatus.SUCCESS_CORRECTIVE, DecisionStatus.NO_CHANGE_DEADBAND):
                            if sp.key == "FURNACE.COT_POLICY_WARM":
                                delta_t55 = decision.delta_u.get("AVT_T55_SP", 0.0)
                                if delta_t55 > 1e-4 and eff_val > sp.limit + 1e-4:
                                    checks.append(check(f"{sp.key}", False, f"{eff_val:.2f} > limit {sp.limit}"))
                            elif sp.sense == "max" and eff_val > sp.limit + 1e-4:
                                checks.append(check(f"{sp.key}", False, f"{eff_val:.2f} > limit {sp.limit}"))
                            elif sp.sense == "min" and eff_val < sp.limit - 1e-4:
                                checks.append(check(f"{sp.key}", False, f"{eff_val:.2f} < limit {sp.limit}"))
            except Exception as e:
                # В случае сбоя проверки - fail-closed
                checks.append(check("kernel.twin_check", False, f"Ошибка двойника ядра: {e}"))

        passed = all(c.passed for c in checks)
        overridden = None if passed else DecisionStatus.REFUSAL_NO_SAFE_ACTION

        return KernelVerdict(
            passed=passed,
            checks=tuple(checks),
            overridden_status=overridden,
            kernel_version=KERNEL_VERSION,
        )


verify = SafetyKernel.verify
