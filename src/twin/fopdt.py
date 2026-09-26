"""Звено апериодической динамики первого порядка с чистым запаздыванием (FOPDT).

Используется цепочкой цифрового двойника (src/twin/chain.py) для динамических
переходных процессов реактора и стабилизатора: FirstOrderDeadTime реализует
аналитическую ZOH-дискретизацию (Zero-Order Hold, alpha = exp(-dt/tau)) с
чистым транспортным запаздыванием через кольцевой буфер.
"""

from __future__ import annotations

import math
from collections import deque


class FirstOrderDeadTime:
    """
    Звено апериодической динамики первого порядка с чистым запаздыванием (FOPDT).
    y[k] = a * y[k-1] + (1 - a) * u_ss[k-d]
    где a = exp(-dt / tau); d = round(theta / dt).
    На вход подаётся статическое (установившееся) значение u_ss.
    """

    def __init__(self, tau_min: float, theta_min: float, dt_min: float, y0: float):
        self.tau_min = max(0.0, tau_min)
        self.theta_min = max(0.0, theta_min)
        self.dt_min = max(1e-4, dt_min)
        self.d = max(0, round(self.theta_min / self.dt_min))
        self.a = math.exp(-self.dt_min / self.tau_min) if self.tau_min > 1e-4 else 0.0

        self._buf: deque[float] = deque(maxlen=max(1, self.d + 1))
        self._y: float = y0
        self.reset(y0)

    def reset(self, y0: float) -> None:
        """Сброс буфера задержки и внутреннего состояния к значению y0."""
        self._buf.clear()
        for _ in range(self.d + 1):
            self._buf.append(y0)
        self._y = y0

    def step(self, u_ss: float) -> float:
        """Шаг дискретного фильтра с чистым запаздыванием."""
        self._buf.append(u_ss)
        delayed_u = self._buf[0]
        self._y = self.a * self._y + (1.0 - self.a) * delayed_u
        return self._y

    @property
    def value(self) -> float:
        """Текущее значение выхода звена."""
        return self._y

