"""Расчет шансовых технологических ограничений и распространения неопределенности.

Реализует спецификацию §5.2 implementation_plan_v3.md:
1. chance_effective: эффективное значение ограничения с учетом трех источников дисперсии:
   sigma_total^2 = sigma_meas^2 + sigma_calib^2(age) + sigma_model^2(Delta u);
2. Лог-домен для серы (GODT.S) и перепада давления (HT_DP_KPA);
3. z_from_alpha: расчет квантилей нормального распределения;
4. Ковариация параметров модели SIGMA_THETA (по умолчанию CV = 30%, ASSUMPTION).
"""

from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Sequence, Tuple
import numpy as np
import scipy.stats as stats

from src.agents.contracts import ConstraintSpec, PlantEstimate, Tier
from src.agents.policy import PolicyConfig

# Базовые значения неопределенных параметров модели theta_0 (ASSUMPTION)
DEFAULT_THETA_PARAMS: Dict[str, float] = {
    "E_h_R": 14000.0,    # Энергия активации трудноудаляемой серы, K
    "k_h_ref": 12.0,     # Масштаб активности катализатора
    "b_t95": 0.03,       # Чувствительность доли трудноудаляемой серы к T95
    "alpha_P": 1.0,      # Степень влияния давления на скорость HDS
    "alpha_G": 0.3,      # Степень влияния кратности ВСГ/сырье на скорость HDS
    "n_dp": 1.8,         # Показатель расхода в уравнении перепада давления
}

# Диагональная ковариационная матрица с коэффициентом вариации CV = 30% (ASSUMPTION)
CV_DEFAULT: float = 0.30
PARAM_NAMES: Tuple[str, ...] = tuple(DEFAULT_THETA_PARAMS.keys())
SIGMA_THETA: np.ndarray = np.diag([
    (CV_DEFAULT * DEFAULT_THETA_PARAMS[name]) ** 2 for name in PARAM_NAMES
])

