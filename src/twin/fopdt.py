"""Цифровой двойник технологического процесса (Discrete MIMO FOPDT Twin).

Реализует апериодическую динамику первого порядка с чистым запаздыванием
(First Order Plus Dead Time, FOPDT) для комплекса «ЭЛОУ-АВТ-6 -> 24-2000»:
- Аналитическая ZOH-дискретизация (Zero-Order Hold, alpha = exp(-dt/tau));
- Раздельные физические постоянные времени (tau_K2 = 60 мин, tau_rx = 40 мин и др.);
- Дифференцированное транспортное запаздывание (кольцевые буферы Dead Time);
- Каноническое уравнение релаксации к базису (устранение Baseline Reset Bug);
- 6D пространство состояний (MIMO State-Space);
- Бесшовная интеграция с 17 ВАК (VakCalculator) и ликвидация KeyError: 'T50'.
"""

from __future__ import annotations

import math
from collections import deque
from copy import deepcopy
from typing import Dict, Any, Optional, List
import numpy as np

from src.twin.vak import VakCalculator

# =============================================================================
# TODO (Post-MVP): Оценивание состояния через EKF / Moving Horizon Estimation (MHE)
# В текущей реализации MVP динамика рассчитывается через аналитическую ZOH-схему FOPDT
# с кольцевыми буферами чистого запаздывания.
# Для промышленного внедрения с нелинейным оцениванием состояния (>100 строк кода):
# 1. Замена дискретной FOPDT-модели на расширенный фильтр Калмана (EKF) или
#    оцениватель на скользящем горизонте (Moving Horizon Estimator, MHE):
#    min_{x_0, {w_k}} ||x_0 - \bar{x}_0||_{P_0}^2 + \sum ||w_k||_Q^2 + \sum ||v_k||_R^2.
# 2. Динамическая адаптация ковариаций шума измерений R(t) на основе реальной
#    достоверности КИПиА и устаревания анализов LIMS (инновационная ковариация).
# 3. Совместная идентификация параметров модели (Online Parameter Estimation)
#    для компенсации постепенной дезактивации катализатора Co-Mo/Ni-Mo в Р-202.
# =============================================================================


# Постоянные времени tau_i (минуты) согласно System_Design.md (Шаг 2.2)
DEFAULT_TAU: Dict[str, float] = {
    "T20": 15.0,     # Верх колонны К-2 (острое орошение)
    "T33": 60.0,     # Отбор дизельной фракции К-2 (исправлено с 20 на 60 мин)
    "T55": 18.0,     # Перевал печи П-3 (COT)
    "T6": 40.0,      # Слой катализатора реактора Р-202
    "W10": 6.0,      # Перепад давления вакуумной колонны К-10
    "Sulfur": 40.0,  # Сера гидрогенизата ГО ДТ
}

# Транспортные запаздывания theta_i (в шагах сетки dt = 10 мин)
# d = floor(theta / dt)
DEFAULT_DELAYS: Dict[str, int] = {
    "T20": 0,     # theta = 0.0 мин (мгновенный отклик на острое орошение)
    "T33": 2,     # theta = 20.0 мин (задержка по высоте колонны и отбору)
    "T55": 1,     # theta = 10.0 мин (транспортное время змеевика печи)
    "T6": 1,      # theta = 10.0 мин (задержка распределения квенча)
    "W10": 0,     # theta = 0.0 мин
    "Sulfur": 1,  # theta = 10.0 мин
}

# Векторы пространства состояний и управляющих воздействий
STATE_VARIABLES: List[str] = ["T20", "T33", "T55", "T6", "W10", "Sulfur"]
CONTROL_INPUTS: List[str] = ["F19", "F12", "F31", "F15", "T20_sp"]

# Матрица статических коэффициентов передачи K (6x5) из System_Design.md (Шаг 2.4)
# Строки: STATE_VARIABLES, Столбцы: CONTROL_INPUTS
DEFAULT_GAIN_MATRIX: Dict[str, Dict[str, float]] = {
    "T20":    {"F19": -0.120, "F12": -0.020, "F31":  0.000, "F15":  0.000,   "T20_sp":  0.850},
    "T33":    {"F19": -0.080, "F12": -0.160, "F31": +0.040, "F15":  0.000,   "T20_sp":  0.100},
    "T55":    {"F19":  0.000, "F12":  0.000, "F31": -0.045, "F15":  0.000,   "T20_sp":  0.000},
    "T6":     {"F19":  0.000, "F12":  0.000, "F31":  0.000, "F15": -0.014,   "T20_sp":  0.000},
    "W10":    {"F19":  0.000, "F12":  0.000, "F31":  0.000, "F15": +0.00025, "T20_sp":  0.000},
    "Sulfur": {"F19":  0.000, "F12":  0.000, "F31":  0.000, "F15": +0.012,   "T20_sp":  0.000},
}

# Стандартные среднеквадратичные отклонения шума КИПиА (при enable_noise=True)
DEFAULT_NOISE_STD: Dict[str, float] = {
    "T20": 0.15,
    "T33": 0.20,
    "T55": 0.10,
    "T6": 0.15,
    "W10": 0.002,
    "Sulfur": 0.05,
}


