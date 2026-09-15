"""Модуль оптимизации поточного блендинга (In-Line Blending Optimization).

Реализует требования System_Design.md (Шаг 3) и mvp_third_step.md:
1. Нелинейная модель температуры вспышки Пенски-Мартенса (индекс Вики-Читтендена FBI)
   с точной линеаризацией для симплекс-метода HiGHS.
2. Кусочно-линейная аппроксимация изотермы насыщения депрессорно-диспергирующей присадки
   (ДДП, изотерма Ленгмюра с предельным насыщением 9.76 °C при 1500 ppm).
3. Жесткая блокировка «Dilution Loophole» (запрет отмывки некондиционного гидрогенизата
   с серой > 10.0 ppm керосином).
4. Минимизация себестоимости товарного дизельного топлива стандарта ГОСТ 32511-2013 (Евро-5).
5. Мотивированный регламентный отказ (Graceful Failure) при несовместности ограничений.
"""

from __future__ import annotations

import math
from typing import Optional, Dict, Any
import numpy as np
from pydantic import BaseModel, Field
from scipy.optimize import linprog

from src.agents.state import MasGraphState


class BlendingRequest(BaseModel):
    """Параметры компонентов и целевые требования на блендинг."""
    
    sulfur_diesel: float = Field(description="Содержание серы в гидрогенизате, мг/кг (ppm)")
    cfpp_diesel: float = Field(description="Предельная температура фильтруемости дизеля, °C")
    flash_diesel: float = Field(description="Температура вспышки дизеля в закрытом тигле, °C")
    
    target_cfpp: float = Field(default=-15.0, description="Целевая ПТФ товарного ДТ, °C (сорт E: -15°C, сорт F: -20°C)")
    target_flash: float = Field(default=56.0, description="Целевая температура вспышки, °C (ГОСТ 55.0 + 1.0 буфер)")
    
    price_diesel: float = Field(default=60000.0, description="Себестоимость дизельного гидрогенизата, руб/т")
    price_kerosene: float = Field(default=85000.0, description="Цена керосиновой фракции ТС-1, руб/т")
    price_ddp: float = Field(default=1500000.0, description="Цена присадки ДДП, руб/т")
    
    max_kerosene_vol: float = Field(default=0.20, description="Максимальная объемная доля керосина (20%)")
    min_diesel_vol: float = Field(default=0.50, description="Минимальная объемная доля дизеля (50%)")
    max_ddp_ppm: float = Field(default=1500.0, description="Максимальная дозировка ДДП, ppm (граница Ленгмюра)")


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


