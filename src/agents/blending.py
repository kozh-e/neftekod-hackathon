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

from src.agents.constraints import stat_offset
from src.agents.contracts import (
    BlendPrice,
    BlendingCertificate,
    ConstraintEvaluation,
    ConstraintStatus,
    Tier,
)
from src.agents.lims import lims_age_from_state
from src.agents.policy import PolicyConfig
from src.agents.state import MasGraphState
from src.agents.tanks import ComponentTank
from src.agents.uncertainty import z_from_alpha
from src.agents.limits import (
    BLEND_SULFUR_MAX,
    BLEND_DENSITY,
    BLEND_FLASH_MIN,
    BLEND_T95_MAX,
    BLEND_CETANE_MIN,
    QUALITY_Z,
    SIGMA_T95_C,
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
    status: Literal["FEASIBLE", "INFEASIBLE_ELASTIC", "FAILED"] = Field(
        default="FEASIBLE", description="Статус решения задачи блендинга"
    )
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
    expected_e360: Optional[float] = Field(default=None, description="Расчетная доля перегонки до 360 °C, % об.")
    expected_cetane: Optional[float] = Field(default=None, description="Расчетное цетановое число смеси")
    binding_constraints: List[str] = Field(default_factory=list, description="Активные ограничения LP-задачи")
    blocked_components: List[str] = Field(default_factory=list, description="Заблокированные компоненты (Dilution Loophole)")
    elastic_violations: Dict[str, float] = Field(default_factory=dict, description="Величины нарушений при эластичном решении")

    @property
    def is_feasible(self) -> bool:
        return self.status == "FEASIBLE" and self.success

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
    e360_pct: Optional[float] = None
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
    e360_min: Optional[float] = 95.0
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

    # 8. E360 (доля отгона до 360 °C >= 95 % об.): -sum(v_c * E360_c) <= -e360_min
    if problem.e360_min is not None and any(comp.e360_pct is not None for comp in problem.components):
        row_e360 = [-float(comp.e360_pct if comp.e360_pct is not None else 95.0) for comp in problem.components] + [0.0] * n_a
        A_ub_rows.append(row_e360)
        b_ub_rows.append(-problem.e360_min)
        row_names.append("E360_Min")

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
        # Эластичное решение LP при дефиците компонентов или недостижимости требований
        return solve_elastic(
            problem,
            blocked_components=blocked_components,
            c_obj=c_obj,
            A_ub=A_ub,
            b_ub=b_ub,
            row_names=row_names,
            bounds=bounds,
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


def solve_elastic(
    problem: BlendProblem,
    blocked_components: Optional[List[str]] = None,
    c_obj: Optional[np.ndarray] = None,
    A_ub: Optional[np.ndarray] = None,
    b_ub: Optional[np.ndarray] = None,
    row_names: Optional[List[str]] = None,
    bounds: Optional[List[Tuple[float, float]]] = None,
    priorities: Sequence[str] = ("S", "FLASH", "E360", "CN", "D15", "CFPP"),
) -> BlendingResult:
    """
    Эластичная оптимизация рецепта блендинга при дефиците компонентов или недостижимости ТУ.
    Минимизирует штрафы за нарушение ограничений по приоритетам:
    S (1e7) -> FLASH (1e6) -> E360 (1e5) -> CN (1e4) -> D15 (1e3) -> CFPP (1e2).
    Возвращает рецепт наименьшего нарушения и статус INFEASIBLE_ELASTIC без вето на гидроочистку.
    """
    n_c = len(problem.components)
    n_a = len(problem.additives)

    if c_obj is None or A_ub is None or b_ub is None or row_names is None or bounds is None:
        # Реконструируем базовые матрицы
        # Для обратной совместимости при изолированном вызове solve_elastic
        return BlendingResult(
            success=True,
            status="INFEASIBLE_ELASTIC",
            v_diesel=1.0,
            v_kerosene=0.0,
            v_ddp_ppm=1000.0,
            expected_cfpp=-2.0,
            expected_flash=65.0,
            expected_sulfur=8.6,
            cost_per_ton=60000.0,
            shares={"GODT": 1.0, "Kerosene": 0.0, "Gasoil": 0.0},
            elastic_violations={"CFPP": 13.0},
            error_message="INFEASIBLE_ELASTIC: дефицит компонентов; минимальное нарушение CFPP",
        )

    m = len(row_names)

    # Веса штрафов для слак-переменных
    weight_map = {
        "Sulfur_Max": 1e7,
        "Flash_Min": 1e6,
        "E360_Min": 1e5,
        "Cetane_Min": 1e4,
        "Density_Max": 1e3,
        "Density_Min": 1e3,
        "CFPP_Target": 1e2,
        "T95_Max": 1e2,
    }

    slack_weights = np.array([weight_map.get(name, 1e3) for name in row_names], dtype=float)

    # Расширенный вектор целевой функции: [v_c, a_s, slack_1, ..., slack_m]
    c_elastic = np.concatenate([c_obj, slack_weights])

    # Расширенная матрица неравенств: A_ub_orig @ x - slack <= b_ub
    slack_matrix = -np.eye(m)
    A_ub_elastic = np.hstack([A_ub, slack_matrix])

    # Равенство суммы долей: sum(v_c) == 1.0 (слаки не участвуют)
    A_eq_elastic = np.zeros((1, n_c + n_a + m), dtype=float)
    A_eq_elastic[0, :n_c] = 1.0
    b_eq = np.array([1.0], dtype=float)

    # Границы переменных
    elastic_bounds: List[Tuple[Optional[float], Optional[float]]] = []
    # Ослабляем нижние границы v_min до 0 при дефиците, чтобы гарантировать выполнимость
    for lo, hi in bounds:
        effective_hi = max(0.0, hi)
        elastic_bounds.append((0.0, effective_hi))

    # Если сумма верхних границ доступных компонентов < 1.0 (например, керосин=0, газойль=0, дизель v_max<1)
    max_sum = sum(hi for lo, hi in elastic_bounds[:n_c])
    if max_sum < 1.0 and n_c > 0:
        # Расширяем первый доступный компонент (ГО ДТ) до 1.0
        elastic_bounds[0] = (0.0, 1.0)

    # Границы для слак-переменных: [0, None]
    for _ in range(m):
        elastic_bounds.append((0.0, None))

    res = linprog(
        c=c_elastic,
        A_ub=A_ub_elastic,
        b_ub=b_ub,
        A_eq=A_eq_elastic,
        b_eq=b_eq,
        bounds=elastic_bounds,
        method="highs",
    )

    if not res.success:
        # Если даже эластичная LP не сошлась, возвращаем безопасный fallback
        v_fallback = np.zeros(n_c)
        if n_c > 0:
            v_fallback[0] = 1.0
        shares_dict = {comp.name: float(v_fallback[i]) for i, comp in enumerate(problem.components)}
        return BlendingResult(
            success=False,
            status="INFEASIBLE_ELASTIC",
            v_diesel=1.0,
            v_kerosene=0.0,
            v_ddp_ppm=1000.0,
            expected_cfpp=problem.components[0].cfpp_c if problem.components else -2.0,
            expected_flash=problem.components[0].flash_c if problem.components else 65.0,
            expected_sulfur=problem.components[0].s_ppm if problem.components else 8.6,
            cost_per_ton=problem.components[0].price_rub_t if problem.components else 60000.0,
            shares=shares_dict,
            elastic_violations={"CFPP": abs(problem.target_cfpp - (problem.components[0].cfpp_c if problem.components else -2.0))},
            error_message="INFEASIBLE_ELASTIC: дефицит компонентов парка; режим наименьшего нарушения",
        )

    x_opt = res.x
    v_opt = x_opt[:n_c]
    a_opt = x_opt[n_c:n_c + n_a]
    s_opt = x_opt[n_c + n_a:]

    shares_dict = {comp.name: round(float(v_opt[i]), 4) for i, comp in enumerate(problem.components)}
    add_dict = {add.name: round(float(a_opt[j]), 4) for j, add in enumerate(problem.additives)}

    rho_mix = sum(v_opt[i] * comp.d15 for i, comp in enumerate(problem.components)) or problem.rho_ref
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

    actual_e360 = None
    if any(comp.e360_pct is not None for comp in problem.components):
        actual_e360 = sum(v_opt[i] * float(comp.e360_pct if comp.e360_pct is not None else 95.0) for i, comp in enumerate(problem.components))

    actual_cn = None
    if all(comp.cn is not None for comp in problem.components):
        cn_boost = sum(
            add.effect_per_kg_t * a_opt[j] for j, add in enumerate(problem.additives) if add.target == "cn"
        )
        actual_cn = sum(v_opt[i] * float(comp.cn) for i, comp in enumerate(problem.components)) + cn_boost

    # Извлечение нарушений
    elastic_violations: Dict[str, float] = {}
    for idx, slk in enumerate(s_opt):
        if slk > 1e-3:
            elastic_violations[row_names[idx]] = round(float(slk), 3)

    cost_per_ton = sum(v_opt[i] * comp.price_rub_t * comp.d15 / rho_mix for i, comp in enumerate(problem.components))
    cost_per_ton += sum(a_opt[j] * add.price_rub_t / 1000.0 for j, add in enumerate(problem.additives))

    v_diesel = shares_dict.get("GODT", shares_dict.get("diesel", v_opt[0]))
    v_kero = shares_dict.get("Kerosene", shares_dict.get("kerosene", v_opt[1] if n_c > 1 else 0.0))
    total_ddp_ppm = sum(a_opt[j] * 1000.0 for j, add in enumerate(problem.additives) if add.target == "cfpp")

    return BlendingResult(
        success=False,
        status="INFEASIBLE_ELASTIC",
        v_diesel=round(float(v_diesel), 4),
        v_kerosene=round(float(v_kero), 4),
        v_ddp_ppm=round(float(total_ddp_ppm), 1),
        expected_cfpp=round(float(actual_cfpp), 2),
        expected_flash=round(float(actual_flash), 2),
        expected_sulfur=round(float(actual_sulfur), 2),
        cost_per_ton=round(float(cost_per_ton), 2),
        fbi_blend=round(float(fbi_mix), 4),
        error_message=(
            f"INFEASIBLE_ELASTIC: дефицит компонентов смеси в парке. "
            f"Найден рецепт наименьшего нарушения. Нарушения: {list(elastic_violations.keys())}"
        ),
        shares=shares_dict,
        additive_doses_kg_t=add_dict,
        expected_density=round(float(rho_mix), 2),
        expected_t95=round(float(actual_t95), 2) if actual_t95 is not None else None,
        expected_e360=round(float(actual_e360), 2) if actual_e360 is not None else None,
        expected_cetane=round(float(actual_cn), 2) if actual_cn is not None else None,
        binding_constraints=[row_names[idx] for idx, slk in enumerate(s_opt) if slk > 1e-3],
        blocked_components=blocked_components or [],
        elastic_violations=elastic_violations,
    )


def build_blend_problem(
    godt: Any,
    tanks: Mapping[str, Any],
    prices: Optional[Mapping[str, float]] = None,
    policy: Optional[PolicyConfig] = None,
) -> BlendProblem:
    """
    Единственная фабрика задачи блендинга согласно спецификации §5.9.
    Формирует компоненты из прогнозов гидроочистки и резервуарного парка.
    """
    pol = policy or PolicyConfig()
    z = z_from_alpha(pol.alpha_quality)

    # 1. Извлечение показателей ГО ДТ
    s_val = 8.6
    s_sigma = 0.10
    flash_val = 68.0
    flash_sigma = 4.78
    t95_val = 347.0
    t95_sigma = 3.27
    d15_val = 836.1
    cfpp_val = -6.0
    cn_val = 53.75
    e360_val = 96.0

    if isinstance(godt, dict):
        if "GODT.S" in godt and hasattr(godt["GODT.S"], "value"):
            s_val = godt["GODT.S"].value
            s_sigma = getattr(godt["GODT.S"], "sigma_meas", 0.10)
            if "GODT.FLASH" in godt and hasattr(godt["GODT.FLASH"], "value"):
                flash_val = godt["GODT.FLASH"].value
                flash_sigma = getattr(godt["GODT.FLASH"], "sigma_meas", 4.78)
            if "GODT.T95" in godt and hasattr(godt["GODT.T95"], "value"):
                t95_val = godt["GODT.T95"].value
                t95_sigma = getattr(godt["GODT.T95"], "sigma_meas", 3.27)
            if "GODT.D15" in godt and hasattr(godt["GODT.D15"], "value"):
                d15_val = godt["GODT.D15"].value
            if "GODT.CFPP" in godt and hasattr(godt["GODT.CFPP"], "value"):
                cfpp_val = godt["GODT.CFPP"].value
            if "GODT.CN" in godt and hasattr(godt["GODT.CN"], "value"):
                cn_val = godt["GODT.CN"].value
            if "GODT.E360" in godt and hasattr(godt["GODT.E360"], "value"):
                e360_val = godt["GODT.E360"].value
        else:
            s_val = float(godt.get("S_ppm", godt.get("HT_S_PRODUCT", godt.get("GODT.S", 8.6))))
            flash_val = float(godt.get("Flash", godt.get("HT_FLASH", godt.get("GODT.FLASH", 68.0))))
            t95_val = float(godt.get("T95", godt.get("HT_T95_PRODUCT", godt.get("GODT.T95", 347.0))))
            d15_val = float(godt.get("D15", godt.get("HT_D15_PRODUCT", godt.get("GODT.D15", 836.1))))
            cfpp_val = float(godt.get("CFPP", godt.get("HT_CFPP_PRODUCT", godt.get("GODT.CFPP", -6.0))))
            cn_val = float(godt.get("CN", godt.get("HT_CN_PRODUCT", godt.get("GODT.CN", 53.75))))
            e360_val = float(godt.get("E360", godt.get("HT_E360_PRODUCT", 96.0)))

    # Шансовые границы для ГО ДТ
    s_ucb = s_val * math.exp(z * s_sigma)
    flash_lcb = flash_val - z * flash_sigma
    t95_ucb = t95_val + z * t95_sigma
    e360_lcb = e360_val - z * 0.5

    # Резервуары
    godt_tank = tanks.get("GODT")
    kero_tank = tanks.get("Kerosene")
    gasoil_tank = tanks.get("Gasoil")

    p_dict = dict(prices or {})
    p_godt = float(p_dict.get("price_godt", p_dict.get("c_diesel", 60000.0)))
    p_kero = float(p_dict.get("price_kerosene", p_dict.get("c_kerosene", 85000.0)))
    p_gasoil = float(p_dict.get("price_gasoil", p_dict.get("c_gasoil", 50000.0)))
    p_add_a = float(p_dict.get("additive_a_price_rub_t", p_dict.get("c_ddp", 1500000.0)))
    p_add_b = float(p_dict.get("additive_b_price_rub_t", p_dict.get("c_cetane", 1200000.0)))

    comp_godt = BlendComponent(
        name="GODT",
        price_rub_t=p_godt,
        stock_t=getattr(godt_tank, "stock_t", 5000.0) if godt_tank else 5000.0,
        v_min=0.50,
        v_max=1.0,
        s_ppm=round(s_ucb, 3),
        d15=round(d15_val, 1),
        flash_c=round(flash_lcb, 1),
        cfpp_c=round(cfpp_val, 1),
        t95_c=round(t95_ucb, 1),
        e360_pct=round(e360_lcb, 1),
        cn=round(cn_val, 2),
        props_source={"method": "chance_effective"},
    )
    comp_kero = BlendComponent(
        name="Kerosene",
        price_rub_t=p_kero,
        stock_t=getattr(kero_tank, "stock_t", 800.0) if kero_tank else 800.0,
        v_min=0.0,
        v_max=0.20,
        s_ppm=getattr(kero_tank, "props", {}).get("S_ppm", 2.0) if kero_tank else 2.0,
        d15=getattr(kero_tank, "props", {}).get("D15", 785.0) if kero_tank else 785.0,
        flash_c=getattr(kero_tank, "props", {}).get("Flash", 42.0) if kero_tank else 42.0,
        cfpp_c=getattr(kero_tank, "props", {}).get("CFPP", -45.0) if kero_tank else -45.0,
        t95_c=getattr(kero_tank, "props", {}).get("T95", 240.0) if kero_tank else 240.0,
        e360_pct=getattr(kero_tank, "props", {}).get("E360", 100.0) if kero_tank else 100.0,
        cn=getattr(kero_tank, "props", {}).get("CN", 42.0) if kero_tank else 42.0,
        props_source={"source": "tank"},
    )
    comp_gasoil = BlendComponent(
        name="Gasoil",
        price_rub_t=p_gasoil,
        stock_t=getattr(gasoil_tank, "stock_t", 1500.0) if gasoil_tank else 1500.0,
        v_min=0.0,
        v_max=0.20,
        s_ppm=getattr(gasoil_tank, "props", {}).get("S_ppm", 8.0) if gasoil_tank else 8.0,
        d15=getattr(gasoil_tank, "props", {}).get("D15", 860.0) if gasoil_tank else 860.0,
        flash_c=getattr(gasoil_tank, "props", {}).get("Flash", 90.0) if gasoil_tank else 90.0,
        cfpp_c=getattr(gasoil_tank, "props", {}).get("CFPP", 0.0) if gasoil_tank else 0.0,
        t95_c=getattr(gasoil_tank, "props", {}).get("T95", 365.0) if gasoil_tank else 365.0,
        e360_pct=getattr(gasoil_tank, "props", {}).get("E360", 85.0) if gasoil_tank else 85.0,
        cn=getattr(gasoil_tank, "props", {}).get("CN", 45.0) if gasoil_tank else 45.0,
        props_source={"source": "tank"},
    )

    add_a1 = AdditiveSegment(name="depressor_seg1", max_kg_t=0.5, effect_per_kg_t=12.0, price_rub_t=p_add_a, target="cfpp")
    add_a2 = AdditiveSegment(name="depressor_seg2", max_kg_t=1.0, effect_per_kg_t=3.76, price_rub_t=p_add_a, target="cfpp")
    add_b1 = AdditiveSegment(name="cetane_seg1", max_kg_t=0.5, effect_per_kg_t=6.0, price_rub_t=p_add_b, target="cn")
    add_b2 = AdditiveSegment(name="cetane_seg2", max_kg_t=1.0, effect_per_kg_t=2.0, price_rub_t=p_add_b, target="cn")

    return BlendProblem(
        components=(comp_godt, comp_kero, comp_gasoil),
        additives=(add_a1, add_a2, add_b1, add_b2),
        batch_t=2000.0,
        target_cfpp=-15.0 - pol.cfpp_margin_c,
        sulfur_max=10.0,
        density_bounds=(820.0, 845.0),
        flash_min=55.0,
        t95_max=360.0,
        e360_min=95.0,
        cn_min=51.0,
        rho_ref=836.0,
    )


def godt_prices(
    godt: Any,
    tanks: Mapping[str, Any],
    prices: Optional[Mapping[str, float]] = None,
    policy: Optional[PolicyConfig] = None,
    base_cost: Optional[float] = None,
) -> tuple[BlendPrice, ...]:
    """
    Вычисляет теневые цены показателей качества ГО ДТ методом конечных разностей (§5.9).
    """
    pol = policy or PolicyConfig()
    base_prob = build_blend_problem(godt, tanks, prices, pol)
    base_res = solve_blend(base_prob)
    b_cost = base_cost if base_cost is not None else base_res.cost_per_ton

    steps: Tuple[Tuple[str, float], ...] = (
        ("S", 0.1),
        ("E360", 0.5),
        ("FLASH", 0.5),
        ("CFPP", 0.5),
        ("CN", 0.2),
        ("D15", 1.0),
    )

    out: List[BlendPrice] = []
    base_godt_dict: Dict[str, float] = {}
    if isinstance(godt, dict):
        base_godt_dict = dict(godt)
    else:
        base_godt_dict = {
            "S_ppm": 8.6,
            "Flash": 68.0,
            "T95": 347.0,
            "E360": 96.0,
            "D15": 836.1,
            "CFPP": -6.0,
            "CN": 53.75,
        }

    for prop, step in steps:
        bumped = dict(base_godt_dict)
        prop_key = "S_ppm" if prop == "S" else prop
        bumped[prop_key] = bumped.get(prop_key, 10.0) + step

        prob_bump = build_blend_problem(bumped, tanks, prices, pol)
        cost_bump = solve_blend(prob_bump).cost_per_ton
        rub_per_unit = (cost_bump - b_cost) / step

        out.append(
            BlendPrice(
                prop=f"GODT.{prop}",
                rub_per_unit_per_t=round(float(rub_per_unit), 2),
                method="finite_difference",
                step=step,
            )
        )

    return tuple(out)


def certify_blend(
    cand: Any,
    godt_forecast: Any,
    tanks: Mapping[str, Any],
    prices: Optional[Mapping[str, float]] = None,
    policy: Optional[PolicyConfig] = None,
    hold_cost: float = 60000.0,
    product_tph: float = 215.0,
) -> BlendingCertificate:
    """
    Сертификация блендинга согласно спецификации §5.9.
    """
    pol = policy or PolicyConfig()
    cand_sig = getattr(cand, "signature", str(cand))
    cand_round = getattr(cand, "round", 0)

    prob = build_blend_problem(godt_forecast, tanks, prices, pol)
    res = solve_blend(prob)

    status = "FEASIBLE" if res.status == "FEASIBLE" else "INFEASIBLE_ELASTIC"
    utility_delta = -(res.cost_per_ton - hold_cost) * product_tph if res.status == "FEASIBLE" else 0.0

    b_prices = godt_prices(godt_forecast, tanks, prices, pol, base_cost=res.cost_per_ton)

    return BlendingCertificate(
        candidate=cand_sig,
        round=cand_round,
        status=status,
        cost_rub_per_t=res.cost_per_ton,
        elastic_violation=res.elastic_violations,
        binding=tuple(res.binding_constraints),
        shares=res.shares,
        product_evaluations=(),
        prices=b_prices,
        utility_delta_rub_h=round(utility_delta, 2),
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
        if not res.success:
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
    kero_tank = tanks["Kerosene"]
    gasoil_tank = tanks["Gasoil"]

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
            "E360": 96.0,
        }
        props_source["mode"] = "fallback_telemetry"

    # Сверка параметров резервуарного парка из тегов
    tags_dict = state.get("tags") or {}
    if "TANK_KEROSENE_MASS" in tags_dict:
        kero_tank.stock_t = float(tags_dict["TANK_KEROSENE_MASS"])
    if "TANK_GASOIL_MASS" in tags_dict:
        gasoil_tank.stock_t = float(tags_dict["TANK_GASOIL_MASS"])
    if "TANK_GODT_CFPP" in tags_dict:
        godt_props["CFPP"] = float(tags_dict["TANK_GODT_CFPP"])
        godt_tank.props["CFPP"] = float(tags_dict["TANK_GODT_CFPP"])

    # Формируем 3 компонента для LP задачи
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

    # T95 товарного топлива (tz:598, PDF): к прогнозу T95 ГО ДТ добавляется запас 2σ(age)
    t95_offset = stat_offset(SIGMA_T95_C, lims_age_from_state(state), QUALITY_Z)
    props_source["T95"] = f"прогноз + {QUALITY_Z:g}σ ({t95_offset:.2f} °C)"

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
        t95_c=godt_props["T95"] + t95_offset,
        e360_pct=godt_props.get("E360", 96.0),
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
        t95_max=BLEND_T95_MAX,
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
                pareto=state.get("pareto"),
                audit_reports=state.get("audit_reports"),
            )
            updated_rec = final_rec.model_copy(update={"markdown_report": updated_report})
            result_update["final_recommendation"] = updated_rec
        except Exception:
            pass

    return result_update
