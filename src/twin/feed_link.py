"""Модуль связи технологических установок ЭЛОУ-АВТ-6 -> 24-2000 (FeedLink).

Реализует передачу качества дизельных фракций с АВТ на установку гидроочистки (ADR-1):
- Уравнения зависимости T95, серы и плотности сырья от отбора фракции F30;
- Транспортное запаздывание theta и динамическое смешение tau_mix в сырьевом парке (режимы buffered / hot);
- Расчет суммарной выработки дизельного дистиллята для материального баланса.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque

from src.twin.params import FeedLinkParams


@dataclass(frozen=True)
class FeedState:
    avt_diesel_tph: float
    t95_feed_c: float
    s_feed_ppm: float
    d15_feed: float


class FeedLink:
    """
    Модель передачи сырья и динамики качества между блоками АВТ-6 и 24-2000.
    """

    def __init__(self, p: FeedLinkParams, dt_min: float = 10.0):
        self.p = p
        self.dt_min = dt_min
        self.delay_steps = max(0, round(self.p.theta_min / self.dt_min))
        self.alpha_mix = math.exp(-self.dt_min / max(self.p.tau_mix_min, 1e-3)) if self.p.mode == "buffered" else 0.0

        # Буферы чистого запаздывания для T95, S, D15
        self._buf_t95: Deque[float] = deque(maxlen=max(1, self.delay_steps + 1))
        self._buf_s: Deque[float] = deque(maxlen=max(1, self.delay_steps + 1))
        self._buf_d15: Deque[float] = deque(maxlen=max(1, self.delay_steps + 1))

        # Текущие состояния емкостного смешения
        self._t95_mix: float = self.p.t95_ref
        self._s_mix: float = self.p.s_ref
        self._d15_mix: float = self.p.d15_ref

        self.reset(self.p.f30_ref, self.p.f32_ref, 0.0)

    def _calculate_instantaneous(self, f30: float, f32: float, tfurn_dev: float) -> tuple[float, float, float, float]:
        """Статический расчет мгновенного качества потока на выходе АВТ."""
        # 1. Суммарная выработка дизеля АВТ
        f_avt = f30 + f32 + self.p.Y_T * tfurn_dev

        # 2. T95 сырья с чувствительностью из официальной формулы ВАК AVT6:240-350:EBP (2.66463)
        t95_in = self.p.t95_ref + self.p.dT95_dF30 * (f30 - self.p.f30_ref) + self.p.c_T95_T * tfurn_dev

        # 3. Сера сырья (прирост трудноудаляемых соединений при утяжелении фракции)
        s_in = self.p.s_ref * (1.0 + self.p.s_t95 * (t95_in - self.p.t95_ref))
        s_in = max(0.0, s_in)

        # 4. Плотность сырья при 15 °C
        d15_in = self.p.d15_ref + self.p.d_t95 * (t95_in - self.p.t95_ref)

        return f_avt, t95_in, s_in, d15_in

    def reset(self, f30: float, f32: float, tfurn_dev: float = 0.0) -> None:
        """Сброс состояния фильтров к равновесному режиму для заданных параметров."""
        f_avt, t95_ss, s_ss, d15_ss = self._calculate_instantaneous(f30, f32, tfurn_dev)

        self._buf_t95.clear()
        self._buf_s.clear()
        self._buf_d15.clear()

        # Заполняем буфер задержки стационарными значениями
        for _ in range(self.delay_steps + 1):
            self._buf_t95.append(t95_ss)
            self._buf_s.append(s_ss)
            self._buf_d15.append(d15_ss)

        self._t95_mix = t95_ss
        self._s_mix = s_ss
        self._d15_mix = d15_ss

    def steady_state(self, f30: float, f32: float, tfurn_dev: float = 0.0) -> FeedState:
        """Расчет установившегося состояния без учета динамических переходов."""
        f_avt, t95_ss, s_ss, d15_ss = self._calculate_instantaneous(f30, f32, tfurn_dev)
        return FeedState(
            avt_diesel_tph=f_avt,
            t95_feed_c=t95_ss,
            s_feed_ppm=s_ss,
            d15_feed=d15_ss,
        )

    def step(self, f30: float, f32: float, tfurn_dev: float = 0.0) -> FeedState:
        """Один шаг дискретной симуляции (dt_min)."""
        f_avt, t95_in, s_in, d15_in = self._calculate_instantaneous(f30, f32, tfurn_dev)

        # 1. Помещаем новое значение в буфер чистого запаздывания
        self._buf_t95.append(t95_in)
        self._buf_s.append(s_in)
        self._buf_d15.append(d15_in)

        # 2. Извлекаем значение с задержкой d шагов
        t95_delayed = self._buf_t95[0]
        s_delayed = self._buf_s[0]
        d15_delayed = self._buf_d15[0]

        # 3. Фильтрация смешения (в режиме hot alpha_mix = 0)
        a = 0.0 if self.p.mode == "hot" else self.alpha_mix

        self._t95_mix = a * self._t95_mix + (1.0 - a) * t95_delayed
        self._s_mix = a * self._s_mix + (1.0 - a) * s_delayed
        self._d15_mix = a * self._d15_mix + (1.0 - a) * d15_delayed

        return FeedState(
            avt_diesel_tph=f_avt,
            t95_feed_c=self._t95_mix,
            s_feed_ppm=self._s_mix,
            d15_feed=self._d15_mix,
        )
