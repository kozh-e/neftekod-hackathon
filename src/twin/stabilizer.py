"""Модуль колонны стабилизации гидрогенизата К-201 (24-2000).

Реализует расчет температуры вспышки дизельного топлива в закрытом тигле (ADR-3):
- Чувствительность к сырьевой нагрузке установки F9;
- Чувствительность к системному давлению верха колонны стабилизации P24;
- Чувствительность к расходу отпаривающего газа поддува W7.
"""

from __future__ import annotations

from src.twin.params import StabilizerParams


class StabilizerColumnCalculator:
    """
    Статический расчет температуры вспышки товарного гидрогенизата после К-201.
    """

    def __init__(self, p: StabilizerParams):
        self.p = p

    def evaluate(self, feed_tph: float, p24_mpa: float, w7_tph: float) -> float:
        """
        Расчет температуры вспышки (°C).

        :param feed_tph: Массовый расход сырья установки HT_F9, т/ч.
        :param p24_mpa: Давление верха колонны К-201 HT_P24, МПа.
        :param w7_tph: Расход газа поддува К-201 HT_W7, т/ч.
        :return: Температура вспышки в диапазоне [40.0, 90.0] °C.
        """
        delta_feed = feed_tph - self.p.f9_ref
        delta_p24 = p24_mpa - self.p.p24_ref
        delta_w7 = w7_tph - self.p.w7_ref

        flash_calc = (
            self.p.flash_ref
            + self.p.a_F * delta_feed
            + self.p.a_P * delta_p24
            + self.p.a_W * delta_w7
        )

        # Ограничиваем физическим диапазоном
        return max(40.0, min(90.0, flash_calc))
