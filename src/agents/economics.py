"""Экономическая модель оценки кандидатов управления (Margin Model).

Рассчитывает дельту операционной маржи (руб/ч) на установившемся режиме относительно удержания текущего режима (hold):
- throughput: прирост маржинального дохода от изменения переработки сырья;
- furnace: затраты на дополнительный подогрев сырья в печи (топливный газ);
- compressor: затраты электроэнергии на компримирование циркулирующего ВСГ;
- pressure: затраты на поддержание системного давления;
- hydrogen: расход водорода КЦА;
- catalyst: ускоренная термическая дезактивация катализатора при росте T_bed;
- avt_diesel: ценность прироста отбора прямогонного дизеля АВТ при изменении температуры печи AVT_T55
  (оценка снизу спредом «прямогонный дизель − нефть», решение команды 7.2.2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, Mapping, Optional

from src.agents.limits import GIVEAWAY_Z, SIGMA_S0_PPM, SULFUR_PRODUCT_MAX
from src.twin.params import EconomicsParams, ReactorParams


@dataclass(frozen=True)
class MarginBreakdown:
    throughput: float
    furnace: float
    compressor: float
    pressure: float
    hydrogen: float
    catalyst: float
    furnace_fuel_gas_nm3: float = 0.0
    furnace_mwh: float = 0.0
    avt_diesel: float = 0.0

    @property
    def total(self) -> float:
        """Net Utility: выручка за вычетом всех операционных затрат относительно hold."""
        return (
            self.throughput
            + self.avt_diesel
            - self.furnace
            - self.compressor
            - self.pressure
            - self.hydrogen
            - self.catalyst
        )


class MarginModel:
    """Модель маржинальности технологических решений относительно baseline (hold)."""

    def __init__(
        self,
        econ_params: Optional[EconomicsParams] = None,
        reactor_params: Optional[ReactorParams] = None,
    ) -> None:
        self.p = econ_params or EconomicsParams()
        self.rp = reactor_params or ReactorParams()

    @property
    def crack_spreads(self) -> Dict[str, float]:
        """Ключевые спреды переработки (Crack Spreads), руб/т."""
        return {
            "crude_to_straight_run": round(self.p.crude_to_straight_spread, 2),
            "straight_to_godt": round(self.p.straight_to_godt_spread, 2),
            "crude_to_godt": round(self.p.crude_to_godt_spread, 2),
        }

    def calc_hourly_gross_margin(self, feed_tph: float) -> float:
        """Расчет часовой валовой маржи гидроочистки (выручка от ГО ДТ минус стоимость прямогона), руб/ч."""
        return round(feed_tph * (self.p.price_godt * self.p.y_liq - self.p.price_straight_run), 2)

    def calc_hourly_operating_costs(
        self,
        feed_tph: float,
        tin_c: float,
        p13_mpa: float,
        vsg_nm3h: float,
        tbed_c: float,
        h2_nm3h: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Оценивает эксплуатационные затраты (OPEX) секции 24-2000 (руб/ч).
        """
        # 1. Топливный газ на подогрев сырья
        t_feed_inlet = 280.0  # после сырьевых теплообменников Т-201/Т-202 (°C)
        delta_t_heat = max(tin_c - t_feed_inlet, 10.0)
        furnace_kj_h = (feed_tph * 1000.0) * self.p.cp_oil * delta_t_heat
        furnace_mwh = furnace_kj_h / (3.6e6 * max(self.p.eta_furnace, 1e-4))
        furnace_cost = self.p.fuel_rub_mwh * furnace_mwh
        furnace_gas_nm3 = self.p.calc_fuel_gas_consumption_nm3(furnace_mwh)

        # 2. Электроэнергия компрессора ЦК-201
        compressor_cost = self.p.compressor_rub_per_nm3 * vsg_nm3h

        # 3. Поддержание давления
        pressure_cost = self.p.pressure_rub_h_per_mpa * p13_mpa

        # 4. Водород КЦА
        act_h2 = h2_nm3h if h2_nm3h is not None else self.p.f25_ref
        hydrogen_cost = self.p.h2_rub_per_nm3 * act_h2

        # 5. Термическая нагрузка на катализатор
        tbed_excess = max(0.0, tbed_c - self.rp.t_in_ref)
        catalyst_cost = self.p.catalyst_rub_h_per_degC * tbed_excess

        total_opex = furnace_cost + compressor_cost + pressure_cost + hydrogen_cost + catalyst_cost

        return {
            "furnace_cost_rub_h": round(furnace_cost, 2),
            "furnace_fuel_gas_nm3_h": round(furnace_gas_nm3, 2),
            "furnace_mwh_h": round(furnace_mwh, 4),
            "compressor_cost_rub_h": round(compressor_cost, 2),
            "pressure_cost_rub_h": round(pressure_cost, 2),
            "hydrogen_cost_rub_h": round(hydrogen_cost, 2),
            "catalyst_cost_rub_h": round(catalyst_cost, 2),
            "total_opex_rub_h": round(total_opex, 2),
        }

    def calc_hourly_net_margin(
        self,
        feed_tph: float,
        tin_c: float,
        p13_mpa: float,
        vsg_nm3h: float,
        tbed_c: float,
        h2_nm3h: Optional[float] = None,
    ) -> float:
        """Расчет часовой чистой операционной маржи гидроочистки (валовая маржа минус OPEX), руб/ч."""
        gross = self.calc_hourly_gross_margin(feed_tph)
        opex = self.calc_hourly_operating_costs(feed_tph, tin_c, p13_mpa, vsg_nm3h, tbed_c, h2_nm3h)
        return round(gross - opex["total_opex_rub_h"], 2)


    def evaluate(
        self,
        ss: Mapping[str, float],
        ss_hold: Mapping[str, float],
        u: Mapping[str, float],
        u_hold: Mapping[str, float],
    ) -> MarginBreakdown:
        """
        Оценивает изменение компонентов маржи на установившемся режиме (руб/ч).
        """
        # 1. Сырьевой поток
        f9 = float(u.get("HT_FEED_SP", ss.get("HT_F9", self.rp.feed_ref)))
        f9_hold = float(u_hold.get("HT_FEED_SP", ss_hold.get("HT_F9", self.rp.feed_ref)))
        delta_f9 = f9 - f9_hold

        # Throughput margin: (P_godt - P_straight) * y_liq * delta_F9
        margin_spread = self.p.price_godt - self.p.price_straight_run
        throughput = margin_spread * self.p.y_liq * delta_f9

        # 2. Печь гидроочистки (подогрев сырья на входе в реактор Р-202)
        tin = float(u.get("HT_TIN_SP", ss.get("HT_T_IN", self.rp.t_in_ref)))
        tin_hold = float(u_hold.get("HT_TIN_SP", ss_hold.get("HT_T_IN", self.rp.t_in_ref)))
        delta_tin = tin - tin_hold

        # Q_furnace = (F9 * 1000 * cp_oil * delta_tin) / (3.6e6 * eta)
        furnace_kj_h = (f9 * 1000.0) * self.p.cp_oil * delta_tin
        furnace_mwh = furnace_kj_h / (3.6e6 * max(self.p.eta_furnace, 1e-4))

        # Печь П-3 АВТ-6: подогрев потока AVT_F31 на ΔT55 и ценность прироста отбора дизеля
        avt_diesel = 0.0
        delta_t55 = float(u.get("AVT_T55_SP", ss.get("AVT_T55", 0.0))) - float(u_hold.get("AVT_T55_SP", ss_hold.get("AVT_T55", 0.0)))
        if abs(delta_t55) > 1e-4:
            f31 = float(ss.get("AVT_F31", self.p.f31_ref))
            furnace_avt_kj_h = (f31 * 1000.0) * self.p.cp_oil * delta_t55
            furnace_mwh += furnace_avt_kj_h / (3.6e6 * max(self.p.eta_furnace, 1e-4))
            delta_diesel_tph = float(ss.get("AVT_DIESEL_TPH", 0.0)) - float(ss_hold.get("AVT_DIESEL_TPH", 0.0))
            avt_diesel = self.p.crude_to_straight_spread * delta_diesel_tph

        furnace = self.p.fuel_rub_mwh * furnace_mwh
        furnace_gas_nm3 = self.p.calc_fuel_gas_consumption_nm3(furnace_mwh)

        # 3. Компрессор циркулирующего ВСГ (F2 / HT_VSG)
        vsg = float(ss.get("HT_VSG", ss.get("HT_F2", 0.0)))
        vsg_hold = float(ss_hold.get("HT_VSG", ss_hold.get("HT_F2", 0.0)))
        delta_vsg = vsg - vsg_hold
        compressor = self.p.compressor_rub_per_nm3 * delta_vsg

        # 4. Системное давление
        p13 = float(u.get("HT_P_SP", ss.get("HT_P_IN", self.rp.p_ref)))
        p13_hold = float(u_hold.get("HT_P_SP", ss_hold.get("HT_P_IN", self.rp.p_ref)))
        delta_p13 = p13 - p13_hold
        pressure = self.p.pressure_rub_h_per_mpa * delta_p13

        # 5. Свежий водород с КЦА
        # C_H2 * F25_ref * [(V/V_ref)*(P/P_ref) - (V_hold/V_ref)*(P_hold/P_ref)]
        f9_ref = max(self.rp.feed_ref, 1e-4)
        p_ref = max(self.rp.p_ref, 1e-4)
        ratio_curr = (f9 / f9_ref) * (p13 / p_ref)
        ratio_hold = (f9_hold / f9_ref) * (p13_hold / p_ref)
        delta_h2 = self.p.f25_ref * (ratio_curr - ratio_hold)
        hydrogen = self.p.h2_rub_per_nm3 * delta_h2

        # 6. Дезактивация катализатора (по средней температуре слоя)
        t_bed = float(ss.get("HT_BED_MEAN", ss.get("HT_T_OUT", tin)))
        t_bed_hold = float(ss_hold.get("HT_BED_MEAN", ss_hold.get("HT_T_OUT", tin_hold)))
        delta_tbed = t_bed - t_bed_hold
        catalyst = self.p.catalyst_rub_h_per_degC * delta_tbed

        return MarginBreakdown(
            throughput=round(throughput, 2),
            furnace=round(furnace, 2),
            compressor=round(compressor, 2),
            pressure=round(pressure, 2),
            hydrogen=round(hydrogen, 2),
            catalyst=round(catalyst, 2),
            furnace_fuel_gas_nm3=round(furnace_gas_nm3, 2),
            furnace_mwh=round(furnace_mwh, 4),
            avt_diesel=round(avt_diesel, 2),
        )


