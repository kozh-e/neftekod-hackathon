"""Модуль кинетики реактора гидроочистки Р-202 (24-2000).

Реализует двухкомпонентную кинетику псевдопервого порядка (easy + refractory HDS, ADR-2):
- Термический баланс и расчет экзотермы Delta_T = T_out - T_in;
- Зависимость скорости гидродесульфуризации от температуры слоя (Аррениус), давления и ВСГ/сырье;
- Нелинейная модель перепада давления по модифицированному уравнению Эргуна.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.twin.params import ReactorParams


@dataclass(frozen=True)
class ReactorInputs:
    t_in_c: float
    feed_tph: float
    p_mpa: float
    gor_nm3m3: float
    s_feed_ppm: float
    t95_feed_c: float
    d15_feed: float
    quench_tph: float


@dataclass(frozen=True)
class ReactorOutputs:
    t_out_c: float
    bed_mean_c: float
    lhsv_rel: float
    vsg_nm3h: float
    f_refractory: float
    s_out_ppm: float
    dp_kpa: float


class ReactorKineticsCalculator:
    """
    Расчет статического отклика реактора гидроочистки Р-202.
    """

    def __init__(self, p: ReactorParams):
        self.p = p

        # Базовый объемный расход сырья и ВСГ в номинале
        # feed_ref (т/ч) / (rho_feed (т/м3)) -> м3/ч
        self.v_ref = self.p.feed_ref / max(self.p.rho_feed_t_m3, 1e-4)
        self.f2_ref = self.p.gor_ref * self.v_ref

        # Оценка температуры выхода и слоя в номинальной точке
        t_out_nom = (
            self.p.t_in_ref
            + self.p.c0
            + self.p.cF * self.p.feed_ref
            + self.p.cS * (0.878 * self.p.s_feed_ref) / 1000.0
            + self.p.cQ * self.p.quench_ref
        )
        self.t_bed_ref = (self.p.t_in_ref + t_out_nom) / 2.0

        # Точный аналитический якорь k_h_ref для получения s_out_ref в номинале
        e_term = (1.0 - self.p.f_h_ref) * self.p.s_feed_ref * math.exp(-self.p.k_e_ref)
        denom = max(self.p.s_out_ref - e_term, 1e-6)
        self.k_h_ref = math.log((self.p.f_h_ref * self.p.s_feed_ref) / denom)

    def evaluate(self, x: ReactorInputs) -> ReactorOutputs:
        """
        Статический расчет показателей реактора Р-202.
        Гарантирует конечные и физичные значения при любых граничных входах.
        """
        feed = max(1e-4, x.feed_tph)
        d15_t_m3 = max(0.5, x.d15_feed / 1000.0) if x.d15_feed > 100.0 else max(0.5, x.d15_feed)
        v = feed / d15_t_m3  # м3/ч
        lhsv_rel = max(1e-4, v / max(self.v_ref, 1e-4))

        gor = max(0.0, x.gor_nm3m3)
        vsg = gor * v  # нм3/ч

        # 1. Экзотерма и температуры выхода и слоя
        s_feed = max(0.0, x.s_feed_ppm)
        quench = max(0.0, x.quench_tph)
        delta_t = (
            self.p.c0
            + self.p.cF * feed
            + self.p.cS * (0.878 * s_feed) / 1000.0
            + self.p.cQ * quench
        )
        t_out = x.t_in_c + delta_t
        bed_mean = (x.t_in_c + t_out) / 2.0

        # 2. Поправки на системное давление и кратность ВСГ/сырье
        p_safe = max(1e-3, x.p_mpa)
        phi_p = (p_safe / self.p.p_ref) ** self.p.alpha_P
        phi_g = (max(gor, 1e-3) / self.p.gor_ref) ** self.p.alpha_G

        # 3. Температурная зависимость констант скоростей (Аррениус)
        t_bed_k = bed_mean + 273.15
        t_bed_ref_k = self.t_bed_ref + 273.15
        inv_diff = (1.0 / t_bed_k) - (1.0 / t_bed_ref_k)

        exp_e = math.exp(-self.p.E_e_R * inv_diff)
        exp_h = math.exp(-self.p.E_h_R * inv_diff)

        k_e = self.p.k_e_ref * exp_e * phi_p * phi_g
        k_h = self.k_h_ref * exp_h * phi_p * phi_g

        # 4. Доля трудноудаляемых сероорганических соединений
        t95_ref = 353.0
        f_h_raw = self.p.f_h_ref * math.exp(self.p.b_t95 * (x.t95_feed_c - t95_ref))
        f_h = max(0.0, min(0.05, f_h_raw))

        # 5. Выходное содержание серы
        term_easy = (1.0 - f_h) * math.exp(-k_e / lhsv_rel)
        term_hard = f_h * math.exp(-k_h / lhsv_rel)
        s_out = s_feed * (term_easy + term_hard)
        s_out = max(0.0, s_out)

        # 6. Перепад давления на катализаторе (модифицированное уравнение Эргуна)
        term_liq = (1.0 - self.p.g_gas) * (lhsv_rel ** self.p.n_dp)
        term_gas = self.p.g_gas * ((vsg / max(self.f2_ref, 1e-3)) ** self.p.n_dp)
        dp = self.p.dp_ref_kpa * (term_liq + term_gas)
        dp = max(0.0, dp)

        return ReactorOutputs(
            t_out_c=t_out,
            bed_mean_c=bed_mean,
            lhsv_rel=lhsv_rel,
            vsg_nm3h=vsg,
            f_refractory=f_h,
            s_out_ppm=s_out,
            dp_kpa=dp,
        )
