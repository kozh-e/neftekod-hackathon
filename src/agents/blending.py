"""Модуль оптимизации блендинга товарного дизельного топлива (Fuel Blending Optimizer).

Реализует линейное программирование (SciPy HiGHS LP) блендинга из резервуаров (ADR-6):
- 3 компонента (ГО ДТ, керосин ТС-1, вакуумный/гидроочищенный газойль) с учетом остатков в резервуарах;
- 2 присадки: депрессорно-диспергирующая (ДДП, сегменты Ленгмюра) и цетаноповышающая (ЦЧ);
- Ограничения ГОСТ 32511-2013 (Евро-5): сера по массе, плотность, вспышка (индекс FBI), ПТФ, T95, ЦЧ;
- Жесткая блокировка «Dilution Loophole» (запрет разбавления некондиционного гидрогенизата);
- Обратная совместимость через solve_recipe().
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field
from scipy.optimize import linprog

from src.agents.state import MasGraphState
from src.agents.tanks import ComponentTank
from src.agents.limits import (
    BLEND_SULFUR_MAX,
    BLEND_DENSITY,
    BLEND_FLASH_MIN,
    BLEND_T95_MAX,
    BLEND_CETANE_MIN,
)
from src.twin.params import load_params


class BlendingRequest(BaseModel):
    """Параметры компонентов и целевые требования на блендинг (legacy)."""
    sulfur_diesel: float = Field(description="Содержание серы в гидрогенизате, мг/кг (ppm)")
    cfpp_diesel: float = Field(description="Предельная температура фильтруемости дизеля, °C")
    flash_diesel: float = Field(description="Температура вспышки дизеля в закрытом тигле, °C")
    target_cfpp: float = Field(default=-15.0, description="Целевая ПТФ товарного ДТ, °C (сорт E: -15°C, сорт F: -20°C)")
    target_flash: float = Field(default=56.0, description="Целевая температура вспышки, °C")
    price_diesel: float = Field(default=60000.0, description="Себестоимость дизельного гидрогенизата, руб/т")
    price_kerosene: float = Field(default=85000.0, description="Цена керосиновой фракции ТС-1, руб/т")
    price_ddp: float = Field(default=1500000.0, description="Цена присадки ДДП, руб/т")
    max_kerosene_vol: float = Field(default=0.20, description="Максимальная объемная доля керосина")
    min_diesel_vol: float = Field(default=0.50, description="Минимальная объемная доля дизеля")
    max_ddp_ppm: float = Field(default=1500.0, description="Максимальная дозировка ДДП, ppm")


class BlendingResult(BaseModel):
    """Результат расчета оптимальной рецептуры блендинга."""
    success: bool = Field(description="Флаг успешности нахождения допустимого решения")
    v_diesel: float = Field(default=0.0, description="Объемная доля дизельного гидрогенизата (0..1)")
    v_kerosene: float = Field(default=0.0, description="Объемная доля керосина ТС-1 (0..1)")
    v_ddp_ppm: float = Field(default=0.0, description="Дозировка присадки ДДП, ppm")
    expected_cfpp: float = Field(default=0.0, description="Расчетная ПТФ товарной смеси, °C")
    expected_flash: float = Field(default=0.0, description="Расчетная температура вспышки смеси, °C")
    expected_sulfur: float = Field(default=0.0, description="Расчетное содержание серы в смеси, ppm")
    cost_per_ton: float = Field(default=0.0, description="Себестоимость тонны товарного топлива, руб/т")
    fbi_blend: float = Field(default=0.0, description="Индекс вспышки смеси Вики-Читтендена")
    error_message: Optional[str] = Field(default=None, description="Пояснение при невозможности оптимизации")

    # Новые поля v2
    shares: Dict[str, float] = Field(default_factory=dict, description="Доли всех компонентов смеси")
    additive_doses_kg_t: Dict[str, float] = Field(default_factory=dict, description="Дозировки присадок, кг/т")
    expected_density: Optional[float] = Field(default=None, description="Расчетная плотность смеси D15, кг/м3")
    expected_t95: Optional[float] = Field(default=None, description="Расчетная T95 смеси, °C")
    expected_cetane: Optional[float] = Field(default=None, description="Расчетное цетановое число смеси")
    binding_constraints: List[str] = Field(default_factory=list, description="Активные ограничения LP-задачи")
    blocked_components: List[str] = Field(default_factory=list, description="Заблокированные компоненты (Dilution Loophole)")

    @property
    def is_feasible(self) -> bool:
        return self.success

    @property
    def infeasibility_reason(self) -> Optional[str]:
        return self.error_message

    @property
    def fractions(self) -> Dict[str, float]:
        """Словарь долей компонентов для обратной совместимости."""
        res = {
            "diesel": self.v_diesel,
            "kerosene": self.v_kerosene,
            "ddp_ppm": self.v_ddp_ppm,
        }
        res.update(self.shares)
        return res


@dataclass(frozen=True)
class BlendComponent:
    name: str
    price_rub_t: float
    stock_t: float
    v_min: float
    v_max: float
    s_ppm: float
    d15: float
    flash_c: float
    cfpp_c: float
    t95_c: Optional[float] = None
    cn: Optional[float] = None
    props_source: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AdditiveSegment:
    name: str
    max_kg_t: float
    effect_per_kg_t: float
    price_rub_t: float
    target: Literal["cfpp", "cn"]


@dataclass(frozen=True)
class BlendProblem:
    components: tuple[BlendComponent, ...]
    additives: tuple[AdditiveSegment, ...]
    batch_t: float = 2000.0
    target_cfpp: float = -15.0
    sulfur_max: float = BLEND_SULFUR_MAX
    density_bounds: Optional[tuple[float, float]] = BLEND_DENSITY
    flash_min: float = BLEND_FLASH_MIN
    t95_max: Optional[float] = BLEND_T95_MAX
    cn_min: Optional[float] = BLEND_CETANE_MIN
    rho_ref: float = 836.0


def calculate_fbi(t_flash: float) -> float:
    """Индекс Вики-Читтендена: FBI = 10^(4.1485 - 0.0601 * T)."""
    return 10.0 ** (4.1485 - 0.0601 * t_flash)


def calculate_flash_from_fbi(fbi: float) -> float:
    """Обращение индекса вспышки: T = (4.1485 - log10(FBI)) / 0.0601."""
    safe_fbi = max(float(fbi), 1e-6)
    return (4.1485 - math.log10(safe_fbi)) / 0.0601


def solve_blend(problem: BlendProblem) -> BlendingResult:
    """
    Решает задачу многокомпонентного блендинга на базе SciPy HiGHS LP.
    """
    n_c = len(problem.components)
    n_a = len(problem.additives)

    blocked_components: List[str] = []
    effective_v_max: List[float] = []

    # 1. Проверка Dilution Loophole и ограничений по остаткам в резервуарах
    for comp in problem.components:
        if comp.s_ppm > 10.0:
            blocked_components.append(f"{comp.name} (S={comp.s_ppm:.1f} > 10.0 ppm)")
            effective_v_max.append(0.0)
        else:
            # Ограничение по остатку: v_c <= stock_c * rho_ref / (batch * rho_c)
            rho_c = max(comp.d15, 700.0)
            v_stock_limit = (comp.stock_t * problem.rho_ref) / max(problem.batch_t * rho_c, 1e-4)
            v_max = min(comp.v_max, v_stock_limit)
            effective_v_max.append(max(0.0, v_max))

    # Если базовый дизель ГО ДТ заблокирован по Dilution Loophole
    if problem.components and problem.components[0].s_ppm > 10.0:
        return BlendingResult(
            success=False,
            blocked_components=blocked_components,
            error_message=(
                f"BLOCKED: Dilution Loophole. Сера дизельного гидрогенизата ({problem.components[0].s_ppm:.1f} ppm) "
                f"превышает норматив ГОСТ (10.0 ppm). Разбавление керосином запрещено правилами безопасности."
            ),
        )

    # Вектор переменных: [v_1, ..., v_Nc, a_1, ..., a_Na]
    # Цель: минимизация себестоимости 1 м3 смеси (руб/м3):
    # sum(v_c * rho_c * Price_c / 1000) + sum(a_s * rho_ref * Price_s / 10^6)
    c_obj = np.zeros(n_c + n_a, dtype=float)
    for i, comp in enumerate(problem.components):
        c_obj[i] = comp.d15 * comp.price_rub_t / 1000.0
    for j, add in enumerate(problem.additives):
        c_obj[n_c + j] = problem.rho_ref * add.price_rub_t / 1e6

    # Границы переменных
    bounds: List[Tuple[float, float]] = []
    for i, comp in enumerate(problem.components):
        v_lo = comp.v_min if comp.s_ppm <= 10.0 else 0.0
        bounds.append((v_lo, effective_v_max[i]))
    for j, add in enumerate(problem.additives):
        bounds.append((0.0, add.max_kg_t))

    # Ограничения-равенства: sum(v_c) == 1.0
    A_eq = np.zeros((1, n_c + n_a), dtype=float)
    A_eq[0, :n_c] = 1.0
    b_eq = np.array([1.0], dtype=float)

    # Ограничения-неравенства A_ub * x <= b_ub
    A_ub_rows: List[List[float]] = []
    b_ub_rows: List[float] = []
    row_names: List[str] = []

    # 2. Сера по массе: sum(v_c * rho_c * (S_c - S_max)) <= 0
    row_s = [comp.d15 * (comp.s_ppm - problem.sulfur_max) for comp in problem.components] + [0.0] * n_a
    A_ub_rows.append(row_s)
    b_ub_rows.append(0.0)
    row_names.append("Sulfur_Max")

    # 3. Плотность
    if problem.density_bounds is not None:
        rho_min, rho_max = problem.density_bounds
        # sum(v_c * rho_c) <= rho_max
        row_d_hi = [comp.d15 for comp in problem.components] + [0.0] * n_a
        A_ub_rows.append(row_d_hi)
        b_ub_rows.append(rho_max)
        row_names.append("Density_Max")

        # -sum(v_c * rho_c) <= -rho_min
        row_d_lo = [-comp.d15 for comp in problem.components] + [0.0] * n_a
        A_ub_rows.append(row_d_lo)
        b_ub_rows.append(-rho_min)
        row_names.append("Density_Min")

    # 4. Вспышка через FBI: sum(v_c * FBI_c) <= FBI_target
    fbi_target = calculate_fbi(problem.flash_min)
    row_flash = [calculate_fbi(comp.flash_c) for comp in problem.components] + [0.0] * n_a
    A_ub_rows.append(row_flash)
    b_ub_rows.append(fbi_target)
    row_names.append("Flash_Min")

    # 5. ПТФ (CFPP): sum(v_c * CFPP_c) - sum(e_a * a_s) <= target_cfpp
    row_cfpp = [comp.cfpp_c for comp in problem.components]
    for add in problem.additives:
        if add.target == "cfpp":
            row_cfpp.append(-add.effect_per_kg_t)
        else:
            row_cfpp.append(0.0)
    A_ub_rows.append(row_cfpp)
    b_ub_rows.append(problem.target_cfpp)
    row_names.append("CFPP_Target")

    # 6. T95 (если задано): sum(v_c * T95_c) <= T95_max
    if problem.t95_max is not None and all(comp.t95_c is not None for comp in problem.components):
        row_t95 = [float(comp.t95_c) for comp in problem.components] + [0.0] * n_a
        A_ub_rows.append(row_t95)
        b_ub_rows.append(problem.t95_max)
        row_names.append("T95_Max")

    # 7. Цетановое число CN (если задано): -sum(v_c * CN_c) - sum(e_b * a_s) <= -cn_min
    if problem.cn_min is not None and all(comp.cn is not None for comp in problem.components):
        row_cn = [-float(comp.cn) for comp in problem.components]
        for add in problem.additives:
            if add.target == "cn":
                row_cn.append(-add.effect_per_kg_t)
            else:
                row_cn.append(0.0)
        A_ub_rows.append(row_cn)
        b_ub_rows.append(-problem.cn_min)
        row_names.append("Cetane_Min")

    A_ub = np.array(A_ub_rows, dtype=float)
    b_ub = np.array(b_ub_rows, dtype=float)

    # Решаем LP через HiGHS
    res = linprog(
        c=c_obj,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )

    if not res.success:
        return BlendingResult(
            success=False,
            blocked_components=blocked_components,
            error_message=(
                f"LP Solver failed ({res.message}). "
                f"Невозможно одновременно обеспечить ПТФ <= {problem.target_cfpp:.1f}°C и вспышку >= {problem.flash_min:.1f}°C "
                f"при заданном качестве сырья и лимитах компонентов и остатков."
            ),
        )

    # Извлечение результатов
    x_opt = res.x
    v_opt = x_opt[:n_c]
    a_opt = x_opt[n_c:]

    shares_dict = {comp.name: round(float(v_opt[i]), 4) for i, comp in enumerate(problem.components)}
    add_dict = {add.name: round(float(a_opt[j]), 4) for j, add in enumerate(problem.additives)}

    # Расчет свойств товарной смеси
    rho_mix = sum(v_opt[i] * comp.d15 for i, comp in enumerate(problem.components))
    mass_fractions = [v_opt[i] * comp.d15 / rho_mix for i, comp in enumerate(problem.components)]

    actual_sulfur = sum(mass_fractions[i] * comp.s_ppm for i, comp in enumerate(problem.components))
    fbi_mix = sum(v_opt[i] * calculate_fbi(comp.flash_c) for i, comp in enumerate(problem.components))
    actual_flash = calculate_flash_from_fbi(fbi_mix)

    cfpp_depression = sum(
        add.effect_per_kg_t * a_opt[j] for j, add in enumerate(problem.additives) if add.target == "cfpp"
    )
    actual_cfpp = sum(v_opt[i] * comp.cfpp_c for i, comp in enumerate(problem.components)) - cfpp_depression

    actual_t95 = None
    if all(comp.t95_c is not None for comp in problem.components):
        actual_t95 = sum(v_opt[i] * float(comp.t95_c) for i, comp in enumerate(problem.components))

    actual_cn = None
    if all(comp.cn is not None for comp in problem.components):
        cn_boost = sum(
            add.effect_per_kg_t * a_opt[j] for j, add in enumerate(problem.additives) if add.target == "cn"
        )
        actual_cn = sum(v_opt[i] * float(comp.cn) for i, comp in enumerate(problem.components)) + cn_boost

    # Стоимость тонны смеси
    cost_per_ton = float(res.fun) * 1000.0 / rho_mix

    # Активные ограничения (slack < 1e-4)
    binding: List[str] = []
    slacks = b_ub - A_ub @ x_opt
    for idx, slk in enumerate(slacks):
        if abs(slk) < 1e-4:
            binding.append(row_names[idx])

    # Для обратной совместимости вычисляем v_diesel, v_kerosene, v_ddp_ppm
    v_diesel = shares_dict.get("GODT", shares_dict.get("diesel", v_opt[0]))
    v_kero = shares_dict.get("Kerosene", shares_dict.get("kerosene", v_opt[1] if n_c > 1 else 0.0))
    total_ddp_ppm = sum(a_opt[j] * 1000.0 for j, add in enumerate(problem.additives) if add.target == "cfpp")

    return BlendingResult(
        success=True,
        v_diesel=round(float(v_diesel), 4),
        v_kerosene=round(float(v_kero), 4),
        v_ddp_ppm=round(float(total_ddp_ppm), 1),
        expected_cfpp=round(float(actual_cfpp), 2),
        expected_flash=round(float(actual_flash), 2),
        expected_sulfur=round(float(actual_sulfur), 2),
        cost_per_ton=round(float(cost_per_ton), 2),
        fbi_blend=round(float(fbi_mix), 4),
        error_message=None,
        shares=shares_dict,
        additive_doses_kg_t=add_dict,
        expected_density=round(float(rho_mix), 2),
        expected_t95=round(float(actual_t95), 2) if actual_t95 is not None else None,
        expected_cetane=round(float(actual_cn), 2) if actual_cn is not None else None,
        binding_constraints=binding,
        blocked_components=blocked_components,
    )


class OptimizerBlending:
    """
    Фасад оптимизатора блендинга, обеспечивающий обратную совместимость с v1 (Шаг 3).
    """

    KERO_SULFUR: float = 2.0
    KERO_CFPP: float = -45.0
    KERO_FLASH: float = 42.0

    @classmethod
    def calculate_fbi(cls, t_flash: float) -> float:
        return calculate_fbi(t_flash)

    @classmethod
    def calculate_flash_from_fbi(cls, fbi: float) -> float:
        return calculate_flash_from_fbi(fbi)

    @classmethod
    def solve_recipe(
        cls,
        sulfur_diesel: float,
        cfpp_diesel: float,
        flash_diesel: float,
        target_cfpp: float = -15.0,
        target_flash: float = 56.0,
        price_diesel: float = 60000.0,
        price_kerosene: float = 85000.0,
        price_ddp: float = 1500000.0,
        max_kerosene_vol: float = 0.20,
        min_diesel_vol: float = 0.50,
        max_ddp_ppm: float = 1500.0,
        density_diesel: Optional[float] = None,
    ) -> BlendingResult:
        """
        Обёртка над solve_blend для обратной совместимости с тестами Шага 3.
        """
        # Блокировка Dilution Loophole
        if sulfur_diesel > 10.0:
            return BlendingResult(
                success=False,
                error_message=(
                    f"BLOCKED: Dilution Loophole. Сера дизельного гидрогенизата ({sulfur_diesel:.1f} ppm) "
                    f"превышает норматив ГОСТ (10.0 ppm). Разбавление керосином запрещено правилами безопасности."
                ),
            )

        d15_diesel = density_diesel or 836.0
        comp_diesel = BlendComponent(
            name="diesel",
            price_rub_t=price_diesel,
            stock_t=1e6,
            v_min=min_diesel_vol,
            v_max=1.0,
            s_ppm=sulfur_diesel,
            d15=d15_diesel,
            flash_c=flash_diesel,
            cfpp_c=cfpp_diesel,
        )

        comp_kero = BlendComponent(
            name="kerosene",
            price_rub_t=price_kerosene,
            stock_t=1e6,
            v_min=0.0,
            v_max=max_kerosene_vol,
            s_ppm=cls.KERO_SULFUR,
            d15=785.0,
            flash_c=cls.KERO_FLASH,
            cfpp_c=cls.KERO_CFPP,
        )

        # Кусочно-линейная ДДП: 0..500 ppm (-12.0 °C на кг/т = -0.012 °C/ppm) и 500..1500 ppm (-3.76 °C на кг/т)
        seg1_max = min(0.5, max_ddp_ppm / 1000.0)
        seg2_max = max(0.0, (max_ddp_ppm - 500.0) / 1000.0)

        add_a1 = AdditiveSegment(name="ddp_seg1", max_kg_t=seg1_max, effect_per_kg_t=12.0, price_rub_t=price_ddp, target="cfpp")
        add_a2 = AdditiveSegment(name="ddp_seg2", max_kg_t=seg2_max, effect_per_kg_t=3.76, price_rub_t=price_ddp, target="cfpp")

        density_b = (821.25, 843.75) if density_diesel is not None else None

        problem = BlendProblem(
            components=(comp_diesel, comp_kero),
            additives=(add_a1, add_a2),
            batch_t=2000.0,
            target_cfpp=target_cfpp,
            sulfur_max=9.5,
            density_bounds=density_b,
            flash_min=target_flash,
            t95_max=None,
            cn_min=None,
            rho_ref=d15_diesel,
        )

        res = solve_blend(problem)
        # Если не сошлось, формируем сообщение в формате v1
        if not res.success and res.error_message and "LP Solver failed" in res.error_message:
            res.error_message = (
                f"LP Solver failed (Infeasible). "
                f"Невозможно одновременно обеспечить ПТФ <= {target_cfpp:.1f}°C и вспышку >= {target_flash:.1f}°C "
                f"при заданном качестве гидрогенизата (CFPP={cfpp_diesel:.1f}°C, Flash={flash_diesel:.1f}°C), "
                f"максимальном вовлечении керосина {max_kerosene_vol*100:.0f}% и пределе насыщения ДДП ({max_ddp_ppm} ppm)."
            )
        return res


def get_default_tanks() -> Dict[str, ComponentTank]:
    """Возвращает стандартную конфигурацию резервуаров (ASSUMPTION, Q5)."""
    return {
        "GODT": ComponentTank(
            name="GODT",
            stock_t=5000.0,
            props={
                "S_ppm": 8.6,
                "D15": 836.1,
                "Flash": 68.0,
                "CFPP": -6.0,
                "T95": 347.0,
                "CN": 53.75,
            },
            prop_sources={"default": "nominal_lims"},
        ),
        "Kerosene": ComponentTank(
            name="Kerosene",
            stock_t=800.0,
            props={
                "S_ppm": 2.0,
                "D15": 785.0,
                "Flash": 42.0,
                "CFPP": -45.0,
                "T95": 240.0,
                "CN": 42.0,
            },
            prop_sources={"default": "assumed_specs"},
        ),
        "Gasoil": ComponentTank(
            name="Gasoil",
            stock_t=1500.0,
            props={
                "S_ppm": 8.0,
                "D15": 860.0,
                "Flash": 90.0,
                "CFPP": 0.0,
                "T95": 365.0,
                "CN": 45.0,
            },
            prop_sources={"default": "assumed_specs"},
        ),
    }


def node_blending_agent(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел графа LangGraph: Агент оптимизации блендинга из резервуаров.
    """
    tanks: Dict[str, ComponentTank] = state.get("tanks") or get_default_tanks()
    godt_tank = tanks["GODT"]

    cand = state.get("selected_candidate")
    props_source: Dict[str, str] = {}

    if cand is not None and getattr(cand, "steady_state", None):
        ss = cand.steady_state
        u_feed = float(cand.delta_u.get("HT_FEED_SP", 0.0)) + 219.6
        godt_props = godt_tank.forecast(
            flow_tph=u_feed * 0.98,
            props_in=ss,
            horizon_h=1.0,
        )
        props_source["mode"] = "steady_state_forecast"
    else:
        # Резервный источник: LIMS -> онлайн -> ВАК -> телеметрия
        raw_telemetry = state.get("raw_telemetry")
        raw_dict: Dict[str, Any] = {}
        if raw_telemetry is not None:
            if hasattr(raw_telemetry, "model_dump"):
                raw_dict = raw_telemetry.model_dump()
            elif isinstance(raw_telemetry, dict):
                raw_dict = raw_telemetry

        s_val = float(raw_dict.get("HT_Q21", raw_dict.get("Sulfur", 8.5)))
        flash_val = float(raw_dict.get("HT_T18", raw_dict.get("flash_diesel", 65.0)))
        cfpp_val = float(raw_dict.get("HT_CFPP_PRODUCT", raw_dict.get("24-2000:GODT:CFPP", -6.0)))
        d15_val = float(raw_dict.get("HT_D15_PRODUCT", raw_dict.get("24-2000:GODT:D15", 836.1)))
        t95_val = float(raw_dict.get("HT_T95_PRODUCT", raw_dict.get("24-2000:GODT:T95", 347.0)))

        godt_props = {
            "S_ppm": s_val,
            "Flash": flash_val,
            "CFPP": cfpp_val,
            "D15": d15_val,
            "T95": t95_val,
            "CN": 53.75,
        }
        props_source["mode"] = "fallback_telemetry"

    # Формируем 3 компонента для LP задачи
    kero_tank = tanks["Kerosene"]
    gasoil_tank = tanks["Gasoil"]

    twin_p = state.get("twin_params") or load_params()
    p_godt = twin_p.blend.price_godt
    p_kero = twin_p.blend.price_kerosene
    p_gasoil = twin_p.blend.price_gasoil
    p_add_a = twin_p.blend.additive_a_price_rub_t
    p_add_b = twin_p.blend.additive_b_price_rub_t

    econ_state = state.get("economics")
    if isinstance(econ_state, dict):
        p_godt = float(econ_state.get("price_godt", econ_state.get("c_diesel", econ_state.get("product_diesel_rub_ton", p_godt))))
        p_kero = float(econ_state.get("price_kerosene", econ_state.get("c_kerosene", p_kero)))
        p_gasoil = float(econ_state.get("price_gasoil", econ_state.get("c_gasoil", p_gasoil)))
        p_add_a = float(econ_state.get("additive_a_price_rub_t", econ_state.get("c_ddp", econ_state.get("price_ddp", p_add_a))))
        p_add_b = float(econ_state.get("additive_b_price_rub_t", econ_state.get("c_cetane", econ_state.get("price_cetane", p_add_b))))
    elif econ_state is not None:
        p_godt = float(getattr(econ_state, "price_godt", getattr(econ_state, "c_diesel", p_godt)))
        p_kero = float(getattr(econ_state, "price_kerosene", getattr(econ_state, "c_kerosene", p_kero)))
        p_gasoil = float(getattr(econ_state, "price_gasoil", getattr(econ_state, "c_gasoil", p_gasoil)))
        p_add_a = float(getattr(econ_state, "additive_a_price_rub_t", getattr(econ_state, "c_ddp", p_add_a)))
        p_add_b = float(getattr(econ_state, "additive_b_price_rub_t", getattr(econ_state, "c_cetane", p_add_b)))


    comp_godt = BlendComponent(
        name="GODT",
        price_rub_t=p_godt,
        stock_t=godt_tank.stock_t,
        v_min=0.50,
        v_max=1.0,
        s_ppm=godt_props["S_ppm"],
        d15=godt_props["D15"],
        flash_c=godt_props["Flash"],
        cfpp_c=godt_props["CFPP"],
        t95_c=godt_props["T95"],
        cn=godt_props["CN"],
        props_source=props_source,
    )

    comp_kero = BlendComponent(
        name="Kerosene",
        price_rub_t=p_kero,
        stock_t=kero_tank.stock_t,
        v_min=0.0,
        v_max=0.20,
        s_ppm=kero_tank.props.get("S_ppm", 2.0),
        d15=kero_tank.props.get("D15", 785.0),
        flash_c=kero_tank.props.get("Flash", 42.0),
        cfpp_c=kero_tank.props.get("CFPP", -45.0),
        t95_c=kero_tank.props.get("T95", 240.0),
        cn=kero_tank.props.get("CN", 42.0),
        props_source={"source": "tank_analysis"},
    )

    comp_gasoil = BlendComponent(
        name="Gasoil",
        price_rub_t=p_gasoil,
        stock_t=gasoil_tank.stock_t,
        v_min=0.0,
        v_max=0.20,
        s_ppm=gasoil_tank.props.get("S_ppm", 8.0),
        d15=gasoil_tank.props.get("D15", 860.0),
        flash_c=gasoil_tank.props.get("Flash", 90.0),
        cfpp_c=gasoil_tank.props.get("CFPP", 0.0),
        t95_c=gasoil_tank.props.get("T95", 365.0),
        cn=gasoil_tank.props.get("CN", 45.0),
        props_source={"source": "tank_analysis"},
    )

    # Присадки A и B
    add_a1 = AdditiveSegment(name="depressor_seg1", max_kg_t=0.5, effect_per_kg_t=12.0, price_rub_t=p_add_a, target="cfpp")
    add_a2 = AdditiveSegment(name="depressor_seg2", max_kg_t=1.0, effect_per_kg_t=3.76, price_rub_t=p_add_a, target="cfpp")
    add_b1 = AdditiveSegment(name="cetane_seg1", max_kg_t=0.5, effect_per_kg_t=6.0, price_rub_t=p_add_b, target="cn")
    add_b2 = AdditiveSegment(name="cetane_seg2", max_kg_t=1.0, effect_per_kg_t=2.0, price_rub_t=p_add_b, target="cn")

    problem = BlendProblem(
        components=(comp_godt, comp_kero, comp_gasoil),
        additives=(add_a1, add_a2, add_b1, add_b2),
        batch_t=2000.0,
        target_cfpp=-15.0,
        sulfur_max=9.5,
        density_bounds=(821.25, 843.75),
        flash_min=56.0,
        t95_max=358.0,
        cn_min=51.5,
        rho_ref=836.0,
    )

    recipe = solve_blend(problem)
    result_update: Dict[str, Any] = {"blending_recipe": recipe}

    final_rec = state.get("final_recommendation")
    selected_cand = state.get("selected_candidate")
    if final_rec is not None and selected_cand is not None:
        try:
            from src.xai.narrative import XAIGenerator

            base_dict = {}
            if state.get("tags"):
                base_dict.update(state["tags"])
            raw_telemetry = state.get("raw_telemetry")
            if raw_telemetry is not None:
                if hasattr(raw_telemetry, "model_dump"):
                    base_dict.update(raw_telemetry.model_dump())
                elif isinstance(raw_telemetry, dict):
                    base_dict.update(raw_telemetry)

            lims_age = float(
                base_dict.get(
                    "lims_age_hours",
                    state.get("confidence", {}).get("lims_age_hours", 0.0),
                )
            )

            updated_report = XAIGenerator.generate_explanation(
                best_candidate=selected_cand,
                base_state=base_dict,
                risk_penalties=state.get("risk_penalties", {}),
                lims_age_hours=lims_age,
                hold_prediction=state.get("hold_prediction"),
                alternatives=state.get("alternatives"),
                confidence=state.get("confidence"),
                blending_recipe=recipe,
            )
            updated_rec = final_rec.model_copy(update={"markdown_report": updated_report})
            result_update["final_recommendation"] = updated_rec
        except Exception:
            pass

    return result_update
