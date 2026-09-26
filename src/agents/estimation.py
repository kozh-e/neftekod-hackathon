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
import logging
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
# Повторяемость поточного ПАК в лог-домене. DATA: notebooks/01_model_evaluation.ipynb §4.2,
# робастная оценка IQR/1.349 остатков nowcast на Train (<= 2025-06-30, n = 983) с вычитанием
# медианного sigma_calib. Прежнее значение 0.0965 (0.83 ppm / 8.6 ppm) занижало интервал.
SIGMA_PAK_LOG: float = 0.11564
SIGMA_MODEL_LOG: float = 0.25   # относительная неопределенность сырой кинетической модели
Q_DRIFT_LOG: float = 0.0005     # скорость дрейфа дисперсии калибровки в час

logger = logging.getLogger(__name__)

# Отбраковка недостоверных проб ЛИМС (ADR-12).
#
# В архиве встречаются физически невозможные лабораторные значения: сера гидрогенизата
# до 2120 мг/кг при спецификации 10 — это уровень СЫРЬЯ, то есть ошибка ввода или
# перепутанная точка отбора. Без отбраковки одна такая проба сдвигает смещение Калмана
# на порядки: R_LIMS_LOG мал, и коэффициент усиления близок к единице.
#
# ОТБРАКОВКА ИДЕТ ТОЛЬКО ПО ФИЗИЧЕСКОЙ ПРАВДОПОДОБНОСТИ, НЕ ПО СТАТИСТИКЕ.
# Робастный статистический детектор (|x - медиана| > k * MAD) здесь применять нельзя:
# показатели удерживаются регулятором в узком коридоре, поэтому MAD мал, и любое
# ДЕЙСТВИТЕЛЬНОЕ отклонение режима выглядит аномалией. На архиве такой детектор
# отбраковывал реальные пробы — серу 2.5 мг/кг и вспышку 54 C, то есть в том числе
# фактическое нарушение ГОСТ. Выбрасывать измерения, ради обнаружения которых система
# и построена, недопустимо: это скрывает нарушение, а не фильтрует шум.
#
# Границы заданы по физике потока и требованиям ГОСТ 32511-2013 с большим запасом:
# отсекается только то, что не может быть результатом анализа данного потока.
LIMS_PLAUSIBLE_RANGE: Dict[str, Tuple[float, float]] = {
    "S":     (0.01, 100.0),    # мг/кг. Глубокая ГО дает единицы; 100+ — это сера сырья
    "FLASH": (20.0, 130.0),    # °C, температура вспышки в закрытом тигле
    "T95":   (150.0, 420.0),   # °C, 95 % выкипания дизельной фракции
    "D15":   (650.0, 1000.0),  # кг/м3, плотность при 15 °C
    "CFPP":  (-70.0, 40.0),    # °C, предельная температура фильтруемости
    "CN":    (10.0, 90.0),     # цетановое число
    "E360":  (0.0, 100.0),     # % об., доля выкипающего до 360 °C
}

