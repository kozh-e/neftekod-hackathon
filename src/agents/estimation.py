"""Оценка состояния технологического комплекса (StateEstimator) и калибровка по ЛИМС.

Реализует спецификацию §4.2, §5.1 implementation_plan_v3.md:
1. Кольцевые буферы 48 ч: хранение пар (t, pak, model_raw, measurements);
2. Калибровка ПАК и модели по новым пробам ЛИМС на момент ОТБОРА (sampled_at);
3. Скалярный фильтр Калмана в лог-домене для серы;
4. Рост неопределенности калибровки со временем (Q_DRIFT_LOG);
5. Расчет текущей оценки качества и неопределенности с якорем (PAK+bias или MODEL+bias);
6. Вычисление масштабирующих множителей модели (factors: GODT.S, HT_DP_KPA);
7. Формирование неизменяемого контракта PlantEstimate.
"""

from __future__ import annotations

from collections import deque
import datetime
import math
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.contracts import (
    Frozen,
    PlantEstimate,
    Provenance,
    ProvenanceKind,
    QualityEstimate,
    QualityProp,
    SignalQuality,
)
from src.agents.policy import PolicyConfig

# Статистические параметры калибровки серы в лог-домене (DATA, ADR-12)
R_LIMS_LOG: float = 0.0025      # sigma_lims_rel ≈ 5% (дисперсия шума анализатора ЛИМС)
SIGMA_PAK_LOG: float = 0.0965   # 0.83 ppm / 8.6 ppm ≈ 0.0965 (повторяемость поточного ПАК)
SIGMA_MODEL_LOG: float = 0.25   # относительная неопределенность сырой кинетической модели
Q_DRIFT_LOG: float = 0.0005     # скорость дрейфа дисперсии калибровки в час

# Неопределенности других показателей в линейном домене
LINEAR_PROP_CONFIG = {
    "FLASH": {"sigma_meas": 4.78, "drift_per_h": 0.05, "unit": "°C", "ref": "ГОСТ 32511-2013 / DATA"},
    "T95": {"sigma_meas": 3.27, "drift_per_h": 0.04, "unit": "°C", "ref": "ВАК 24-2000 / DATA"},
    "D15": {"sigma_meas": 1.20, "drift_per_h": 0.01, "unit": "кг/м3", "ref": "ГОСТ 32511-2013 / DATA"},
    "CN": {"sigma_meas": 0.50, "drift_per_h": 0.005, "unit": "", "ref": "ГОСТ 32511-2013 / DATA"},
    "CFPP": {"sigma_meas": 1.00, "drift_per_h": 0.01, "unit": "°C", "ref": "ГОСТ 32511-2013 / DATA"},
    "E360": {"sigma_meas": 0.50, "drift_per_h": 0.005, "unit": "% об.", "ref": "ГОСТ 32511-2013 / DATA"},
}

# Допустимый порог рассогласования при сверке MV
RESYNC_TOL: Dict[str, float] = {
    "HT_FEED_SP": 1.0,
    "HT_TIN_SP": 0.5,
    "HT_P_SP": 0.02,
    "HT_GOR_SP": 5.0,
    "AVT_T55_SP": 0.5,
}

MV_TAG_MAP: Dict[str, str] = {
    "HT_FEED_SP": "HT_F9",
    "HT_TIN_SP": "HT_T6",
    "HT_P_SP": "HT_P13",
    "HT_GOR_SP": "HT_GOR",
    "AVT_T55_SP": "AVT_T55",
}


class LimsSample(Frozen):
    """Лабораторный анализ LIMS с метками времени отбора и готовности."""
    prop: QualityProp
    value: float
    sampled_at: datetime.datetime
    available_at: datetime.datetime
    stream: str = "GODT"


