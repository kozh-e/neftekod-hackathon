"""Модель резервуарного парка сырья и компонентов блендинга (ComponentTank).

Реализует динамику накопления и смешения в резервуарах (ADR-6):
- Идеальное смешение серы по массе потоков;
- Смешение плотности D15, фракционного состава T95, цетанового числа CN и ПТФ по объему;
- Нелинейное смешение температуры вспышки через индекс Вики-Читтендена (FBI);
- Прогнозирование свойств запаса в резервуаре на горизонте роллаута.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


@dataclass
class ComponentTank:
    """
    Резервуар компонента смешения с динамическим пересчетом качества при приеме и откачке.
    """

    name: str
    stock_t: float
    props: Dict[str, float] = field(default_factory=dict)
    prop_sources: Dict[str, str] = field(default_factory=dict)

    # Константы Вики-Читтендена для индекса вспышки
    FBI_A: float = 4.1485
    FBI_B: float = 0.0601

    @classmethod
    def _flash_to_fbi(cls, t_flash: float) -> float:
        return 10.0 ** (cls.FBI_A - cls.FBI_B * t_flash)

    @classmethod
    def _fbi_to_flash(cls, fbi: float) -> float:
        safe_fbi = max(float(fbi), 1e-6)
        return (cls.FBI_A - math.log10(safe_fbi)) / cls.FBI_B

    def receive(self, flow_tph: float, props_in: Mapping[str, float], dt_h: float) -> None:
        """
        Прием нового потока в резервуар за время dt_h (часы).
        Выполняет расчет идеального смешения компонентов.
        """
        mass_in = max(0.0, flow_tph * dt_h)
        if mass_in <= 1e-6:
            return

        m_old = max(0.0, self.stock_t)
        m_new = m_old + mass_in
        if m_new <= 1e-6:
            return

        # 1. Смешение серы по массе
        s_old = self.props.get("S_ppm", 8.6)
        s_in = props_in.get("S_ppm", props_in.get("HT_S_PRODUCT", s_old))
        s_new = (m_old * s_old + mass_in * s_in) / m_new

        # 2. Объемы компонентов
        d15_old = self.props.get("D15", 836.1)
        d15_in = props_in.get("D15", props_in.get("HT_D15_PRODUCT", d15_old))
        rho_old = max(d15_old / 1000.0, 0.5)
        rho_in = max(d15_in / 1000.0, 0.5)

        v_old = m_old / rho_old
        v_in = mass_in / rho_in
        v_new = v_old + v_in

        v_frac_old = v_old / max(v_new, 1e-6)
        v_frac_in = v_in / max(v_new, 1e-6)

        # 3. Плотность D15 по объему
        d15_new = v_frac_old * d15_old + v_frac_in * d15_in

        # 4. Объемное линейное смешение T95, CN, CFPP
        t95_old = self.props.get("T95", 347.0)
        t95_in = props_in.get("T95", props_in.get("HT_T95_PRODUCT", t95_old))
        t95_new = v_frac_old * t95_old + v_frac_in * t95_in

        cn_old = self.props.get("CN", 53.75)
        cn_in = props_in.get("CN", props_in.get("HT_CN_PRODUCT", cn_old))
        cn_new = v_frac_old * cn_old + v_frac_in * cn_in

        cfpp_old = self.props.get("CFPP", -6.0)
        cfpp_in = props_in.get("CFPP", props_in.get("HT_CFPP_PRODUCT", cfpp_old))
        cfpp_new = v_frac_old * cfpp_old + v_frac_in * cfpp_in

        # Объемное смешение фракции разгонки E360 (% об.)
        e360_old = self.props.get("E360", 96.0)
        e360_in = props_in.get("E360", props_in.get("HT_E360_PRODUCT", e360_old))
        e360_new = v_frac_old * e360_old + v_frac_in * e360_in

        # 5. Температура вспышки через индекс FBI
        flash_old = self.props.get("Flash", 68.0)
        flash_in = props_in.get("Flash", props_in.get("HT_FLASH", flash_old))
        fbi_old = self._flash_to_fbi(flash_old)
        fbi_in = self._flash_to_fbi(flash_in)
        fbi_new = v_frac_old * fbi_old + v_frac_in * fbi_in
        flash_new = self._fbi_to_flash(fbi_new)

        self.stock_t = m_new
        self.props["S_ppm"] = s_new
        self.props["D15"] = d15_new
        self.props["T95"] = t95_new
        self.props["E360"] = e360_new
        self.props["CN"] = cn_new
        self.props["CFPP"] = cfpp_new
        self.props["Flash"] = flash_new

    def withdraw(self, mass_t: float) -> None:
        """Списание массы продукта из резервуара."""
        self.stock_t = max(0.0, self.stock_t - mass_t)

    def forecast(
        self, flow_tph: float, props_in: Mapping[str, float], horizon_h: float
    ) -> Dict[str, float]:
        """
        Прогноз физико-химических показателей смеси в резервуаре на горизонте horizon_h.
        Не изменяет текущее состояние резервуара.
        """
        tank_copy = copy.deepcopy(self)
        tank_copy.receive(flow_tph, props_in, horizon_h)
        return dict(tank_copy.props)