class DiscreteMIMOFOPDTTwin:
    """
    Многосвязный дискретный цифровой двойник технологического поезда
    ЭЛОУ-АВТ-6 -> 24-2000 (MIMO State-Space FOPDT с ZOH).
    """

    def __init__(
        self,
        dt_minutes: float = 10.0,
        tau: Optional[Dict[str, float]] = None,
        delays: Optional[Dict[str, int]] = None,
        gain_matrix: Optional[Dict[str, Dict[str, float]]] = None,
        enable_noise: bool = False,
        noise_std: Optional[Dict[str, float]] = None,
    ):
        """
        :param dt_minutes: Шаг дискретизации по времени (мин). По умолчанию 10.0 мин.
        :param tau: Словарь постоянных времени для выходных переменных.
        :param delays: Словарь задержек (число шагов сетки).
        :param gain_matrix: Матрица коэффициентов статического усиления K.
        :param enable_noise: Флаг генерации гауссовского технологического шума.
        :param noise_std: СКО шума для каждого тега.
        """
        self.dt = dt_minutes
        self.tau = tau or DEFAULT_TAU
        self.delays = delays or DEFAULT_DELAYS
        self.K = gain_matrix or DEFAULT_GAIN_MATRIX
        self.enable_noise = enable_noise
        self.noise_std = noise_std or DEFAULT_NOISE_STD

        # Вычисление коэффициентов ZOH-дискретизации: alpha_i = exp(-dt / tau_i)
        self.alpha: Dict[str, float] = {
            var: math.exp(-self.dt / self.tau[var]) for var in STATE_VARIABLES
        }

        # Максимальная глубина предыстории для буферов задержки
        max_delay = max(self.delays.values()) if self.delays else 0
        self.buffer_size = max_delay + 1

        # Кольцевые буферы для управляющих воздействий
        self.u_history: Dict[str, deque] = {
            ctrl: deque([0.0] * self.buffer_size, maxlen=self.buffer_size)
            for ctrl in CONTROL_INPUTS
        }

        # Базовая рабочая точка (y_base) и текущее состояние (y_k)
        self.base_state: Optional[Dict[str, Any]] = None
        self.current_state: Optional[Dict[str, Any]] = None

    def initialize(self, initial_telemetry: Dict[str, Any]) -> None:
        """
        Захват базовой рабочей точки (Operating Point) и инициализация динамики.

        :param initial_telemetry: Исходные показания КИПиА и LIMS.
        """
        self.base_state = deepcopy(initial_telemetry)
        self.current_state = deepcopy(initial_telemetry)

        # Сброс кольцевых буферов предыстории
        for ctrl in CONTROL_INPUTS:
            self.u_history[ctrl] = deque([0.0] * self.buffer_size, maxlen=self.buffer_size)

    def step(self, delta_u: Dict[str, float]) -> Dict[str, Any]:
        """
        Выполнение одного шага симуляции длительностью dt_minutes.

        Реализует каноническое разностное уравнение релаксации к базису:
        y[k] = alpha * y[k-1] + (1 - alpha) * y_base + (1 - alpha) * K * delta_u[k - d]

        :param delta_u: Словарь приращений уставок, например {"F15": 50.0, "T20_sp": -1.0}
        :return: Обновленный словарь состояния КИПиА
        """
        if self.current_state is None or self.base_state is None:
            raise ValueError("Цифровой двойник не инициализирован! Вызовите initialize().")

        # 1. Запись текущих управляющих воздействий в кольцевые буферы
        for ctrl in CONTROL_INPUTS:
            val = float(delta_u.get(ctrl, 0.0))
            self.u_history[ctrl].append(val)

        # 2. Обновление динамических состояний по разностным уравнениям ZOH
        for var in STATE_VARIABLES:
            a = self.alpha[var]
            y_prev = float(self.current_state.get(var, 0.0))
            y_base = float(self.base_state.get(var, 0.0))
            d = self.delays.get(var, 0)

            # Вычисление статического отклика от задержанных управляющих сигналов
            # u[k - d] берется из истории: индекс -1 - d
            forced_response = 0.0
            for ctrl in CONTROL_INPUTS:
                k_gain = self.K.get(var, {}).get(ctrl, 0.0)
                if k_gain != 0.0:
                    delayed_u = self.u_history[ctrl][-1 - d]
                    forced_response += k_gain * delayed_u

            # Релаксация к базису: y[k] = a * y[k-1] + (1 - a) * y_base + (1 - a) * forced_response
            y_new = a * y_prev + (1.0 - a) * y_base + (1.0 - a) * forced_response

            # Наложение стохастического измерительного шума при необходимости
            if self.enable_noise:
                std = self.noise_std.get(var, 0.0)
                if std > 0.0:
                    y_new += float(np.random.normal(0.0, std))

            self.current_state[var] = y_new

        return self.current_state

    def get_telemetry(self) -> Dict[str, Any]:
        """
        Формирует полный срез телеметрии для UI (Streamlit) и агентов.

        Обогащает физические параметры КИПиА расчетом 17 ВАК на лету.
        Физические теги (например, 'T20', 'T33') и расчетные качества
        ('AVT6:240-350:T50') полностью изолированы и не конфликтуют.
        """
        if self.current_state is None:
            return {}

        telemetry = deepcopy(self.current_state)

        # Вычисление 17 виртуальных анализаторов качества
        vak_values = VakCalculator.calculate(telemetry)
        telemetry.update(vak_values)

        return telemetry