class KalmanState:
    """Скалярный фильтр Калмана для смещения (bias) и его дисперсии."""

    def __init__(self, b: float = 0.0, p: float = 0.01, last_time: Optional[datetime.datetime] = None):
        self.b: float = b
        self.P: float = p
        self.last_update: Optional[datetime.datetime] = last_time

    def predict(self, t_now: datetime.datetime, q_drift: float = Q_DRIFT_LOG) -> None:
        """Экстраполяция дисперсии калибровки со временем."""
        if self.last_update is not None and t_now > self.last_update:
            dt_h = (t_now - self.last_update).total_seconds() / 3600.0
            self.P += q_drift * max(0.0, dt_h)
            self.last_update = t_now
        elif self.last_update is None:
            self.last_update = t_now

    def update(self, innovation: float, r_noise: float, t_now: datetime.datetime) -> None:
        """Коррекция смещения по новому замеру (measurement update)."""
        k = self.P / (self.P + r_noise)
        self.b += k * (innovation - self.b)
        self.P = (1.0 - k) * self.P
        self.last_update = t_now


class HistoryBuffer:
    """Кольцевой буфер истории измерений и выходов двойника за 48 часов."""

    def __init__(self, max_hours: float = 48.0):
        self.max_seconds = max_hours * 3600.0
        self._history: Deque[Tuple[datetime.datetime, Dict[str, Any]]] = deque()

    def append(self, t: datetime.datetime, entry: Dict[str, Any]) -> None:
        self._history.append((t, entry))
        cutoff = t - datetime.timedelta(seconds=self.max_seconds)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def get_at(self, t_target: datetime.datetime) -> Optional[Dict[str, Any]]:
        """Ищет ближайшую запись в буфере к моменту t_target."""
        if not self._history:
            return None
        # Ищем ближайший элемент по времени
        best_entry = None
        best_dt = float("inf")
        for t, entry in self._history:
            dt = abs((t - t_target).total_seconds())
            if dt < best_dt:
                best_dt = dt
                best_entry = entry
            elif dt > best_dt:
                # Временной ряд упорядочен, можно остановиться при росте разницы
                break

        # Допускаем интерполяцию в пределах 30 минут
        if best_dt <= 1800.0:
            return best_entry
        return None

    def pak_at(self, t_target: datetime.datetime) -> Optional[float]:
        rec = self.get_at(t_target)
        if rec and "pak" in rec and rec["pak"] is not None:
            val = rec["pak"]
            if isinstance(val, (int, float)) and val > 0.0:
                return float(val)
        return None

    def model_at(self, t_target: datetime.datetime) -> Optional[float]:
        rec = self.get_at(t_target)
        if rec and "model_raw" in rec and rec["model_raw"] is not None:
            val = rec["model_raw"]
            if isinstance(val, (int, float)) and val > 0.0:
                return float(val)
        return None


# Alias for RingBuffer
RingBuffer = HistoryBuffer