# Априорная неопределенность переменных состояния, если нет в PlantEstimate.quality
SIGMA_STATE: Dict[str, float] = {
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


def z_from_alpha(alpha: float) -> float:
    """
    Вычисляет квантиль стандартного нормального распределения z = Phi^-1(1 - alpha).
    Например: alpha=0.0228 -> z ≈ 2.0; alpha=0.00135 -> z ≈ 3.0.
    """
    alpha_clamped = max(1e-6, min(0.5, float(alpha)))
    return float(stats.norm.ppf(1.0 - alpha_clamped))


class SensitivityModel:
    """
    Модель градиентов отклика выхода по неопределенным параметрам theta.
    Для hold (Delta u = 0) градиент g = 0.
    Для Delta u != 0 градиент g пропорционален шагу управления.
    """

    @staticmethod
    def grad_log_ratio(quantity: str, delta_u: Mapping[str, float]) -> np.ndarray:
        """
        Градиент d[ln f(u) - ln f(u0)] / d theta для лог-домена.
        """
        g = np.zeros(len(PARAM_NAMES), dtype=float)
        if not delta_u:
            return g

        norm_step = math.sqrt(sum(v ** 2 for v in delta_u.values()))
        if norm_step < 1e-6:
            return g

        if quantity == "GODT.S":
            # Чувствительность к E_h_R и активности k_h
            dt_in = delta_u.get("HT_TIN_SP", 0.0)
            df9 = delta_u.get("HT_FEED_SP", 0.0)
            g[0] = -dt_in / (363.3 + 273.15) ** 2 * 1e-4  # d(ln S)/d(E_h_R)
            g[1] = -df9 / (219.6 * 12.0)                  # d(ln S)/d(k_h)
            g[3] = -delta_u.get("HT_P_SP", 0.0) / 3.922   # d(ln S)/d(alpha_P)
            g[4] = -delta_u.get("HT_GOR_SP", 0.0) / 360.0 # d(ln S)/d(alpha_G)
        elif quantity == "HT_DP_KPA":
            df9 = delta_u.get("HT_FEED_SP", 0.0)
            g[5] = math.log(max(0.5, 1.0 + df9 / 219.6)) if abs(df9) > 1e-4 else 0.0

        return g

    @staticmethod
    def grad_diff(quantity: str, delta_u: Mapping[str, float]) -> np.ndarray:
        """
        Градиент d[f(u) - f(u0)] / d theta для линейного домена.
        """
        g = np.zeros(len(PARAM_NAMES), dtype=float)
        if not delta_u:
            return g

        norm_step = math.sqrt(sum(v ** 2 for v in delta_u.values()))
        if norm_step < 1e-6:
            return g

        # Для линейных моделей масштабируем нормой шага
        g[1] = 0.01 * norm_step
        return g


def chance_effective(
    spec: ConstraintSpec,
    mean_now: float,
    pred_u: float,
    pred_u0: float,
    estimate: Optional[PlantEstimate] = None,
    delta_u: Optional[Mapping[str, float]] = None,
    policy: Optional[PolicyConfig] = None,
    sens_model: Optional[SensitivityModel] = None,
) -> Tuple[float, float, float]:
    """
    Рассчитывает шансовое эффективное значение ограничения для кандидата u (§5.2):
    eff = center +/- z(alpha)·sigma_total,
    где sigma_total^2 = sigma_meas^2 + sigma_calib^2(age) + g @ SIGMA_THETA @ g.

    Единственная поддерживаемая сигнатура: mean_now/pred_u/pred_u0 — числовые
    текущее/прогнозное(кандидат)/прогнозное(hold) значения контролируемой
    величины; estimate — опциональный PlantEstimate для оценки текущей
    неопределенности состояния (sigma_meas/sigma_calib по spec.quantity).
    Раньше функция дополнительно поддерживала неявную (duck-typed) перегрузку
    chance_effective(spec, estimate, val, policy) — она была удалена как
    непредсказуемая; ни один вызывающий код в репозитории её не использовал
    (проверено по всем вызовам chance_effective в src/ и tests/).

    Возвращает:
    (effective_value, sigma_total, z).
    """
    m_now = float(mean_now)
    p_u = float(pred_u)
    p_0 = float(pred_u0)

    sens = sens_model or SensitivityModel()
    p = policy or PolicyConfig()
    du = dict(delta_u or {})

    alpha = p.alpha_equipment if spec.tier == Tier.T1_EQUIPMENT else p.alpha_quality
    z = z_from_alpha(alpha)

    # Оценка неопределенности текущего состояния
    q = estimate.quality.get(spec.quantity) if estimate else None
    if q is not None:
        base_var = q.sigma_meas ** 2 + q.sigma_calib ** 2
    else:
        base_var = SIGMA_STATE.get(spec.quantity, 1.0) ** 2

    # Неопределенность отклика модели на приращение Delta u
    if spec.domain == "log":
        g = sens.grad_log_ratio(spec.quantity, du)
        var_model = float(g @ SIGMA_THETA @ g)
        var_total = base_var + var_model
        sigma_total = math.sqrt(max(1e-8, var_total))

        # Контрфактический логарифмический прогноз
        # mean_now * (pred_u / pred_u0)
        p_u_safe = max(1e-4, p_u)
        p_0_safe = max(1e-4, p_0)
        m_now_safe = max(1e-4, m_now)

        center_log = math.log(m_now_safe) + math.log(p_u_safe) - math.log(p_0_safe)
        sign = +1.0 if spec.sense == "max" else -1.0
        eff_log = center_log + sign * z * sigma_total
        effective_val = math.exp(eff_log)

    else:
        g = sens.grad_diff(spec.quantity, du)
        var_model = float(g @ SIGMA_THETA @ g)
        var_total = base_var + var_model
        sigma_total = math.sqrt(max(1e-8, var_total))

        center = m_now + p_u - p_0
        sign = +1.0 if spec.sense == "max" else -1.0
        effective_val = center + sign * z * sigma_total

    return effective_val, sigma_total, z