# Неопределенности других показателей в линейном домене.
# sigma_meas для FLASH, T95, D15 и CFPP: DATA, notebooks/01_model_evaluation.ipynb §4.2 —
# робастная оценка IQR/1.349 остатков nowcast на Train (<= 2025-06-30) с вычитанием
# медианного sigma_calib, чтобы вклад калибровки не учитывался дважды.
# CN и E360 оставлены прежними: лабораторных данных для оценки нет (ЦЧ — 42 пробы за
# 3.6 года, доли выкипания при 360 °C в ЛИМС нет вовсе).
LINEAR_PROP_CONFIG = {
    "FLASH": {"sigma_meas": 3.85, "drift_per_h": 0.05, "unit": "°C", "ref": "ГОСТ 32511-2013 / DATA"},
    "T95": {"sigma_meas": 4.95, "drift_per_h": 0.04, "unit": "°C", "ref": "ВАК 24-2000 / DATA"},
    "D15": {"sigma_meas": 1.07, "drift_per_h": 0.01, "unit": "кг/м3", "ref": "ГОСТ 32511-2013 / DATA"},
    "CN": {"sigma_meas": 0.50, "drift_per_h": 0.005, "unit": "", "ref": "ГОСТ 32511-2013 / ASSUMPTION"},
    "CFPP": {"sigma_meas": 1.27, "drift_per_h": 0.01, "unit": "°C", "ref": "ГОСТ 32511-2013 / DATA"},
    "E360": {"sigma_meas": 0.50, "drift_per_h": 0.005, "unit": "% об.", "ref": "ГОСТ 32511-2013 / ASSUMPTION"},
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

        # Журнал отбракованных проб ЛИМС
        self.rejected_lims: List[Dict[str, Any]] = []

    @staticmethod
    def _lims_implausible(sample: LimsSample) -> Optional[str]:
        """
        Проверка пробы ЛИМС на физическую правдоподобность.

        Возвращает причину отбраковки или None, если проба принимается.
        Отсекается только то, что не может быть результатом анализа данного потока:
        нечисловое значение или выход за физические границы LIMS_PLAUSIBLE_RANGE.
        Реальные отклонения режима, включая нарушения спецификации, проходят —
        именно ради них система и работает.
        """
        value = sample.value
        if value is None or math.isnan(value) or math.isinf(value):
            return "нечисловое значение"
        bounds = LIMS_PLAUSIBLE_RANGE.get(sample.prop)
        if bounds is None:
            return None
        lo, hi = bounds
        if value < lo or value > hi:
            return f"вне физического диапазона [{lo:g}; {hi:g}]"
        return None

    def process_lims_sample(self, sample: LimsSample, t_now: datetime.datetime) -> None:
        """
        Калибровка смещений ПАК и модели по пробе ЛИМС на момент ОТБОРА (sampled_at).

        Физически невозможные пробы отбраковываются и в фильтры не попадают.
        Факт отбраковки фиксируется в self.rejected_lims и в журнале: лабораторный
        результат по ТЗ является контрольным фактом, и система не вправе игнорировать
        его молча. Возраст калибровки при отбраковке не обновляется — sigma продолжает
        расти, то есть поведение остаётся консервативным.
        """
        reason = self._lims_implausible(sample)
        if reason is not None:
            self.rejected_lims.append({
                "prop": sample.prop,
                "value": float(sample.value) if sample.value is not None else None,
                "sampled_at": sample.sampled_at,
                "rejected_at": t_now,
                "reason": reason,
            })
            logger.warning(
                "Проба ЛИМС отбракована как недостоверная: %s = %.4g (отбор %s): %s",
                sample.prop, sample.value, sample.sampled_at, reason,
            )
            return

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
        }
        # В буфер пишутся ВСЕ показатели из LINEAR_PROP_CONFIG по тому же правилу
        # поиска ключа, что и при расчете оценки ниже. Раньше здесь был жестко
        # заданный список FLASH/T95/D15, из-за чего process_lims_sample не находил
        # записи для CFPP, CN и E360 и молча пропускал их калибровку: смещение по
        # этим показателям не исправлялось никогда.
        for prop in LINEAR_PROP_CONFIG:
            raw_prop = twin_raw_output.get(f"HT_{prop}_PRODUCT", twin_raw_output.get(f"HT_{prop}"))
            if raw_prop is None:
                continue
            val = float(raw_prop)
            if math.isnan(val) or math.isinf(val):
                continue
            hist_entry[prop] = val
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

        ht_p8_raw = tags.get("HT_P8")
        ht_dp_kpa_fallback = float(ht_p8_raw) * 980.665 if ht_p8_raw is not None and float(ht_p8_raw) < 5.0 else (float(ht_p8_raw) if ht_p8_raw is not None else 177.0)
        measured_constraints: Dict[str, float] = {
            "AVT_T55": float(tags.get("AVT_T55", u_actual.get("AVT_T55_SP", 381.7))),
            "HT_DP_KPA": float(tags.get("HT_DP_KPA", ht_dp_kpa_fallback)),
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