class StateEstimator:
    """
    Оценщик технологического состояния с фильтрацией неопределенности и калибровкой.
    """

    def __init__(self):
        self.buffer = HistoryBuffer(max_hours=48.0)
        # Фильтры Калмана для серы (в лог-домене)
        self.pak_log_bias = KalmanState(b=0.0, p=R_LIMS_LOG)
        self.model_log_bias = KalmanState(b=0.0, p=SIGMA_MODEL_LOG ** 2)
        self.last_lims_sample_time: Optional[datetime.datetime] = None

        # Фильтры смещений для линейных параметров
        self.linear_biases: Dict[str, KalmanState] = {
            prop: KalmanState(b=0.0, p=cfg["sigma_meas"] ** 2)
            for prop, cfg in LINEAR_PROP_CONFIG.items()
        }

    def process_lims_sample(self, sample: LimsSample, t_now: datetime.datetime) -> None:
        """
        Калибровка смещений ПАК и модели по пробе ЛИМС на момент ОТБОРА (sampled_at).
        """
        if sample.prop == "S":
            # 1. Поиск исторических значений ПАК и модели на момент отбора
            pak_then = self.buffer.pak_at(sample.sampled_at)
            model_then = self.buffer.model_at(sample.sampled_at)

            # Если в буфере нет истории (первый запуск), используем текущие значения
            if model_then is None:
                model_then = sample.value
            if pak_then is None:
                pak_then = sample.value

            if pak_then is not None and pak_then > 0.0 and sample.value > 0.0:
                e_pak = math.log(sample.value) - math.log(pak_then)
                self.pak_log_bias.update(e_pak, R_LIMS_LOG, t_now)

            if model_then is not None and model_then > 0.0 and sample.value > 0.0:
                e_model = math.log(sample.value) - math.log(model_then)
                self.model_log_bias.update(e_model, R_LIMS_LOG, t_now)

            self.last_lims_sample_time = sample.sampled_at

        elif sample.prop in self.linear_biases:
            cfg = LINEAR_PROP_CONFIG[sample.prop]
            rec = self.buffer.get_at(sample.sampled_at)
            model_then = rec.get(sample.prop) if rec else None
            if model_then is not None:
                e = sample.value - float(model_then)
                self.linear_biases[sample.prop].update(e, cfg["sigma_meas"] ** 2, t_now)

    def update(
        self,
        tags: Mapping[str, Any],
        t_now: datetime.datetime,
        twin_raw_output: Mapping[str, float],
        u_twin_current: Mapping[str, float],
        new_lims: Sequence[LimsSample] = (),
        pak_quality: SignalQuality = SignalQuality.GOOD,
        twin_steps: int = 1,
    ) -> PlantEstimate:
        """
        Шаг обновления оценки состояния технологического комплекса (§5.1).
        """
        # 1. Запись текущих срезов в буфер 48 ч
        raw_s = float(twin_raw_output.get("HT_S_PRODUCT", 8.6))
        pak_val = tags.get("HT_Q21")
        pak_float = float(pak_val) if pak_val is not None and not (math.isnan(pak_val) or math.isinf(pak_val)) else None

        hist_entry: Dict[str, Any] = {
            "pak": pak_float if pak_quality == SignalQuality.GOOD else None,
            "model_raw": raw_s,
            "FLASH": float(twin_raw_output.get("HT_FLASH", 68.0)),
            "T95": float(twin_raw_output.get("HT_T95_PRODUCT", 347.0)),
            "D15": float(twin_raw_output.get("HT_D15_PRODUCT", 836.0)),
        }
        self.buffer.append(t_now, hist_entry)

        # 2. Обработка новых анализов ЛИМС
        for sample in new_lims:
            self.process_lims_sample(sample, t_now)

        # 3. Рост неопределенности калибровки со временем
        self.pak_log_bias.predict(t_now, Q_DRIFT_LOG)
        self.model_log_bias.predict(t_now, Q_DRIFT_LOG)

        # 4. Определение возраста последней калибровки
        if self.last_lims_sample_time is not None:
            calib_age_h = max(0.0, (t_now - self.last_lims_sample_time).total_seconds() / 3600.0)
        else:
            calib_age_h = float(tags.get("lims_age_hours", 0.0))

        # 5. Текущая оценка серы гидрогенизата ГО ДТ
        if pak_quality == SignalQuality.GOOD and pak_float is not None and pak_float > 0.0:
            log_s = math.log(pak_float) + self.pak_log_bias.b
            sigma_meas = SIGMA_PAK_LOG
            sigma_calib = math.sqrt(max(1e-6, self.pak_log_bias.P))
            anchor: Literal["PAK+bias", "MODEL+bias", "LIMS"] = "PAK+bias"
            s_value = math.exp(log_s)
        else:
            log_s = math.log(max(0.1, raw_s)) + self.model_log_bias.b
            sigma_meas = SIGMA_MODEL_LOG
            sigma_calib = math.sqrt(max(1e-6, self.model_log_bias.P))
            anchor = "MODEL+bias"
            s_value = math.exp(log_s)

        quality_estimates: Dict[str, QualityEstimate] = {
            "GODT.S": QualityEstimate(
                stream="GODT",
                prop="S",
                value=round(s_value, 3),
                domain="log",
                sigma_meas=sigma_meas,
                sigma_calib=sigma_calib,
                calib_age_h=calib_age_h,
                anchor=anchor,
                last_lims_sampled_at=self.last_lims_sample_time,
                provenance=Provenance(
                    kind=ProvenanceKind.DATA,
                    ref="scripts/estimate_quality_uncertainty.py / ADR-12",
                    note="Оценка серы гидрогенизата в лог-домене с поправкой Калмана",
                ),
            )
        }

        # Оценки других показателей качества
        for prop, cfg in LINEAR_PROP_CONFIG.items():
            filter_state = self.linear_biases[prop]
            filter_state.predict(t_now, cfg["drift_per_h"])
            raw_val = float(twin_raw_output.get(f"HT_{prop}_PRODUCT", twin_raw_output.get(f"HT_{prop}", 0.0)))
            val_corrected = raw_val + filter_state.b
            sigma_calib_lin = math.sqrt(max(1e-4, filter_state.P))

            quality_estimates[f"GODT.{prop}"] = QualityEstimate(
                stream="GODT",
                prop=prop,  # type: ignore
                value=round(val_corrected, 2),
                domain="linear",
                sigma_meas=cfg["sigma_meas"],
                sigma_calib=sigma_calib_lin,
                calib_age_h=calib_age_h,
                anchor="MODEL+bias",
                last_lims_sampled_at=self.last_lims_sample_time,
                provenance=Provenance(
                    kind=ProvenanceKind.DATA,
                    ref=cfg["ref"],
                    note=f"Оценка {prop} с аддитивной авторегрессионной поправкой",
                ),
            )

        # 6. Сверка MV и детекция ручных изменений
        u_actual: Dict[str, float] = {}
        manual_changes: List[str] = []
        for mv, tag in MV_TAG_MAP.items():
            if mv in tags and isinstance(tags[mv], (int, float)) and not math.isnan(tags[mv]):
                u_actual[mv] = float(tags[mv])
                curr_twin = u_twin_current.get(mv, u_actual[mv])
                tol = RESYNC_TOL.get(mv, 1.0)
                if abs(u_actual[mv] - curr_twin) > tol:
                    manual_changes.append(mv)
            elif tag in tags and isinstance(tags[tag], (int, float)) and not math.isnan(tags[tag]):
                u_actual[mv] = float(tags[tag])
                # Проверка расхождения с уставкой двойника
                curr_twin = u_twin_current.get(mv, u_actual[mv])
                tol = RESYNC_TOL.get(mv, 1.0)
                if abs(u_actual[mv] - curr_twin) > tol:
                    manual_changes.append(mv)
            else:
                u_actual[mv] = u_twin_current.get(mv, 0.0)

        # 7. Измеренные ограничения и возмущения
        disturbances: Dict[str, float] = {
            "AVT_F30": float(tags.get("AVT_F30", 128.3)),
            "AVT_F32": float(tags.get("AVT_F32", 81.6)),
            "AVT_F31": float(tags.get("AVT_F31", 540.5)),
            "AVT_P52": float(tags.get("AVT_P52", 0.045)),
            "HT_F14": float(tags.get("HT_F14", 6.05)),
        }

        measured_constraints: Dict[str, float] = {
            "AVT_T55": float(tags.get("AVT_T55", u_actual.get("AVT_T55_SP", 381.7))),
            "HT_DP_KPA": float(tags.get("HT_DP_KPA", tags.get("HT_P8", 177.0) * (980.665 if tags.get("HT_P8", 1.0) < 5.0 else 1.0))),
            "HT_T11": float(tags.get("HT_T11", 364.0)),
            "HT_GOR": float(tags.get("HT_GOR", u_actual.get("HT_GOR_SP", 360.0))),
            "AVT_F31": float(tags.get("AVT_F31", 540.5)),
            "AVT_P52": float(tags.get("AVT_P52", 0.045)),
        }

        # 8. Множители модели: сера и перепад Р-202 (фактор засорения катализатора)
        raw_dp = max(10.0, float(twin_raw_output.get("HT_DP_KPA", 177.0)))
        meas_dp = measured_constraints["HT_DP_KPA"]
        factors: Dict[str, float] = {
            "GODT.S": s_value / max(0.1, raw_s),
            "HT_DP_KPA": meas_dp / raw_dp,
        }

        return PlantEstimate(
            t=t_now,
            u_actual=u_actual,
            manual_changes=tuple(manual_changes),
            disturbances=disturbances,
            measured_constraints=measured_constraints,
            quality=quality_estimates,
            factors=factors,
            twin_steps_advanced=twin_steps,
        )
