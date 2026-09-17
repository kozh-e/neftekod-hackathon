"""Компенсация запаздывания лабораторных анализов LIMS (Bumpless Transfer & Bias Decay).

Реализует требования System_Design.md (Шаг 5) и mvp_fifth_step.md:
1. Ретроспективная инновация: вычисление ошибки ВАК относительно исторической точки отбора пробы.
2. Экспоненциальное затухание (Bias Decay) с периодом полураспада 12 часов.
3. Безударный перенос (Bumpless Transfer): фильтрация первого порядка (tau = 30 мин),
   исключающая скачкообразные возмущения в контурах управления при вводе анализа.
4. Верхняя доверительная граница (Upper Confidence Bound, UCB + z*sigma) для наихудшего
   сценария по сере, защищающая от выпуска бракованной продукции Евро-5.

Вето Агента Качества в графе использует не этот компенсатор, а stat_offset() (src/agents/constraints.py)
с константами ADR-12 из src/agents/limits.py; значения по умолчанию здесь согласованы с ними.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Tuple

from src.agents.limits import QUALITY_Z, SIGMA_S0_PPM


def lims_age_from_state(state: Mapping[str, Any]) -> float:
    """Возраст анализов ЛИМС (ч) из состояния графа: raw_telemetry -> tags -> confidence."""
    raw = state.get("raw_telemetry")
    if raw is not None and hasattr(raw, "lims_age_hours"):
        return float(raw.lims_age_hours)
    tags: Optional[Mapping[str, Any]] = state.get("tags")
    if tags and "lims_age_hours" in tags:
        return float(tags["lims_age_hours"])
    confidence = state.get("confidence") or {}
    return float(confidence.get("lims_age_hours", 0.0))


class LimsBiasCompensator:
    """
    Класс коррекции виртуальных анализаторов качества (ВАК) с учетом
    транспортного и лабораторного запаздывания анализов LIMS.
    """

    def __init__(
        self,
        dt_minutes: float = 10.0,
        tau_filter_minutes: float = 30.0,
        half_life_hours: float = 12.0,
        base_sigma: float = SIGMA_S0_PPM,
        z: float = QUALITY_Z,
    ):
        """
        :param dt_minutes: Такт дискретизации системы (10.0 мин).
        :param tau_filter_minutes: Постоянная времени безударного фильтра (30.0 мин).
        :param half_life_hours: Период полураспада доверия к анализу LIMS (12.0 ч).
        :param base_sigma: Базовая погрешность прогноза серы (DATA, ADR-12).
        :param z: Квантиль доверительной границы (tz:598: +2σ).
        """
        self.z = z
        self.dt = dt_minutes
        self.tau_filter = tau_filter_minutes
        self.alpha_filter = math.exp(-self.dt / self.tau_filter)
        
        # Коэффициент экспоненциального затухания смещения (Bias Decay): ln(2) / T_half
        self.decay_lambda = math.log(2.0) / (half_life_hours * 60.0)
        
        self.target_bias = 0.0
        self.current_filtered_bias = 0.0
        self.base_sigma = base_sigma

    def process_new_lims_result(
        self,
        lims_value: float,
        historical_vak_value_at_sampling_time: float,
        current_age_minutes: float
    ) -> None:
        """
        Обработка нового лабораторного анализа при его поступлении.
        
        :param lims_value: Результат анализа LIMS (например, сера 8.5 ppm).
        :param historical_vak_value_at_sampling_time: Прогноз ВАК в момент отбора пробы (2-4 ч назад).
        :param current_age_minutes: Возраст пробы на момент ввода анализа в минутах.
        """
        # 1. Ретроспективная инновация (ошибка прогноза в прошлом)
        raw_error = float(lims_value) - float(historical_vak_value_at_sampling_time)

        # 2. Экспоненциальное затухание ошибки с учетом задержки анализа
        decay_factor = math.exp(-self.decay_lambda * max(0.0, current_age_minutes))

        # Установка целевого смещения
        self.target_bias = raw_error * decay_factor

    def get_corrected_vak(
        self,
        current_vak_value: float,
        minutes_since_last_lims: float
    ) -> Tuple[float, float]:
        """
        Вычисление скорректированного значения ВАК и верхней доверительной границы (UCB).
        
        :param current_vak_value: Текущий оперативный расчет ВАК.
        :param minutes_since_last_lims: Время, прошедшее с момента последнего анализа LIMS (мин).
        :return: (corrected_value, upper_confidence_bound)
        """
        # 1. Затухание целевого смещения за прошедший такт dt
        self.target_bias *= math.exp(-self.decay_lambda * self.dt)

        # 2. Безударный перенос (фильтрация первого порядка)
        self.current_filtered_bias = (
            self.alpha_filter * self.current_filtered_bias
            + (1.0 - self.alpha_filter) * self.target_bias
        )

        # 3. Скорректированное значение
        corrected_value = current_vak_value + self.current_filtered_bias

        # 4. Расчет растущей неопределенности при старении анализа
        uncertainty_multiplier = 1.0 + (max(0.0, minutes_since_last_lims) / (24.0 * 60.0))
        sigma_t = self.base_sigma * uncertainty_multiplier

        # 5. Верхняя доверительная граница (+z сигма)
        upper_confidence_bound = corrected_value + self.z * sigma_t

        return round(corrected_value, 3), round(upper_confidence_bound, 3)

    def reset(self) -> None:
        """Сброс состояния фильтра смещения."""
        self.target_bias = 0.0
        self.current_filtered_bias = 0.0
