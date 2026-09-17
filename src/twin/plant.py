"""Имитатор технологической установки для реплея сценариев и тестов замкнутого контура (PlantSimulator v2).

Стенд v2 реализует физически адекватную модель объекта управления:
- Рассогласование модели (plant-model mismatch): варьирование параметров кинетики и гидравлики
  (E_h/R ±15%, активность k_h [0.8; 1.2], множитель перепада Р-202 (fouling), dF30/dT55);
- График ЛИМС: разрыв во времени между моментом отбора (sampled_at) и появлением результата (available_at, 2-8 часов);
- Отказы ПАК: симуляция залипания (frozen), дрейфа, аппаратного клампинга (307.0/313.0) и NaN;
- Выдача фактических измеренных положений регулирующих органов (MV) в телеметрии;
- Метод truth(): истинные физические значения технологических параметров без инструментального шума и отказов.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

import numpy as np

from src.twin.chain import FullChainTwin
from src.twin.params import TwinParams, load_params

MV_TAGS: Dict[str, str] = {
    "HT_FEED_SP": "HT_F9",
    "HT_TIN_SP": "HT_T6",
    "HT_P_SP": "HT_P13",
    "HT_GOR_SP": "HT_GOR",
    "AVT_T55_SP": "AVT_T55",
}

ONLINE_OUTPUT_TAGS: Dict[str, Tuple[str, ...]] = {
    "HT_S_PRODUCT": ("HT_Q21",),
    "HT_T_OUT": ("HT_T11",),
    "HT_FLASH": ("HT_T18",),
    "HT_D15_PRODUCT": ("PAK_D15", "24-2000:GODT:D15"),
    "HT_T95_PRODUCT": ("24-2000:GODT:T95",),
}

# Для обратной совместимости с v1
OUTPUT_TAGS: Dict[str, tuple] = {
    "HT_S_PRODUCT": ("HT_Q21", "LIMS_HT_S"),
    "HT_T_OUT": ("HT_T11",),
    "HT_FLASH": ("HT_T18",),
}

LIMS_PROPERTY_TAGS: Dict[str, str] = {
    "HT_S_PRODUCT": "LIMS_HT_S",
    "HT_FLASH": "LIMS_HT_FLASH",
    "HT_D15_PRODUCT": "LIMS_HT_D15",
    "HT_T95_PRODUCT": "LIMS_HT_T95",
}


@dataclass
class PlantMismatch:
    """Параметры рассогласования модели двойника и физической установки."""
    e_h_r_factor: float = 1.0          # Множитель E_h/R (±15% -> [0.85; 1.15])
    k_h_factor: float = 1.0            # Множитель активности k_h [0.8; 1.2]
    fouling_factor: float = 1.0        # Множитель гидравлического сопротивления/засорения Р-202
    df30_dt55: Optional[float] = None  # Отклик AVT_F30 на T55 из диапазона архива [-0.28; 1.57]

    @classmethod
    def random(cls, seed: int = 0) -> PlantMismatch:
        """Генерация случайного физически допустимого рассогласования параметров установки."""
        rng = np.random.default_rng(seed)
        return cls(
            e_h_r_factor=float(rng.uniform(0.85, 1.15)),
            k_h_factor=float(rng.uniform(0.8, 1.2)),
            fouling_factor=float(rng.uniform(1.0, 1.3)),
            df30_dt55=float(rng.uniform(-0.28, 1.57)),
        )


@dataclass
class SensorFault:
    """Конфигурация отказа измерительного прибора / датчика."""
    fault_type: Literal["frozen", "drift", "clamping", "nan"]
    value: Optional[float] = None
    drift_rate_per_step: float = 0.0
    drift_start_step: int = 0


@dataclass
class LimsSample:
    """Проба лаборатории LIMS с временными метками отбора и готовности анализа."""
    sampled_at_step: int
    sampled_at_hour: float
    available_at_step: int
    available_at_hour: float
    values: Dict[str, float]


class PlantSimulator:
    """Установка-имитатор стенда v2: динамический отклик, рассогласование, отказы, график ЛИМС."""

    def __init__(
        self,
        tags: Mapping[str, float],
        params: Optional[TwinParams] = None,
        q21_noise_ppm: float = 0.0,
        seed: int = 0,
        mismatch: Optional[PlantMismatch] = None,
        enable_lims_schedule: bool = False,
        lims_interval_hours: float = 12.0,
        lims_delay_hours: float = 4.0,
    ) -> None:
        p = copy.deepcopy(params or load_params())
        self.mismatch = mismatch or PlantMismatch()

        # Применение рассогласования модели к физической установке
        p.reactor.E_h_R *= self.mismatch.e_h_r_factor
        p.reactor.dp_ref_kpa *= self.mismatch.fouling_factor
        if self.mismatch.df30_dt55 is not None:
            p.feed.dF30_dT55 = self.mismatch.df30_dt55

        self.twin = FullChainTwin(p)
        if self.mismatch.k_h_factor != 1.0:
            self.twin.kinetics.k_h_ref *= self.mismatch.k_h_factor

        self.twin.initialize(tags)
        self.tags: Dict[str, float] = dict(tags)
        self.q21_noise_ppm = q21_noise_ppm
        self.seed = seed
        self.rng = np.random.default_rng(seed)

        # Конфигурация времени и ЛИМС
        self.step_count: int = 0
        self.dt_min: float = self.twin.dt_min
        self.enable_lims_schedule = enable_lims_schedule
        self.lims_interval_hours = lims_interval_hours
        self.lims_delay_hours = lims_delay_hours
        self.pending_lims_samples: List[LimsSample] = []
        self.last_lims_sample_hour: float = 0.0

        # Конфигурация отказов ПАК
        self.faults: Dict[str, SensorFault] = {}

        # Истинные значения (без шумов и отказов)
        self._last_raw_output: Dict[str, float] = self.twin.steady_state(self.twin.u_current)

    @property
    def current_hour(self) -> float:
        """Текущее время имитации в часах."""
        return self.step_count * (self.dt_min / 60.0)

    @property
    def measured_mvs(self) -> Dict[str, float]:
        """Фактические измеренные положения регулирующих органов (MV)."""
        return {
            "HT_FEED_SP": float(self.tags.get("HT_F9", self.twin.u_current["HT_FEED_SP"])),
            "HT_TIN_SP": float(self.tags.get("HT_T6", self.twin.u_current["HT_TIN_SP"])),
            "HT_P_SP": float(self.tags.get("HT_P13", self.twin.u_current["HT_P_SP"])),
            "HT_GOR_SP": float(self.tags.get("HT_GOR", self.twin.u_current["HT_GOR_SP"])),
            "AVT_T55_SP": float(self.tags.get("AVT_T55", self.twin.u_current["AVT_T55_SP"])),
        }

    def set_fault(
        self,
        tag: str,
        fault_type: Literal["frozen", "drift", "clamping", "nan"],
        value: Optional[float] = None,
        drift_rate_per_step: float = 0.0,
    ) -> None:
        """Настройка симуляции отказа датчика/канала ПАК."""
        if fault_type == "frozen" and value is None:
            value = float(self.tags.get(tag, 0.0))
        self.faults[tag] = SensorFault(
            fault_type=fault_type,
            value=value,
            drift_rate_per_step=drift_rate_per_step,
            drift_start_step=self.step_count,
        )

    def clear_fault(self, tag: str) -> None:
        """Сброс отказа конкретного датчика."""
        self.faults.pop(tag, None)

    def clear_faults(self) -> None:
        """Сброс всех отказов датчиков."""
        self.faults.clear()

    def schedule_lims_sample(self, delay_hours: Optional[float] = None) -> None:
        """Планирование отбора пробы ЛИМС на текущем такте с задержкой лаборатории."""
        delay = delay_hours if delay_hours is not None else self.lims_delay_hours
        steps_delay = max(1, int(round((delay * 60.0) / self.dt_min)))
        sample_values = {
            tag: float(self._last_raw_output.get(prop, self.tags.get(tag, 0.0)))
            for prop, tag in LIMS_PROPERTY_TAGS.items()
        }
        sample = LimsSample(
            sampled_at_step=self.step_count,
            sampled_at_hour=self.current_hour,
            available_at_step=self.step_count + steps_delay,
            available_at_hour=self.current_hour + delay,
            values=sample_values,
        )
        self.pending_lims_samples.append(sample)
        self.last_lims_sample_hour = self.current_hour

    def measure(self) -> Dict[str, float]:
        """Один такт установки и срез телеметрии с учетом динамики, отказов и ЛИМС."""
        self.step_count += 1
        out = self.twin.step(self.twin.u_current)
        self._last_raw_output = dict(out)

        # 1. Обновление онлайн измерений
        for key, tag_names in ONLINE_OUTPUT_TAGS.items():
            if key in out:
                for name in tag_names:
                    self.tags[name] = float(out[key])

        # 2. Обновление измеренных положений регулирующих органов (MV feedback)
        # Динамические температуры соответствуют выходам звеньев FOPDT:
        self.tags["HT_T6"] = float(out.get("HT_T_IN", self.twin.u_current["HT_TIN_SP"]))
        self.tags["AVT_T55"] = float(out.get("AVT_T55", self.twin.u_current["AVT_T55_SP"]))
        self.tags["HT_F9"] = float(self.twin.u_current["HT_FEED_SP"])
        self.tags["HT_P13"] = float(self.twin.u_current["HT_P_SP"])
        self.tags["HT_GOR"] = float(self.twin.u_current["HT_GOR_SP"])

        # Фактические уставки регуляторов (SP feedback)
        for sp_name in ("HT_FEED_SP", "HT_TIN_SP", "HT_P_SP", "HT_GOR_SP", "AVT_T55_SP"):
            if sp_name in self.twin.u_current:
                self.tags[sp_name] = float(self.twin.u_current[sp_name])

        # 3. Обработка графика ЛИМС
        if self.enable_lims_schedule:
            # Автоматический отбор проб по расписанию
            if self.current_hour - self.last_lims_sample_hour >= self.lims_interval_hours:
                self.schedule_lims_sample()

            # Применение готовых результатов анализа
            ready_samples = [s for s in self.pending_lims_samples if self.step_count >= s.available_at_step]
            if ready_samples:
                latest = ready_samples[-1]
                for tag, val in latest.values.items():
                    self.tags[tag] = val
                age_hours = (self.step_count - latest.sampled_at_step) * (self.dt_min / 60.0)
                self.tags["lims_age_hours"] = age_hours
                self.tags["lims_sampled_at_hours_ago"] = age_hours
                # Удаляем примененные пробы
                self.pending_lims_samples = [s for s in self.pending_lims_samples if s not in ready_samples]
            else:
                # Возраст ЛИМС растет со временем
                if "lims_age_hours" in self.tags:
                    self.tags["lims_age_hours"] += self.dt_min / 60.0
        else:
            # Режим обратной совместимости: обновление LIMS_HT_S напрямую
            self.tags["LIMS_HT_S"] = float(out["HT_S_PRODUCT"])

        snapshot = dict(self.tags)

        # 4. Шум поточного анализатора серы HT_Q21
        if self.q21_noise_ppm > 0.0 and "HT_Q21" not in self.faults:
            snapshot["HT_Q21"] += float(self.rng.normal(0.0, self.q21_noise_ppm))

        # 5. Применение отказов датчиков (ПАК)
        for tag, fault in self.faults.items():
            if fault.fault_type == "nan":
                snapshot[tag] = float("nan")
            elif fault.fault_type == "clamping":
                snapshot[tag] = float(fault.value if fault.value is not None else 307.0)
            elif fault.fault_type == "frozen":
                snapshot[tag] = float(fault.value if fault.value is not None else self.tags.get(tag, 0.0))
            elif fault.fault_type == "drift":
                steps = self.step_count - fault.drift_start_step
                drift = fault.drift_rate_per_step * max(0, steps)
                base = float(snapshot.get(tag, 0.0))
                snapshot[tag] = base + drift

        # Дополнительно передаем измеренные положения MV
        snapshot["measured_mvs"] = dict(self.measured_mvs)

        return snapshot

    def apply(self, delta_u: Mapping[str, float]) -> None:
        """Применение одобренного оператором вектора приращений уставок к приводам установки."""
        for mv, delta in delta_u.items():
            if mv in self.twin._u_current:
                self.twin._u_current[mv] += float(delta)

    def truth(self) -> Dict[str, float]:
        """Истинные технологические параметры установки без инструментального шума КИП и отказов."""
        out = self._last_raw_output
        return {
            "HT_S_PRODUCT": float(out["HT_S_PRODUCT"]),
            "HT_DP_KPA": float(out["HT_DP_KPA"]),
            "HT_T_OUT": float(out["HT_T_OUT"]),
            "HT_T11": float(out["HT_T_OUT"]),
            "AVT_T55": float(out["AVT_T55"]),
            "T55": float(out["AVT_T55"]),
            "HT_FLASH": float(out["HT_FLASH"]),
            "HT_T_IN": float(out["HT_T_IN"]),
            "HT_T6": float(out["HT_T_IN"]),
            "HT_BED_MEAN": float(out["HT_BED_MEAN"]),
            "HT_D15_PRODUCT": float(out["HT_D15_PRODUCT"]),
            "HT_T95_PRODUCT": float(out["HT_T95_PRODUCT"]),
            "HT_CFPP_PRODUCT": float(out["HT_CFPP_PRODUCT"]),
            "HT_CN_PRODUCT": float(out["HT_CN_PRODUCT"]),
            "AVT_DIESEL_TPH": float(out["AVT_DIESEL_TPH"]),
            "HT_FEED_TO_AVT": float(out["HT_FEED_TO_AVT"]),
            "HT_F9": float(self.twin.u_current["HT_FEED_SP"]),
            "HT_P13": float(self.twin.u_current["HT_P_SP"]),
            "HT_GOR": float(self.twin.u_current["HT_GOR_SP"]),
        }