class EconomicsEvaluator:
    """
    Оценщик полезности технологических ходов согласно спецификации §5.10 (Задача P2.7).
    Полезность = Маржа (с ограничением по балансу сырья) + Вклад цен блендинга
                 - Стоимость перемещения MV - Износ катализатора.
    Логарифмический барьер риска исключен из полезности согласно ADR-15.
    """

    def __init__(
        self,
        margin_model: Optional[MarginModel] = None,
        policy: Optional[Any] = None,
    ) -> None:
        self.margin_model = margin_model or MarginModel()
        self.policy = policy

    def utility(
        self,
        ss: Mapping[str, float],
        ss_hold: Mapping[str, float],
        cand: Any,
        blending_certificate: Optional[Any] = None,
        policy: Optional[Any] = None,
        max_feed_allowed: Optional[float] = None,
    ) -> float:
        pol = policy or self.policy
        du = getattr(cand, "delta_u", cand) if not isinstance(cand, dict) else cand
        u_hold = {
            "HT_FEED_SP": float(ss_hold.get("HT_FEED_SP", ss_hold.get("HT_F9", self.margin_model.rp.feed_ref))),
            "HT_TIN_SP": float(ss_hold.get("HT_TIN_SP", ss_hold.get("HT_T_IN", self.margin_model.rp.t_in_ref))),
            "HT_P_SP": float(ss_hold.get("HT_P_SP", ss_hold.get("HT_P_IN", self.margin_model.rp.p_ref))),
            "HT_GOR_SP": float(ss_hold.get("HT_GOR_SP", ss_hold.get("HT_GOR", 360.0))),
            "AVT_T55_SP": float(ss_hold.get("AVT_T55_SP", ss_hold.get("AVT_T55", 381.7))),
        }
        u_cand = {k: u_hold[k] + du.get(k, 0.0) for k in u_hold}

        # 1. Расчет базовой маржи
        breakdown = self.margin_model.evaluate(ss, ss_hold, u_cand, u_hold)
        raw_margin = breakdown.total

        # 2. Ограничение маржи по балансу сырья буфера АВТ-ГО
        f9 = float(ss.get("HT_FEED_SP", ss.get("HT_F9", self.margin_model.rp.feed_ref)))
        if max_feed_allowed is not None and f9 > max_feed_allowed:
            excess_feed = f9 - max_feed_allowed
            margin_spread = self.margin_model.p.price_godt - self.margin_model.p.price_straight_run
            loss = margin_spread * self.margin_model.p.y_liq * excess_feed
            raw_margin -= max(0.0, loss)

        # 3. Вклад цен блендинга
        blend_utility = 0.0
        if blending_certificate is not None:
            blend_utility = float(getattr(blending_certificate, "utility_delta_rub_h", 0.0))

        # 4. Штраф за износ катализатора из политики (catalyst_wear_rub_h_per_c)
        cat_rate = getattr(pol, "catalyst_wear_rub_h_per_c", 450.0) if pol else 450.0
        tin = float(u_cand.get("HT_TIN_SP", ss.get("HT_T_IN", self.margin_model.rp.t_in_ref)))
        tin_hold = float(u_hold.get("HT_TIN_SP", ss_hold.get("HT_T_IN", self.margin_model.rp.t_in_ref)))
        t_bed = float(ss.get("HT_BED_MEAN", ss.get("HT_T_OUT", tin)))
        t_bed_hold = float(ss_hold.get("HT_BED_MEAN", ss_hold.get("HT_T_OUT", tin_hold)))
        delta_tbed = max(0.0, t_bed - t_bed_hold)
        # Корректировка износа катализатора с учетом параметра политики
        cat_adjustment = (cat_rate - self.margin_model.p.catalyst_rub_h_per_degC) * delta_tbed

        # 5. Стоимость перемещения исполнительных механизмов (move_cost)
        move_cost_rate = getattr(pol, "move_cost_rub_h_per_unit_norm", 0.0) if pol else 0.0
        norm_step = math.sqrt(sum(v ** 2 for v in u_cand.values())) if u_cand else 0.0
        move_cost = move_cost_rate * norm_step

        # Штраф за переочистку: 500 руб/ч на каждый ppm ниже границы ~8 ppm.
        # Удерживает серу в рабочем коридоре вместо падения к 6 ppm.
        s_hat = ss.get("HT_S_PRODUCT")
        giveaway_penalty = 0.0
        if s_hat is not None:
            s_limit = SULFUR_PRODUCT_MAX.hi or 10.0
            giveaway_boundary_ppm = s_limit - GIVEAWAY_Z * SIGMA_S0_PPM  # ~8 ppm
            giveaway_ppm = max(0.0, giveaway_boundary_ppm - float(s_hat))
            giveaway_penalty = 3000000.0 * giveaway_ppm

        net_utility = raw_margin + blend_utility - cat_adjustment - move_cost - giveaway_penalty
        return round(float(net_utility), 2)
