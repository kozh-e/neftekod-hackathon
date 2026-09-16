"""Цифровой двойник сквозной технологической цепочки ЭЛОУ-АВТ-6 -> 24-2000 (FullChainTwin).

Реализует гибридный grey-box двойник технологического поезда:
- FeedLink: динамика качества отбора сырья АВТ;
- ReactorKineticsCalculator: экзотерма и двухкомпонентная HDS кинетика Р-202;
- StabilizerColumnCalculator: температура вспышки ГО ДТ в К-201;
- FirstOrderDeadTime: динамические переходные процессы первого порядка с транспортным запаздыванием;
- Регламентная авторегрессионная коррекция расхождений (assimilate / bias update).
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.twin.params import TwinParams
from src.twin.tags import (
    DERIVED,
    NOMINAL_OPERATING_POINT,
    RHO_FEED_T_M3,
    fill_from_nominal,
    normalize_tags,
)
from src.twin.feed_link import FeedLink, FeedState
from src.twin.kinetics import ReactorInputs, ReactorKineticsCalculator
from src.twin.stabilizer import StabilizerColumnCalculator
from src.twin.product import product_properties
from src.twin.fopdt import FirstOrderDeadTime


MV_NAMES: Tuple[str, ...] = (
    "HT_FEED_SP",
    "HT_TIN_SP",
    "HT_P_SP",
    "HT_GOR_SP",
    "AVT_TFURN_DEV",
)

OUTPUTS: Tuple[str, ...] = (
    "AVT_DIESEL_TPH",
    "HT_FEED_TO_AVT",
    "HT_T95_FEED",
    "HT_S_FEED",
    "HT_T_IN",
    "HT_T_OUT",
    "HT_BED_MEAN",
    "HT_DP_KPA",
    "HT_GOR",
    "HT_VSG",
    "HT_S_PRODUCT",
    "HT_FLASH",
    "HT_D15_PRODUCT",
    "HT_T95_PRODUCT",
    "HT_CFPP_PRODUCT",
    "HT_CN_PRODUCT",
    "AVT_T55",
)

BIAS_SOURCES: Dict[str, Tuple[str, ...]] = {
    "HT_S_PRODUCT": ("LIMS_HT_S", "HT_Q21"),
    "HT_FLASH": ("LIMS_HT_FLASH", "HT_T18"),
    "HT_D15_PRODUCT": ("LIMS_HT_D15", "PAK_D15", "24-2000:GODT:D15"),
    "HT_T95_PRODUCT": ("LIMS_HT_T95", "24-2000:GODT:T95"),
    "HT_S_FEED": ("LIMS_HT_FEED_S", "HT_Q20"),
    "HT_T_OUT": ("HT_T11",),
    "HT_DP_KPA": ("HT_DP_KPA",),
}

REQUIRED_TAGS: Tuple[str, ...] = (
    "AVT_F30",
    "AVT_F32",
    "HT_F9",
    "HT_T6",
    "HT_T11",
    "HT_P13",
    "HT_F2",
    "HT_F14",
    "HT_P24",
    "HT_W7",
    "HT_Q21",
    "HT_Q20",
)


class FullChainTwin:
    """
    Полный цифровой двойник сквозного технологического поезда.
    """

    def __init__(self, params: TwinParams):
        self.params = params
        self.dt_min = params.dt_min

        # Подмодели
        self.feed_link = FeedLink(self.params.feed, dt_min=self.dt_min)
        self.kinetics = ReactorKineticsCalculator(self.params.reactor)
        self.stabilizer = StabilizerColumnCalculator(self.params.stabilizer)

        # Динамические звенья первого порядка с чистым запаздыванием (FOPDT)
        dyn = self.params.dynamics
        self.fopdt_t_in = FirstOrderDeadTime(dyn.tau_t_in, 0.0, self.dt_min, self.params.reactor.t_in_ref)
        self.fopdt_t_out = FirstOrderDeadTime(dyn.tau_t_out, 0.0, self.dt_min, self.params.reactor.t_in_ref + 0.53)
        self.fopdt_bed = FirstOrderDeadTime(dyn.tau_bed, 0.0, self.dt_min, self.params.reactor.t_in_ref + 0.26)
        self.fopdt_s = FirstOrderDeadTime(dyn.tau_s, dyn.theta_s, self.dt_min, self.params.reactor.s_out_ref)
        self.fopdt_dp = FirstOrderDeadTime(dyn.tau_dp, 0.0, self.dt_min, self.params.reactor.dp_ref_kpa)
        self.fopdt_flash = FirstOrderDeadTime(dyn.tau_flash, dyn.theta_flash, self.dt_min, self.params.stabilizer.flash_ref)
        self.fopdt_d15 = FirstOrderDeadTime(dyn.tau_product, dyn.theta_product, self.dt_min, self.params.feed.d15_ref - self.params.product.delta_d15_hdt)
        self.fopdt_t95 = FirstOrderDeadTime(dyn.tau_product, dyn.theta_product, self.dt_min, self.params.product.t95_feed_ref - self.params.product.delta_t95_hdt)
        self.fopdt_cfpp = FirstOrderDeadTime(dyn.tau_product, dyn.theta_product, self.dt_min, self.params.product.cfpp_ref)
        self.fopdt_cn = FirstOrderDeadTime(dyn.tau_product, dyn.theta_product, self.dt_min, self.params.product.cn_ref)
        self.fopdt_t55 = FirstOrderDeadTime(dyn.tau_t55, dyn.theta_t55, self.dt_min, 381.7)

        # Смещения авторегрессионной коррекции (bias)
        self.biases: Dict[str, float] = {k: 0.0 for k in BIAS_SOURCES}

        # Текущий рабочий режим и неконтролируемые возмущения
        self._u_current: Dict[str, float] = {
            "HT_FEED_SP": self.params.reactor.feed_ref,
            "HT_TIN_SP": self.params.reactor.t_in_ref,
            "HT_P_SP": self.params.reactor.p_ref,
            "HT_GOR_SP": self.params.reactor.gor_ref,
            "AVT_TFURN_DEV": 0.0,
        }
        self._disturbances: Dict[str, float] = {
            "AVT_F30": self.params.feed.f30_ref,
            "AVT_F32": self.params.feed.f32_ref,
            "HT_F14": self.params.reactor.quench_ref,
            "HT_P24": self.params.stabilizer.p24_ref,
            "HT_W7": self.params.stabilizer.w7_ref,
            "AVT_T55": 381.7,
        }

    @property
    def u_current(self) -> Dict[str, float]:
        return dict(self._u_current)

    @property
    def disturbances(self) -> Dict[str, float]:
        return dict(self._disturbances)

    def initialize(self, tags: Mapping[str, float]) -> List[str]:
        """
        Инициализирует двойник по входному срезу телеметрии.
        Устанавливает стационарное состояние всех фильтров под u_current и ассимилирует измерения.
        """
        norm_tags, norm_warnings = normalize_tags(tags)
        filled, fill_warnings = fill_from_nominal(norm_tags, REQUIRED_TAGS)
        warnings = norm_warnings + fill_warnings

        # Фиксируем возмущения
        self._disturbances["AVT_F30"] = filled.get("AVT_F30", self.params.feed.f30_ref)
        self._disturbances["AVT_F32"] = filled.get("AVT_F32", self.params.feed.f32_ref)
        self._disturbances["HT_F14"] = filled.get("HT_F14", self.params.reactor.quench_ref)
        self._disturbances["HT_P24"] = filled.get("HT_P24", self.params.stabilizer.p24_ref)
        self._disturbances["HT_W7"] = filled.get("HT_W7", self.params.stabilizer.w7_ref)
        self._disturbances["AVT_T55"] = filled.get("AVT_T55", 381.7)

        # Вычисляем u_current
        f9 = filled.get("HT_F9", self.params.reactor.feed_ref)
        t6 = filled.get("HT_T6", self.params.reactor.t_in_ref)
        p13 = filled.get("HT_P13", self.params.reactor.p_ref)
        gor = filled.get("HT_GOR")
        if gor is None or math.isnan(gor):
            f2 = filled.get("HT_F2", 93309.0)
            gor = f2 / max(f9 / RHO_FEED_T_M3, 1e-4)

        self._u_current = {
            "HT_FEED_SP": f9,
            "HT_TIN_SP": t6,
            "HT_P_SP": p13,
            "HT_GOR_SP": gor,
            "AVT_TFURN_DEV": 0.0,
        }

        # Обнуляем смещения перед расчетом чистой модели
        self.biases = {k: 0.0 for k in BIAS_SOURCES}

        # Сброс подмоделей к стационарному состоянию
        self.feed_link.reset(self._disturbances["AVT_F30"], self._disturbances["AVT_F32"], 0.0)
        ss = self.steady_state(self._u_current)

        self.fopdt_t_in.reset(self._u_current["HT_TIN_SP"])
        self.fopdt_t_out.reset(ss["HT_T_OUT"])
        self.fopdt_bed.reset(ss["HT_BED_MEAN"])
        self.fopdt_s.reset(ss["HT_S_PRODUCT"])
        self.fopdt_dp.reset(ss["HT_DP_KPA"])
        self.fopdt_flash.reset(ss["HT_FLASH"])
        self.fopdt_d15.reset(ss["HT_D15_PRODUCT"])
        self.fopdt_t95.reset(ss["HT_T95_PRODUCT"])
        self.fopdt_cfpp.reset(ss["HT_CFPP_PRODUCT"])
        self.fopdt_cn.reset(ss["HT_CN_PRODUCT"])
        self.fopdt_t55.reset(self._disturbances["AVT_T55"])

        # Ассимилируем доступные измерения для вычисления bias
        self.assimilate(filled)

        return warnings

    def steady_state(self, u_abs: Mapping[str, float]) -> Dict[str, float]:
        """
        Расчет равновесного статического отклика цепочки на уставки u_abs.
        """
        u_feed = float(u_abs.get("HT_FEED_SP", self._u_current["HT_FEED_SP"]))
        u_tin = float(u_abs.get("HT_TIN_SP", self._u_current["HT_TIN_SP"]))
        u_p = float(u_abs.get("HT_P_SP", self._u_current["HT_P_SP"]))
        u_gor = float(u_abs.get("HT_GOR_SP", self._u_current["HT_GOR_SP"]))
        u_tfurn = float(u_abs.get("AVT_TFURN_DEV", self._u_current["AVT_TFURN_DEV"]))

        f30 = self._disturbances.get("AVT_F30", self.params.feed.f30_ref)
        f32 = self._disturbances.get("AVT_F32", self.params.feed.f32_ref)
        f14 = self._disturbances.get("HT_F14", self.params.reactor.quench_ref)
        p24 = self._disturbances.get("HT_P24", self.params.stabilizer.p24_ref)
        w7 = self._disturbances.get("HT_W7", self.params.stabilizer.w7_ref)
        t55 = self._disturbances.get("AVT_T55", 381.7)

        feed_ss = self.feed_link.steady_state(f30, f32, u_tfurn)

        rx_inp = ReactorInputs(
            t_in_c=u_tin,
            feed_tph=u_feed,
            p_mpa=u_p,
            gor_nm3m3=u_gor,
            s_feed_ppm=feed_ss.s_feed_ppm,
            t95_feed_c=feed_ss.t95_feed_c,
            d15_feed=feed_ss.d15_feed,
            quench_tph=f14,
        )
        rx_ss = self.kinetics.evaluate(rx_inp)

        flash_ss = self.stabilizer.evaluate(u_feed, p24, w7)
        prod_ss = product_properties(feed_ss, self.params.product)

        feed_to_avt = u_feed / max(feed_ss.avt_diesel_tph, 1e-3)

        return {
            "AVT_DIESEL_TPH": feed_ss.avt_diesel_tph,
            "HT_FEED_TO_AVT": feed_to_avt,
            "HT_T95_FEED": feed_ss.t95_feed_c,
            "HT_S_FEED": feed_ss.s_feed_ppm + self.biases.get("HT_S_FEED", 0.0),
            "HT_T_IN": u_tin,
            "HT_T_OUT": rx_ss.t_out_c + self.biases.get("HT_T_OUT", 0.0),
            "HT_BED_MEAN": rx_ss.bed_mean_c,
            "HT_DP_KPA": rx_ss.dp_kpa + self.biases.get("HT_DP_KPA", 0.0),
            "HT_GOR": u_gor,
            "HT_VSG": rx_ss.vsg_nm3h,
            "HT_S_PRODUCT": rx_ss.s_out_ppm + self.biases.get("HT_S_PRODUCT", 0.0),
            "HT_FLASH": flash_ss + self.biases.get("HT_FLASH", 0.0),
            "HT_D15_PRODUCT": prod_ss["HT_D15_PRODUCT"] + self.biases.get("HT_D15_PRODUCT", 0.0),
            "HT_T95_PRODUCT": prod_ss["HT_T95_PRODUCT"] + self.biases.get("HT_T95_PRODUCT", 0.0),
            "HT_CFPP_PRODUCT": prod_ss["HT_CFPP_PRODUCT"],
            "HT_CN_PRODUCT": prod_ss["HT_CN_PRODUCT"],
            "AVT_T55": t55,
        }

    def step(self, u_abs: Mapping[str, float]) -> Dict[str, float]:
        """
        Один шаг динамической симуляции комплекса (dt_min).
        """
        u_feed = float(u_abs.get("HT_FEED_SP", self._u_current["HT_FEED_SP"]))
        u_tin = float(u_abs.get("HT_TIN_SP", self._u_current["HT_TIN_SP"]))
        u_p = float(u_abs.get("HT_P_SP", self._u_current["HT_P_SP"]))
        u_gor = float(u_abs.get("HT_GOR_SP", self._u_current["HT_GOR_SP"]))
        u_tfurn = float(u_abs.get("AVT_TFURN_DEV", self._u_current["AVT_TFURN_DEV"]))

        f30 = self._disturbances.get("AVT_F30", self.params.feed.f30_ref)
        f32 = self._disturbances.get("AVT_F32", self.params.feed.f32_ref)
        f14 = self._disturbances.get("HT_F14", self.params.reactor.quench_ref)
        p24 = self._disturbances.get("HT_P24", self.params.stabilizer.p24_ref)
        w7 = self._disturbances.get("HT_W7", self.params.stabilizer.w7_ref)
        t55_meas = self._disturbances.get("AVT_T55", 381.7)

        # 1. Сырьевая связь
        feed_dyn = self.feed_link.step(f30, f32, u_tfurn)

        # 2. Температура входа
        t_in = self.fopdt_t_in.step(u_tin)

        # 3. Статика и динамика реактора
        rx_inp = ReactorInputs(
            t_in_c=t_in,
            feed_tph=u_feed,
            p_mpa=u_p,
            gor_nm3m3=u_gor,
            s_feed_ppm=feed_dyn.s_feed_ppm,
            t95_feed_c=feed_dyn.t95_feed_c,
            d15_feed=feed_dyn.d15_feed,
            quench_tph=f14,
        )
        rx_out = self.kinetics.evaluate(rx_inp)

        t_out = self.fopdt_t_out.step(rx_out.t_out_c)
        bed_mean = self.fopdt_bed.step(rx_out.bed_mean_c)
        s_product = self.fopdt_s.step(rx_out.s_out_ppm)
        dp = self.fopdt_dp.step(rx_out.dp_kpa)

        # 4. Стабилизатор вспышки
        flash_ss = self.stabilizer.evaluate(u_feed, p24, w7)
        flash = self.fopdt_flash.step(flash_ss)

        # 5. Свойства продукта
        prod_ss = product_properties(feed_dyn, self.params.product)
        d15 = self.fopdt_d15.step(prod_ss["HT_D15_PRODUCT"])
        t95 = self.fopdt_t95.step(prod_ss["HT_T95_PRODUCT"])
        cfpp = self.fopdt_cfpp.step(prod_ss["HT_CFPP_PRODUCT"])
        cn = self.fopdt_cn.step(prod_ss["HT_CN_PRODUCT"])

        # 6. Вспомогательные теги
        feed_to_avt = u_feed / max(feed_dyn.avt_diesel_tph, 1e-3)
        t55 = self.fopdt_t55.step(t55_meas)

        # 7. Добавление смещений авторегрессии (biases)
        return {
            "AVT_DIESEL_TPH": feed_dyn.avt_diesel_tph,
            "HT_FEED_TO_AVT": feed_to_avt,
            "HT_T95_FEED": feed_dyn.t95_feed_c,
            "HT_S_FEED": feed_dyn.s_feed_ppm + self.biases.get("HT_S_FEED", 0.0),
            "HT_T_IN": t_in,
            "HT_T_OUT": t_out + self.biases.get("HT_T_OUT", 0.0),
            "HT_BED_MEAN": bed_mean,
            "HT_DP_KPA": dp + self.biases.get("HT_DP_KPA", 0.0),
            "HT_GOR": u_gor,
            "HT_VSG": rx_out.vsg_nm3h,
            "HT_S_PRODUCT": s_product + self.biases.get("HT_S_PRODUCT", 0.0),
            "HT_FLASH": flash + self.biases.get("HT_FLASH", 0.0),
            "HT_D15_PRODUCT": d15 + self.biases.get("HT_D15_PRODUCT", 0.0),
            "HT_T95_PRODUCT": t95 + self.biases.get("HT_T95_PRODUCT", 0.0),
            "HT_CFPP_PRODUCT": cfpp,
            "HT_CN_PRODUCT": cn,
            "AVT_T55": t55,
        }

    def predict(self, u_abs: Mapping[str, float], horizon: int) -> Dict[str, List[float]]:
        """
        Прогнозирование многомерной траектории на H шагов вперед без изменения текущего состояния.
        """
        twin_copy = self.clone()
        trajectories: Dict[str, List[float]] = {out: [] for out in OUTPUTS}

        for _ in range(horizon):
            step_res = twin_copy.step(u_abs)
            for k in OUTPUTS:
                trajectories[k].append(step_res[k])

        return trajectories

    def assimilate(self, measured: Mapping[str, float]) -> Dict[str, float]:
        """
        Оценивает расхождение модели с измерениями (bias_i = y_meas - y_model)
        по приоритетным источникам ТЗ: LIMS -> ПАК/онлайн -> ВАК.
        """
        # Сначала получаем текущие несмещенные выходы модели
        raw_s = self.fopdt_s.value
        raw_flash = self.fopdt_flash.value
        raw_d15 = self.fopdt_d15.value
        raw_t95 = self.fopdt_t95.value
        raw_dp = self.fopdt_dp.value
        raw_tout = self.fopdt_t_out.value
        raw_s_feed = self.feed_link.steady_state(
            self._disturbances.get("AVT_F30", self.params.feed.f30_ref),
            self._disturbances.get("AVT_F32", self.params.feed.f32_ref),
            0.0,
        ).s_feed_ppm

        model_vals = {
            "HT_S_PRODUCT": raw_s,
            "HT_FLASH": raw_flash,
            "HT_D15_PRODUCT": raw_d15,
            "HT_T95_PRODUCT": raw_t95,
            "HT_S_FEED": raw_s_feed,
            "HT_T_OUT": raw_tout,
            "HT_DP_KPA": raw_dp,
        }

        for target_key, sources in BIAS_SOURCES.items():
            for src in sources:
                val = measured.get(src)
                if val is not None and not (math.isnan(val) or math.isinf(val)):
                    # Q20 переводится в базис ЛИМС
                    if src == "HT_Q20":
                        val = val * self.params.feed.q20_to_lims
                    model_base = model_vals.get(target_key, 0.0)
                    self.biases[target_key] = val - model_base
                    break

        return dict(self.biases)

    def clone(self) -> FullChainTwin:
        """Создает изолированную глубокую копию двойника."""
        return copy.deepcopy(self)