class OptimizerBlending:
    """
    Оптимизатор рецептуры смешения на базе SciPy HiGHS LP.
    Учитывает нелинейность вспышки и изотерму насыщения присадки.
    """

    # Коэффициенты индекса смешения вспышки Вики-Читтендена (Wickey-Chittenden)
    FBI_A: float = 4.1485
    FBI_B: float = 0.0601

    # Характеристики керосиновой фракции ТС-1
    KERO_SULFUR: float = 2.0     # ppm
    KERO_CFPP: float = -45.0     # °C
    KERO_FLASH: float = 42.0     # °C

    @classmethod
    def calculate_fbi(cls, t_flash: float) -> float:
        """
        Расчет индекса смешения вспышки Вики-Читтендена:
        FBI = 10^(4.1485 - 0.0601 * T)
        """
        return 10.0 ** (cls.FBI_A - cls.FBI_B * t_flash)

    @classmethod
    def calculate_flash_from_fbi(cls, fbi: float) -> float:
        """
        Аналитическое обращение индекса вспышки:
        T_flash = (4.1485 - log10(FBI)) / 0.0601
        """
        safe_fbi = max(float(fbi), 1e-6)
        return (cls.FBI_A - math.log10(safe_fbi)) / cls.FBI_B

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
    ) -> BlendingResult:
        """
        Решает задачу линейного программирования (SciPy HiGHS) для поиска оптимального рецепта.
        """
        # 1. Жесткая блокировка «Dilution Loophole» (Правило 3 AGENTS.md)
        # Категорически запрещено подавать некондиционный гидрогенизат (>10 ppm) на блендинг
        if sulfur_diesel > 10.0:
            return BlendingResult(
                success=False,
                error_message=(
                    f"BLOCKED: Dilution Loophole. Сера дизельного гидрогенизата ({sulfur_diesel:.1f} ppm) "
                    f"превышает норматив ГОСТ (10.0 ppm). Разбавление керосином запрещено правилами безопасности."
                )
            )

        # 2. Индексы вспышки компонентов и таргета
        fbi_diesel = cls.calculate_fbi(flash_diesel)
        fbi_kero = cls.calculate_fbi(cls.KERO_FLASH)
        fbi_target = cls.calculate_fbi(target_flash)

        # 3. Линеаризация присадки ДДП (Кусочно-линейная аппроксимация Ленгмюра):
        # x_ddp1: 0..500 ppm, депрессия до 6.0 °C (наклон = -6.0 / 500.0 = -0.012 °C/ppm)
        # x_ddp2: 0..1000 ppm, депрессия до 3.76 °C (наклон = -3.76 / 1000.0 = -0.00376 °C/ppm)
        # Суммарная максимальная депрессия присадки = 9.76 °C.
        # Вектор переменных: x = [v_diesel, v_kerosene, x_ddp1, x_ddp2]
        c = np.array([
            price_diesel,
            price_kerosene,
            price_ddp * 1e-6,
            price_ddp * 1e-6
        ], dtype=float)

        # Ограничения вида A_ub * x <= b_ub
        A_ub = [
            # Ограничение 1: Температура вспышки (FBI_mix <= FBI_target)
            [fbi_diesel, fbi_kero, 0.0, 0.0],
            # Ограничение 2: Предельная температура фильтруемости (CFPP_mix <= target_cfpp)
            [cfpp_diesel, cls.KERO_CFPP, -0.012, -0.00376],
            # Ограничение 3: Суммарный лимит присадки (x_ddp1 + x_ddp2 <= max_ddp_ppm)
            [0.0, 0.0, 1.0, 1.0],
        ]
        b_ub = [
            fbi_target,
            target_cfpp,
            max_ddp_ppm,
        ]

        # Ограничение вида A_eq * x == b_eq: объемный баланс базы топлива
        A_eq = [[1.0, 1.0, 0.0, 0.0]]
        b_eq = [1.0]

        # Границы переменных
        bounds = [
            (min_diesel_vol, 1.0),        # v_diesel: от 50% до 100%
            (0.0, max_kerosene_vol),      # v_kerosene: от 0% до 20%
            (0.0, 500.0),                 # x_ddp1: первые 500 ppm
            (0.0, 1000.0),                # x_ddp2: следующие 1000 ppm
        ]

        # 4. Запуск оптимизатора HiGHS
        res = linprog(
            c,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=bounds,
            method="highs"
        )

        # 5. Обработка несовместности ограничений (Infeasible LP)
        if not res.success:
            return BlendingResult(
                success=False,
                error_message=(
                    f"LP Solver failed ({res.message}). "
                    f"Невозможно одновременно обеспечить ПТФ <= {target_cfpp:.1f}°C и вспышку >= {target_flash:.1f}°C "
                    f"при заданном качестве гидрогенизата (CFPP={cfpp_diesel:.1f}°C, Flash={flash_diesel:.1f}°C), "
                    f"максимальном вовлечении керосина {max_kerosene_vol*100:.0f}% и пределе насыщения ДДП ({max_ddp_ppm} ppm)."
                )
            )

        # 6. Извлечение результатов и валидация
        v_d, v_k, x_d1, x_d2 = res.x
        total_ddp_ppm = x_d1 + x_d2

        fbi_mix = v_d * fbi_diesel + v_k * fbi_kero
        actual_flash = cls.calculate_flash_from_fbi(fbi_mix)
        actual_cfpp = v_d * cfpp_diesel + v_k * cls.KERO_CFPP - 0.012 * x_d1 - 0.00376 * x_d2
        actual_sulfur = v_d * sulfur_diesel + v_k * cls.KERO_SULFUR
        cost_ton = float(res.fun)

        return BlendingResult(
            success=True,
            v_diesel=round(float(v_d), 4),
            v_kerosene=round(float(v_k), 4),
            v_ddp_ppm=round(float(total_ddp_ppm), 1),
            expected_cfpp=round(float(actual_cfpp), 2),
            expected_flash=round(float(actual_flash), 2),
            expected_sulfur=round(float(actual_sulfur), 2),
            cost_per_ton=round(cost_ton, 2),
            fbi_blend=round(float(fbi_mix), 4),
            error_message=None
        )


def node_blending_agent(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел графа LangGraph: Агент оптимизации поточного блендинга (Fuel Blending Agent).
    
    Вызывается после выбора допустимого кандидата технологического режима.
    Использует фактические или прогнозные показатели гидрогенизата для выработки рецепта.
    """
    telemetry = state.get("raw_telemetry")
    
    # Извлечение параметров качества гидрогенизата
    sulfur = getattr(telemetry, "Sulfur", 8.5) if telemetry else 8.5
    # Если в телеметрии присутствуют расчетные значения ВАК
    cfpp = getattr(telemetry, "24-2000:GODT:CFPP", -5.0) if telemetry else -5.0
    flash = getattr(telemetry, "flash_diesel", 65.0) if telemetry else 65.0

    recipe = OptimizerBlending.solve_recipe(
        sulfur_diesel=float(sulfur),
        cfpp_diesel=float(cfpp),
        flash_diesel=float(flash),
    )

    return {"blending_recipe": recipe}
