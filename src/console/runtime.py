"""Модуль рантайма сессий пульта оператора (R3).

Управляет модельным временем, историей измерений, фоновым метрономом и связью с PlantSimulator.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Set

from src.agents.contracts import DataAssessment, PlantEstimate
from src.agents.graph import get_graph
from src.console import auto, forecast
from src.console.contracts import (
    AutoExitCode,
    AutoInfo,
    Banner,
    FeedItem,
    LastAutoExit,
    ModeInfo,
    Point,
    Trajectory,
)
from src.console.feed import feed_from_graph_result
from src.twin.plant import PlantSimulator
from src.agents.scenarios import scenario_1_normal_tags
from src.twin.session import TWIN_STORE

T0_DATE = datetime(2026, 9, 19, 4, 40, 0, tzinfo=timezone.utc)

# Справочные датчики двойника (B1, план §4): реально считаются в src/twin/chain.py::step(),
# но не входят в ONLINE_OUTPUT_TAGS PlantSimulator и нигде не выводятся консолью.
# Ключ буфера -> имя поля в PlantSimulator.last_model_outputs (сырой выход twin.step()).
# HT_T11 — единственное исключение: сырой ключ twin.step() называется HT_T_OUT
# (в self.tags он уже копируется как HT_T11 через ONLINE_OUTPUT_TAGS, см. src/twin/plant.py).
SENSOR_SOURCE_TAGS: Dict[str, str] = {
    "HT_BED_MEAN": "HT_BED_MEAN",
    "HT_VSG": "HT_VSG",
    "HT_T11": "HT_T_OUT",
    "HT_D15_PRODUCT": "HT_D15_PRODUCT",
    "HT_T95_PRODUCT": "HT_T95_PRODUCT",
    "HT_CFPP_PRODUCT": "HT_CFPP_PRODUCT",
    "HT_CN_PRODUCT": "HT_CN_PRODUCT",
    "HT_S_FEED": "HT_S_FEED",
    "HT_T95_FEED": "HT_T95_FEED",
}


class ConsoleSession:
    """Состояние одной активной сессии пульта оператора."""

    def __init__(
        self,
        session_id: str,
        plant: Optional[PlantSimulator] = None,
    ) -> None:
        self.session_id: str = session_id
        self.lock = threading.RLock()

        self.plant: PlantSimulator = plant or PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)
        self.graph = get_graph()

        self.tick: int = 0
        self.tick_delta = timedelta(seconds=600)
        self.now: datetime = T0_DATE

        # Кольцевые буферы истории на 73 такта (12 ч)
        self.history_len = 73
        self.cv_history: Dict[str, Deque[Point]] = {
            "sulfur": deque(maxlen=self.history_len),
            "flash": deque(maxlen=self.history_len),
            "dp": deque(maxlen=self.history_len),
        }
        self.mv_sp_history: Dict[str, Deque[Point]] = {
            sp: deque(maxlen=self.history_len)
            for sp in ("HT_FEED_SP", "HT_TIN_SP", "HT_P_SP", "HT_GOR_SP", "AVT_T55_SP")
        }
        self.mv_pv_history: Dict[str, Deque[Point]] = {
            sp: deque(maxlen=self.history_len)
            for sp in ("HT_FEED_SP", "HT_TIN_SP", "HT_P_SP", "HT_GOR_SP", "AVT_T55_SP")
        }
        # Кольцевые буферы справочных датчиков двойника (WABT, ВСГ, T11, качество сырья/продукта) —
        # см. SENSOR_SOURCE_TAGS выше, заполняются в tick_single() из PlantSimulator.last_model_outputs.
        self.sensor_history: Dict[str, Deque[Point]] = {
            key: deque(maxlen=self.history_len) for key in SENSOR_SOURCE_TAGS
        }

        self.u_current: Dict[str, float] = dict(self.plant.twin.u_current)
        self.mode: str = "ADVISORY"
        self.auto_since: Optional[str] = None
        self.last_auto_exit: Optional[LastAutoExit] = None

        self.feed: Deque[FeedItem] = deque(maxlen=200)
        self.last_graph_result: Optional[Dict[str, Any]] = None
        self.last_cycle_id: Optional[str] = None
        self.last_estimate: Optional[PlantEstimate] = None
        self.last_data_assessment: Optional[DataAssessment] = None

        self.banner: Optional[Banner] = None
        self.acks: Set[str] = set()

        self.seconds_per_tick: float = 5.0 * 2
        self.last_tick_time: float = time.time()
        self.skipped_next: bool = False

        self.scenario_id: Optional[str] = None
        self.scenario_title: Optional[str] = None

        # Live-переопределение цен рынка/тарифов оператором (B3, план §6): частичный словарь
        # ключей EconomicsParams, применяется к каждому такту через graph.invoke(..., "economics").
        # Пусто -> используется дефолт из load_params().economics (см. src/console/api.py).
        self.economics_override: Dict[str, float] = {}

        self.hold_trajectory: Optional[Trajectory] = None
        self._traj_cache: Dict[Any, Trajectory] = {}

    @property
    def next_tick_in_s(self) -> Optional[float]:
        """Оставшееся время до следующего тика в секундах."""
        if self.seconds_per_tick <= 0:
            return None
        elapsed = time.time() - self.last_tick_time
        return max(0.0, round(self.seconds_per_tick - elapsed, 1))

    def apply(self, u_target: Dict[str, float]) -> None:
        """
        Применяет уставки в PlantSimulator и коммитит ход в TWIN_STORE.
        ВНИМАНИЕ: Вызывающий код обязан выполнить проверку SafetyKernel и коридора шага ДО вызова apply.
        """
        with self.lock:
            # assert: caller performs SafetyKernel and corridor checks prior to apply()
            delta_u = {
                sp: round(u_target[sp] - self.u_current.get(sp, u_target[sp]), 3)
                for sp in u_target
            }
            self.plant.apply(delta_u)
            TWIN_STORE.commit_applied_move(self.session_id, delta_u)
            self.u_current = dict(self.plant.twin.u_current)
            self._traj_cache.clear()

    def tick_single(self, warmup: bool = False) -> None:
        """Выполняет один такт модельного времени (10 мин)."""
        with self.lock:
            self.tick += 1
            self.now = T0_DATE + self.tick * self.tick_delta
            self.last_tick_time = time.time()
            self._traj_cache.clear()

            # 1. Измерение телеметрии симулятора
            tags = self.plant.measure()
            now_iso = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")

            # 1a. Запись истории справочных датчиков двойника (WABT, ВСГ, T11, качество сырья/продукта):
            # эти теги реально считаются twin.step(), но не входят в ONLINE_OUTPUT_TAGS/self.tags.
            raw_outputs = self.plant.last_model_outputs
            for buf_key, raw_key in SENSOR_SOURCE_TAGS.items():
                raw_val = raw_outputs.get(raw_key)
                sensor_qual = (
                    "MISSING"
                    if raw_val is None or (isinstance(raw_val, float) and math.isnan(raw_val))
                    else "GOOD"
                )
                sensor_pt_val = None if sensor_qual == "MISSING" else round(float(raw_val), 3)
                self.sensor_history[buf_key].append(Point(t=now_iso, v=sensor_pt_val, quality=sensor_qual))

            # 2. Запись истории CV
            s_val = tags.get("HT_Q21")
            s_qual = (
                "MISSING"
                if s_val is None or (isinstance(s_val, float) and math.isnan(s_val))
                else "GOOD"
            )
            s_pt_val = None if s_qual == "MISSING" else round(float(s_val), 3)
            self.cv_history["sulfur"].append(Point(t=now_iso, v=s_pt_val, quality=s_qual))

            f_val = tags.get("HT_FLASH")
            f_qual = (
                "MISSING"
                if f_val is None or (isinstance(f_val, float) and math.isnan(f_val))
                else "GOOD"
            )
            f_pt_val = None if f_qual == "MISSING" else round(float(f_val), 2)
            self.cv_history["flash"].append(Point(t=now_iso, v=f_pt_val, quality=f_qual))

            dp_raw = tags.get("HT_DP_KPA", tags.get("HT_P8", 177.0))
            dp_qual = (
                "MISSING"
                if dp_raw is None or (isinstance(dp_raw, float) and math.isnan(dp_raw))
                else "GOOD"
            )
            dp_pt_val = None if dp_qual == "MISSING" else round(float(dp_raw), 1)
            self.cv_history["dp"].append(Point(t=now_iso, v=dp_pt_val, quality=dp_qual))

            # 3. Запись истории MV (SP и PV)
            mv_tag_map = {
                "HT_FEED_SP": "HT_F9",
                "HT_TIN_SP": "HT_T6",
                "HT_P_SP": "HT_P13",
                "HT_GOR_SP": "HT_GOR",
                "AVT_T55_SP": "AVT_T55",
            }
            for sp_name, pv_tag in mv_tag_map.items():
                sp_v = self.u_current.get(sp_name, 0.0)
                pv_v = float(tags.get(pv_tag, sp_v))
                self.mv_sp_history[sp_name].append(Point(t=now_iso, v=round(sp_v, 3), quality="GOOD"))
                self.mv_pv_history[sp_name].append(Point(t=now_iso, v=round(pv_v, 3), quality="GOOD"))

            # 4. Вызов графа МАС
            graph_res = self.graph.invoke(
                {
                    "tags": tags,
                    "session_id": self.session_id,
                    "economics": self.economics_override or None,
                }
            )
            self.last_graph_result = graph_res
            self.last_cycle_id = graph_res.get("cycle", {}).get("cycle_id")
            self.last_estimate = graph_res.get("estimate")
            self.last_data_assessment = graph_res.get("data")

            # 5. Обновление ленты и исполнение автомата
            if not warmup:
                new_feed = feed_from_graph_result(graph_res, self.now)
                for item in reversed(new_feed):
                    self.feed.appendleft(item)

                # Если включен режим AUTO
                if self.mode == "AUTO":
                    exit_res = auto.check_auto_exit(self)
                    if exit_res is None:
                        auto.auto_step(self)

            # 6. Расчет траектории hold для текущего такта
            self.hold_trajectory = forecast.build_trajectory(self, self.u_current, "hold")


class SessionRegistry:
    """Реестр и диспетчер активных сессий пульта."""

    def __init__(self) -> None:
        self._sessions: Dict[str, ConsoleSession] = {}
        self._lock = threading.RLock()
        self._metronome_thread = threading.Thread(target=self._metronome_loop, daemon=True)
        self._metronome_thread.start()

    def register(self, session: ConsoleSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> ConsoleSession:
        with self._lock:
            if session_id not in self._sessions:
                if session_id == "demo":
                    from src.console.demo import load_scenario
                    return load_scenario(session_id, "S2", warmup_ticks=48)
                session = ConsoleSession(session_id=session_id)
                self._sessions[session_id] = session
                return session
            return self._sessions[session_id]

    def load_scenario(self, session_id: str, scenario: str, warmup_ticks: int = 48) -> ConsoleSession:
        from src.console.demo import load_scenario as demo_load
        return demo_load(session_id, scenario, warmup_ticks=warmup_ticks)

    def tick(self, session_id: str, n: int = 1) -> ConsoleSession:
        session = self.get(session_id)
        for _ in range(n):
            session.tick_single(warmup=False)
        return session

    def set_speed(self, session_id: str, seconds_per_tick: float) -> None:
        session = self.get(session_id)
        with session.lock:
            session.seconds_per_tick = float(seconds_per_tick)

    def set_fault(self, session_id: str, tag: str, fault_type: str, lims_age_hours: Optional[float] = None) -> None:
        session = self.get(session_id)
        with session.lock:
            if fault_type == "clear":
                session.plant.clear_fault(tag)
            else:
                session.plant.set_fault(tag, fault_type)
            if lims_age_hours is not None:
                session.plant.tags["lims_age_hours"] = float(lims_age_hours)

    def _metronome_loop(self) -> None:
        """Фоновый поток метронома: проверяет сессии и выполняет тики."""
        while True:
            try:
                time.sleep(0.2)
                now_ts = time.time()
                with self._lock:
                    active_sessions = list(self._sessions.values())

                for session in active_sessions:
                    if session.seconds_per_tick > 0:
                        elapsed = now_ts - session.last_tick_time
                        if elapsed >= session.seconds_per_tick:
                            session.tick_single(warmup=False)
            except Exception:
                pass


REGISTRY: SessionRegistry = SessionRegistry()
