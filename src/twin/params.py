"""Параметры цифрового двойника технологической цепочки и экономико-технологических моделей.

Определяет датаклассы с метаданными источника (NORM / REGISTRY / DATA / ASSUMPTION)
и функцию загрузки калибровочных параметров load_params().
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)


def P(default: Any, source: str, note: str = "") -> Any:
    """Хелпер создания поля с метаданными происхождения."""
    return field(default=default, metadata={"source": source, "note": note})


@dataclass
class FeedLinkParams:
    mode: Literal["buffered", "hot"] = P("buffered", "ASSUMPTION", "Режим связи: buffered (буферная емкость) или hot (горячая струя)")
    theta_min: float = P(10.0, "ASSUMPTION", "Чистое транспортное запаздывание сырья от АВТ к ГО, мин")
    tau_mix_min: float = P(120.0, "ASSUMPTION", "Постоянная времени идеального смешения в сырьевом парке, мин")
    feed_to_avt_bounds: tuple[float, float] = P((0.81, 1.26), "DATA", "Допустимый диапазон F9/(F30+F32) q05-q95")
    t95_ref: float = P(353.0, "DATA", "Базовая температура T95 сырья ГО (ЛИМС ГО т.1), °C")
    dT95_dF30: float = P(0.0691, "DATA", "Чувствительность T95 сырья ГО к отбору фр. 290-350 (AVT_F30), "
                                         "°C/(т/ч): МНК по лабораторным пробам сырья с контролем F32 и F65, "
                                         "Train <= 2025-06-30; по годам +0.025…+0.233, знак устойчив. "
                                         "Прежнее значение 2.66463 было взято из коэффициента при F30 в "
                                         "формуле ВАК AVT6:240-350:EBP — худшей в наборе (MAE ~70 °C при "
                                         "величине 363 °C) — и завышало эффект примерно в 39 раз")
    f30_ref: float = P(128.3, "DATA", "Номинальный расход тяжелой дизельной фр. 290-350, т/ч")
    f32_ref: float = P(81.6, "DATA", "Номинальный расход легкой дизельной фр. 240-290, т/ч")
    s_ref: float = P(9470.0, "DATA", "Базовая сера сырья ГО (ЛИМС ГО т.1), ppm")
    s_t95: float = P(0.01, "ASSUMPTION", "Относительный прирост серы сырья на 1 °C утяжеления T95, 1/°C")
    d15_ref: float = P(847.2, "DATA", "Базовая плотность сырья ГО (ЛИМС ГО т.1 D15), кг/м3")
    d_t95: float = P(0.3, "ASSUMPTION", "Прирост плотности сырья на 1 °C утяжеления T95, кг/м3 на °C")
    dF30_dT55: float = P(0.068, "ASSUMPTION", "Отклик отбора фр. 290–350 (AVT_F30) на T55, т/ч на °C: МНК по всему архиву (поправка на F65, F31); неустойчив по подпериодам −0.28…+1.57 (scripts/estimate_quality_uncertainty.py)")
    dF32_dT55: float = P(0.191, "ASSUMPTION", "Отклик отбора фр. 240–290 (AVT_F32) на T55, т/ч на °C: МНК по всему архиву; неустойчив по подпериодам −0.29…+0.20")
    q20_to_lims: float = P(1.0 / 0.878, "DATA", "Пересчет онлайн-серы сырья Q20 в базис ЛИМС т.1")


@dataclass
class ReactorParams:
    t_in_ref: float = P(363.3, "DATA", "Номинальная температура входа в Р-202 (HT_T6), °C")
    feed_ref: float = P(219.6, "DATA", "Номинальный расход сырья (HT_F9), т/ч")
    p_ref: float = P(3.922, "DATA", "Номинальное давление на входе в Р-202 (HT_P13), МПа")
    gor_ref: float = P(360.0, "DATA", "Номинальное соотношение ВСГ/сырье, нм3/м3")
    quench_ref: float = P(6.05, "DATA", "Номинальный расход квенча в Р-202 (HT_F14), т/ч")
    s_feed_ref: float = P(9470.0, "DATA", "Номинальная сера сырья Р-202, ppm")
    s_out_ref: float = P(8.6, "DATA", "Номинальная сера продукта на выходе (ЛИМС т.2), ppm")
    c0: float = P(8.194, "DATA", "Константа регрессии экзотермы T_out - T_in, °C")
    cF: float = P(-0.032, "DATA", "Коэффициент влияния F9 на экзотерму, °C/(т/ч)")
    cS: float = P(-0.069, "DATA", "Коэффициент влияния серы сырья на экзотерму, °C/(1000 ppm)")
    cQ: float = P(-0.012, "DATA", "Коэффициент влияния квенча F14 на экзотерму, °C/(т/ч)")
    E_e_R: float = P(11000.0, "ASSUMPTION", "Энергия активации легкоудаляемой серы E_e/R, K")
    E_h_R: float = P(14000.0, "ASSUMPTION", "Энергия активации трудноудаляемой серы E_h/R, K")
    k_e_ref: float = P(12.0, "ASSUMPTION", "Безразмерная константа скорости легкой фракции серы при LHSV_ref")
    f_h_ref: float = P(0.004, "ASSUMPTION", "Базовая доля трудноудаляемой серы в сырье")
    b_t95: float = P(0.03, "ASSUMPTION", "Чувствительность доли трудноудаляемой серы к T95 сырья, 1/°C")
    alpha_P: float = P(1.0, "ASSUMPTION", "Степень влияния давления на скорость HDS")
    alpha_G: float = P(0.3, "ASSUMPTION", "Степень влияния кратности ВСГ/сырье на скорость HDS")
    dp_ref_kpa: float = P(177.0, "DATA", "Номинальный перепад давления на реакторе Р-202, кПа")
    n_dp: float = P(1.8, "ASSUMPTION", "Степень расхода в уравнении Эргуна для перепада давления")
    g_gas: float = P(0.5, "ASSUMPTION", "Вес газовой фазы в перепаде давления")
    rho_feed_t_m3: float = P(0.847, "DATA", "Плотность сырья ГО при рабочих условиях, т/м3")


@dataclass
class StabilizerParams:
    flash_ref: float = P(68.273, "DATA", "Базовая температура вспышки ГО ДТ (ЛИМС), °C")
    f9_ref: float = P(216.6655, "DATA", "Номинальный расход сырья установки, т/ч")
    p24_ref: float = P(0.5847, "DATA", "Номинальное давление на выходе К-201, МПа")
    w7_ref: float = P(0.1719, "DATA", "Номинальный расход газа поддува К-201, т/ч")
    t18_ref: float = P(69.315, "DATA", "Номинальное показание APC-анализатора вспышки HT_T18, °C")
    a_T18: float = P(0.3571, "DATA", "Вес показания APC-анализатора HT_T18: оценён на остатке "
                                     "режимной модели, применяется только там, где HT_T18 "
                                     "является фактическим измерением (оценщик, реплей)")
    a_F: float = P(-0.1407, "DATA", "Чувствительность вспышки к нагрузке F9, °C/(т/ч)")
    a_P: float = P(-13.701, "DATA", "Чувствительность вспышки к давлению К-201 P24, °C/МПа")
    a_W: float = P(3.586, "DATA", "Чувствительность вспышки к газу поддува W7, °C/(т/ч)")


@dataclass
class ProductParams:
    t95_feed_ref: float = P(353.0, "DATA", "Базовая температура T95 сырья ГО, °C")
    delta_d15_hdt: float = P(11.1, "DATA", "Снижение плотности D15 в процессе гидроочистки, кг/м3")
    delta_t95_hdt: float = P(6.0, "DATA", "Снижение T95 в процессе гидроочистки, °C")
    cfpp_ref: float = P(-6.0, "DATA", "Базовая ПТФ гидрогенизата ГО ДТ (ЛИМС), °C")
    dcfpp_dt95: float = P(0.3, "ASSUMPTION", "Чувствительность ПТФ гидрогенизата к утяжелению T95, °C/°C")
    cn_ref: float = P(53.75, "DATA", "Базовое цетановое число гидрогенизата (ЛИМС т.2, n=42)")
    dcn_dt95: float = P(0.05, "ASSUMPTION", "Чувствительность ЦЧ к изменению фракционного состава")


@dataclass
class BlendParams:
    batch_t: float = P(2000.0, "ASSUMPTION", "Размер партии товарного дизельного топлива, т")
    target_cfpp: float = P(-15.0, "NORM", "Целевая ПТФ для зимнего сорта E по ГОСТ 32511, °C")
    target_flash: float = P(56.0, "NORM", "Минимальная температура вспышки с учетом запаса 1.0 °C, °C")
    # Цены компонентов блендинга в резервуарном парке (СПбМТСБ / оптовый рынок 2024-2026)
    price_godt: float = P(68000.0, "ASSUMPTION", "Себестоимость дизельного гидрогенизата в резервуаре, руб/т")
    price_kerosene: float = P(88000.0, "ASSUMPTION", "Стоимость керосиновой фракции ТС-1, руб/т")
    price_gasoil: float = P(52000.0, "ASSUMPTION", "Стоимость гидроочищенного газойля, руб/т")
    # Параметры присадок (депрессорной ДДП A и цетаноповышающей B)
    additive_a_seg1_max_kg_t: float = P(0.5, "ASSUMPTION", "Максимальная дозировка сегмента 1 присадки A, кг/т (500 ppm)")
    additive_a_seg1_eff: float = P(12.0, "ASSUMPTION", "Эффект депрессии сегмента 1 присадки A, °C на кг/т")
    additive_a_seg2_max_kg_t: float = P(1.0, "ASSUMPTION", "Максимальная дозировка сегмента 2 присадки A, кг/т (1000 ppm)")
    additive_a_seg2_eff: float = P(3.76, "ASSUMPTION", "Эффект депрессии сегмента 2 присадки A, °C на кг/т")
    additive_a_price_rub_t: float = P(450000.0, "ASSUMPTION", "Цена присадки A (ДДП оптом), руб/т")

    additive_b_seg1_max_kg_t: float = P(0.5, "ASSUMPTION", "Максимальная дозировка сегмента 1 присадки B, кг/т")
    additive_b_seg1_eff: float = P(6.0, "ASSUMPTION", "Прирост ЦЧ от сегмента 1 присадки B, ЦЧ на кг/т")
    additive_b_seg2_max_kg_t: float = P(1.0, "ASSUMPTION", "Максимальная дозировка сегмента 2 присадки B, кг/т")
    additive_b_seg2_eff: float = P(2.0, "ASSUMPTION", "Прирост ЦЧ от сегмента 2 присадки B, ЦЧ на кг/т")
    additive_b_price_rub_t: float = P(260000.0, "ASSUMPTION", "Цена цетаноповышающей присадки B (2-EHN оптом), руб/т")


@dataclass
class EconomicsParams:
    # 1. Сырье и промежуточные дистилляты (СПбМТСБ / Минфин РФ 2024-2026)
    price_crude_oil: float = P(41500.0, "ASSUMPTION", "Рыночная стоимость сырой нефти Urals на входе в ЭЛОУ-АВТ-6 (СПбМТСБ/Минфин), руб/т")
    price_straight_run: float = P(52000.0, "ASSUMPTION", "Альтернативная стоимость прямогонного сырья ГО (смесь фракций F30+F32), руб/т")
    price_godt: float = P(68000.0, "ASSUMPTION", "Рыночная оптовая стоимость стабильного гидроочищенного дизеля ГО ДТ Евро-5 (СПбМТСБ), руб/т")
    price_kerosene: float = P(88000.0, "ASSUMPTION", "Рыночная стоимость гидроочищенного авиакеросина ТС-1 (СПбМТСБ), руб/т")
    price_gasoil: float = P(52000.0, "ASSUMPTION", "Рыночная стоимость тяжелого гидроочищенного/вакуумного газойля, руб/т")
    y_liq: float = P(0.98, "ASSUMPTION", "Выход жидкого стабильного гидрогенизата в секции 24-2000, доли")

    # 2. Топливный газ технологических печей (тарифы ФАС РФ 2024-2026)
    fuel_gas_price_rub_1000nm3: float = P(7800.0, "ASSUMPTION", "Стоимость топливного газа заводской сети с учетом тарифов ФАС и транспорта, руб/1000 нм3")
    fuel_gas_lhv_mj_nm3: float = P(35.8, "ASSUMPTION", "Низшая теплота сгорания заводского топливного газа, МДж/нм3")
    fuel_rub_mwh: float = P(2700.0, "ASSUMPTION", "Полная себестоимость тепловой энергии печей (топливо + эксплуатация сети), руб/МВт*ч")
    eta_furnace: float = P(0.85, "ASSUMPTION", "Эксплуатационный КПД трубчатых печей подогрева сырья")
    cp_oil: float = P(2.8, "ASSUMPTION", "Изобарная теплоемкость углеводородного сырья при 350-380 °C, кДж/(кг*К)")

    # 3. Электроэнергия, компримирование и водород (ОРЭМ АТС / промышленный тариф)
    power_rub_kwh: float = P(6.80, "ASSUMPTION", "Тариф на промышленную электроэнергию НПЗ (ОРЭМ 1-я ЦЗ + сетевой тариф ВН), руб/кВт*ч")
    compressor_rub_per_nm3: float = P(0.34, "ASSUMPTION", "Удельные затраты электроэнергии на компримирование циркулирующего ВСГ компрессором ЦК-201, руб/нм3")
    pressure_rub_h_per_mpa: float = P(22000.0, "ASSUMPTION", "Стоимость поддержания повышенного системного давления свежим ВСГ, руб/(ч*МПа)")
    h2_rub_per_nm3: float = P(16.50, "ASSUMPTION", "Себестоимость свежего водорода 99.9% с установки КЦА (паровая конверсия метана), руб/нм3")
    f25_ref: float = P(13199.0, "DATA", "Номинальный расход водорода КЦА, нм3/ч")
    f31_ref: float = P(540.7, "DATA", "Медианный расход нефтепродукта через печь П-3 (AVT_F31, т/ч) — база затрат топлива на нагрев")

    # 4. Катализатор и арбитраж
    catalyst_rub_h_per_degC: float = P(450.0, "ASSUMPTION", "Стоимость ускоренной термической дезактивации Co-Mo/Ni-Mo катализатора при росте T_bed, руб/(ч*°C)")
    min_margin_improvement: float = P(1000.0, "ASSUMPTION", "Порог экономической зоны нечувствительности (Deadband), руб/ч")

    def calc_fuel_gas_consumption_nm3(self, heat_mwh: float) -> float:
        """Рассчитывает физический расход топливного газа (нм3) по тепловой нагрузке (МВт*ч)."""
        heat_mj = heat_mwh * 3600.0
        lhv = max(self.fuel_gas_lhv_mj_nm3, 1.0)
        return heat_mj / lhv

    @property
    def crude_to_straight_spread(self) -> float:
        """Спред переработки сырой нефти в прямогонный дистиллят (руб/т)."""
        return self.price_straight_run - self.price_crude_oil

    @property
    def straight_to_godt_spread(self) -> float:
        """Спред гидроочистки прямогонного дистиллята в ГО ДТ (руб/т)."""
        return self.price_godt - self.price_straight_run

    @property
    def crude_to_godt_spread(self) -> float:
        """Сквозной спред переработки нефти в товарный ГО ДТ с учетом выхода (руб/т)."""
        return (self.price_godt * self.y_liq) - self.price_crude_oil

    @property
    def margin_spread(self) -> float:
        """Маржинальный спред гидроочистки (цена ГО ДТ минус сырье прямогон), руб/т."""
        return self.price_godt - self.price_straight_run

    # Алиасы наименований для обратной совместимости
    @property
    def product_diesel_rub_ton(self) -> float:
        return self.price_godt

    @property
    def straight_run_diesel_rub_ton(self) -> float:
        return self.price_straight_run

    @property
    def crude_oil_rub_ton(self) -> float:
        return self.price_crude_oil



@dataclass
class DynamicsParams:
    tau_t_in: float = P(15.0, "ASSUMPTION", "Постоянная времени прогрева печи на входе Р-202, мин")
    tau_t_out: float = P(20.0, "ASSUMPTION", "Постоянная времени температуры выхода Р-202, мин")
    tau_bed: float = P(20.0, "ASSUMPTION", "Постоянная времени средней температуры слоя Р-202, мин")
    tau_s: float = P(40.0, "ASSUMPTION", "Постоянная времени HDS-реакции по сере, мин")
    theta_s: float = P(10.0, "ASSUMPTION", "Чистое запаздывание кинетики серы, мин")
    tau_dp: float = P(6.0, "ASSUMPTION", "Постоянная времени перепада давления реактора, мин")
    tau_flash: float = P(30.0, "ASSUMPTION", "Постоянная времени температуры вспышки в стабилизаторе, мин")
    theta_flash: float = P(10.0, "ASSUMPTION", "Чистое запаздывание температуры вспышки, мин")
    tau_product: float = P(40.0, "ASSUMPTION", "Постоянная времени свойств продукта (D15/T95/CFPP/CN), мин")
    theta_product: float = P(10.0, "ASSUMPTION", "Чистое запаздывание свойств продукта, мин")
    tau_t55: float = P(18.0, "ASSUMPTION", "Постоянная времени печи П-3 (мониторинг), мин")
    theta_t55: float = P(10.0, "ASSUMPTION", "Чистое запаздывание печи П-3, мин")


@dataclass
class TwinParams:
    feed: FeedLinkParams = field(default_factory=FeedLinkParams)
    reactor: ReactorParams = field(default_factory=ReactorParams)
    stabilizer: StabilizerParams = field(default_factory=StabilizerParams)
    product: ProductParams = field(default_factory=ProductParams)
    blend: BlendParams = field(default_factory=BlendParams)
    economics: EconomicsParams = field(default_factory=EconomicsParams)
    dynamics: DynamicsParams = field(default_factory=DynamicsParams)
    dt_min: float = 10.0

    def assumptions(self) -> list[str]:
        """Собирает список параметров с меткой ASSUMPTION для вывода в XAI."""
        res: list[str] = []
        for sub_name in ("feed", "reactor", "stabilizer", "product", "blend", "economics", "dynamics"):
            sub_obj = getattr(self, sub_name)
            for f in fields(sub_obj):
                meta = f.metadata
                if meta.get("source") == "ASSUMPTION":
                    val = getattr(sub_obj, f.name)
                    note = meta.get("note", "")
                    res.append(f"{sub_name}.{f.name} = {val} ({note})")
        return res


def load_params(path: str | Path = "config/twin_params.json") -> TwinParams:
    """
    Загружает параметры цифрового двойника.
    При наличии файла перекрывает параметры по умолчанию данными из JSON (получившими статус DATA).
    """
    params = TwinParams()
    p_path = Path(path)
    if not p_path.exists():
        return params

    try:
        with open(p_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for sub_name in ("feed", "reactor", "stabilizer", "product", "blend", "economics", "dynamics"):
            if sub_name in data and isinstance(data[sub_name], dict):
                sub_obj = getattr(params, sub_name)
                sub_dict = data[sub_name]
                for k, v in sub_dict.items():
                    if hasattr(sub_obj, k):
                        setattr(sub_obj, k, v)
        if "dt_min" in data:
            params.dt_min = float(data["dt_min"])
    except Exception:
        logger.exception("Не удалось разобрать %s, используются номинальные параметры ASSUMPTION", p_path)

    return params
