"""Модуль управления демонстрационными сценариями пульта (R3).

Обеспечивает загрузку сценариев S1–S5, прогрев модельной истории, управление скоростью и инжекцию неисправностей.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from src.agents.scenarios import (
    scenario_1_normal_tags,
    scenario_2_quality_risk_tags,
    scenario_4_conflict_tags,
)
from src.twin.plant import PlantSimulator

if TYPE_CHECKING:
    from src.console.runtime import ConsoleSession


SCENARIO_TITLES = {
    "S1": "Нормальный режим: выход на оптимум",
    "S2": "Риск качества: утяжеление сырья",
    "S3d": "Отказ данных: потеря сигнала серы",
    "S4": "Сквозной консенсус МАС: конфликт ограничений",
    "S5": "Выход за огибающую оборудования",
}


def load_scenario(session_id: str, scenario: str, warmup_ticks: int = 48) -> ConsoleSession:
    """
    Инициализирует сессию выбранным технологическим сценарием,
    прогревает ее на указанное количество тактов и сохраняет в реестре.
    """
    from src.console.runtime import ConsoleSession, REGISTRY

    title = SCENARIO_TITLES.get(scenario, f"Сценарий {scenario}")

    # Создание физического симулятора PlantSimulator
    if scenario in ("S1", "S3d"):
        plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)
    elif scenario == "S2":
        # Для S2 начинаем с номинала, а за 18 тактов до конца прогрева включим утяжеление сырья
        plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)
    elif scenario == "S4":
        plant = PlantSimulator(scenario_4_conflict_tags(), q21_noise_ppm=0.05, seed=42)
    elif scenario == "S5":
        # S5: стартуем с предельных значений
        s5_tags = scenario_1_normal_tags()
        s5_tags["AVT_T55"] = 389.0
        s5_tags["HT_DP_KPA"] = 470.0
        plant = PlantSimulator(s5_tags, q21_noise_ppm=0.05, seed=42)
    else:
        plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)

    session = ConsoleSession(session_id=session_id, plant=plant)
    session.scenario_id = scenario
    session.scenario_title = title

    # Прогрев истории
    s2_switch_tick = max(0, warmup_ticks - 18)
    for t in range(warmup_ticks):
        if scenario == "S2" and t == s2_switch_tick:
            # Переключение сырья на утяжеленное
            s2_tags = scenario_2_quality_risk_tags()
            for k in ("AVT_F30", "AVT_F32", "HT_F14", "s_feed_ppm", "t95_feed_c"):
                if k in s2_tags:
                    session.plant.tags[k] = s2_tags[k]

        session.tick_single(warmup=True)

    # Пост-настройки для S3d
    if scenario == "S3d":
        session.plant.set_fault("HT_Q21", "nan")
        session.plant.tags["lims_age_hours"] = 26.0
        # Делаем 1 такт для фиксации отказа
        session.tick_single(warmup=False)

    REGISTRY.register(session)
    return session


def set_speed(session_id: str, seconds_per_tick: float) -> None:
    """Устанавливает интервал реального времени на такт (0 = пауза)."""
    from src.console.runtime import REGISTRY

    session = REGISTRY.get(session_id)
    with session.lock:
        session.seconds_per_tick = float(seconds_per_tick)


def set_fault(
    session_id: str,
    tag: str,
    fault_type: str,
    lims_age_hours: Optional[float] = None,
) -> None:
    """Инжектирует неисправность КИПиА или задержку анализа LIMS."""
    from src.console.runtime import REGISTRY

    session = REGISTRY.get(session_id)
    with session.lock:
        if fault_type == "clear":
            session.plant.clear_fault(tag)
        else:
            session.plant.set_fault(tag, fault_type)

        if lims_age_hours is not None:
            session.plant.tags["lims_age_hours"] = float(lims_age_hours)
