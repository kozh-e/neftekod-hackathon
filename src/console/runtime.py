"""Модуль рантайма сессий пульта оператора (R3).

Управляет модельным временем, историей измерений, фоновым метрономом и связью с PlantSimulator.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
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

logger = logging.getLogger(__name__)

T0_DATE = datetime(2026, 9, 19, 4, 40, 0, tzinfo=timezone.utc)

# Отдельный от общего FastAPI/anyio threadpool (run_in_threadpool, по умолчанию до 40
# воркеров на всё приложение) пул специально для тактов симуляции (session.tick_single()).
# Такт сериализуется через session._tick_compute_lock и может реально ждать/считать
# graph.invoke() от долей секунды до нескольких секунд — а под нагрузкой (несколько
# тактов подряд от метронома + ручные вызовы, напр. api.demo.tick(1) после каждого
# commit(), см. static/console/app.js::executeCommit) эти вызовы ставятся в очередь друг
# за другом и КАЖДЫЙ держит выделенный ему поток общего пула занятым всё время ожидания.
# Если гонять такты через ОБЩИЙ пул (как раньше), очередь из тактов начинает выедать
# воркеры, нужные СОВЕРШЕННО не связанным с тактами быстрым запросам вроде
# /api/console/preview и /api/console/state — они просто не получают свободный поток,
# хотя сами не используют вообще никаких блокировок и в изоляции считаются <100мс.
# Эмпирически: 60 параллельных /demo/tick без этого пула откладывали параллельный
# /preview на 47с (см. аудит от 2026-09-22). Отдельный пул держит очередь тактов у себя
# и не даёт им голодом заморить остальной API.
TICK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tick-worker")

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
        # Сериализует только вызовы graph.invoke() этой сессии (метроном vs. ручной такт) —
        # см. tick_single(). Не путать с self.lock: apply()/preview()/build_state() эту
        # блокировку не запрашивают и не ждут.
        self._tick_compute_lock = threading.Lock()

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

        # Последний снимок телеметрии из measure() (см. tick_single()) — единственное место,
        # где measure() должно вызываться: это МУТИРУЮЩИЙ метод (двигает twin.step(), степ-каунтер,
        # график ЛИМС, шум/отказы датчиков), а не идемпотентное чтение. Код, которому нужно
        # ПРОЧИТАТЬ текущие теги (тревоги T1 в service.py, автовыход в auto.py), должен читать
        # last_measurement, а не звать session.plant.measure() повторно — повторный вызов не
        # только лишний раз двигает физическую модель мимо счётчика тактов на каждый опрос
        # /api/console/state (2с), но и делает это БЕЗ session.lock, гоняясь без синхронизации с
        # apply()/tick_single() из других потоков (uvicorn threadpool).
        self.last_measurement: Dict[str, float] = dict(self.plant.tags)

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
            self._traj_cache.clear()

            # 1. Измерение телеметрии симулятора — единственный вызов measure() за такт (см.
            # last_measurement выше); результат сохраняем для read-only обращений между тактами.
            tags = self.plant.measure()
            self.last_measurement = tags
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

            # HT_FLASH не входит в ONLINE_OUTPUT_TAGS (в self.tags онлайн-прокси пишется под
            # именем HT_T18 — см. src/twin/plant.py::ONLINE_OUTPUT_TAGS), а расписание ЛИМС
            # выключено по умолчанию (enable_lims_schedule=False во всех демо-сценариях). Из-за
            # этого tags.get("HT_FLASH") был всегда None, и график «ВСПЫШКА» показывал «нет
            # сигнала» даже в нормальном режиме. Настоящее значение вспышки двойник считает на
            # каждом такте (last_model_outputs) — используем его как источник, если инструментальный
            # тег не выставлен явно (в т.ч. чтобы инъекция отказа через demo.fault("HT_FLASH", ...)
            # по-прежнему могла явно затемнить график).
            f_val = tags.get("HT_FLASH", raw_outputs.get("HT_FLASH"))
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

            session_id = self.session_id
            economics_snapshot = self.economics_override or None

            # При прогреве (warmup=True) полный граф МАС не нужен: прогреву нужна только
            # история физики выше (cv_history/mv_*_history/sensor_history), а рекомендацию
            # агента при прогреве никто не читает — self.last_graph_result и hold_trajectory
            # останутся от предыдущего такта (или None до первого живого такта). Раньше граф
            # звался НА КАЖДОМ из warmup_ticks (обычно 48), из-за чего загрузка/смена сценария
            # занимала ~48×1.3с = минуту и больше — см. запрос пользователя «загрузка < 10с».
            # demo.py::load_scenario() всегда делает один tick_single(warmup=False) сразу после
            # прогрева, так что настоящая рекомендация готова до того, как оператор увидит экран.
            if warmup:
                # Метка last_tick_time нужна ЗДЕСЬ, а не в начале функции (см. правку ниже про
                # graph.invoke()): при прогреве graph.invoke() не вызывается вообще, так что этот
                # такт уже полностью завершён к этому месту.
                self.last_tick_time = time.time()
                return

        # 4. Вызов графа МАС — ЧИСТАЯ функция снимка tags/session_id/economics, ничего в self не
        # трогает. Раньше это было внутри "with self.lock:" вместе со всем остальным, из-за чего
        # apply()/commit() из другого запроса ждали session.lock на всю длительность графа
        # (~1.3с и больше на сложных циклах), а не только на реальное изменение состояния сессии.
        # _tick_compute_lock — ОТДЕЛЬНАЯ блокировка (не session.lock): она не мешает
        # apply()/preview()/build_state() из других запросов, а только не даёт двум тикам этой
        # сессии (метроном + ручной "Следующий такт") посчитать граф параллельно и записать
        # результат в неверном порядке.
        with self._tick_compute_lock:
            graph_res = self.graph.invoke(
                {
                    "tags": tags,
                    "session_id": session_id,
                    "economics": economics_snapshot,
                }
            )

        with self.lock:
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

            # last_tick_time фиксируем в КОНЦЕ такта (а не в начале, как было раньше), уже после
            # graph.invoke(). Раньше метка ставилась до вызова графа МАС: если граф на каком-то
            # такте считался дольше session.seconds_per_tick (эмпирически бывает — см. комментарий
            # у TICK_EXECUTOR выше), метроном (_metronome_loop, опрос раз в 0.2с) видел "elapsed >=
            # seconds_per_tick" СРАЗУ по завершении такого такта и тут же, без всякой паузы, запускал
            # следующий tick_single() — такты шли сплошной очередью, метроном практически не отпускал
            # _tick_compute_lock, а обычный threading.Lock не гарантирует очерёдность (fairness), так
            # что ручные такты (в т.ч. api.demo.tick(1) после commit(), см. app.js::executeCommit)
            # могли голодать за эту блокировку сколь угодно долго. Метка в конце такта гарантирует
            # реальную паузу не менее seconds_per_tick между тактами метронома вне зависимости от
            # того, сколько времени фактически занял граф — и даёт ручным тактам честный шанс.
            self.last_tick_time = time.time()


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

    def order_lims_sample(self, session_id: str, delay_hours: Optional[float] = None) -> None:
        """Внеочередной отбор пробы ЛИМС «Заказать анализ» — обычная задержка лаборатории применяется,
        если delay_hours не передан явно."""
        session = self.get(session_id)
        with session.lock:
            session.plant.schedule_lims_sample(delay_hours=delay_hours)

    def enter_lims_manual(self, session_id: str, prop: str, value: float) -> None:
        """Ручной ввод результата анализа ЛИМС оператором, минуя расписание/задержку."""
        session = self.get(session_id)
        with session.lock:
            session.plant.enter_lims_manual(prop, value)

    def _metronome_loop(self) -> None:
        """Фоновый поток метронома: проверяет сессии и выполняет тики.

        ВАЖНО (аудит 2026-09-22, реальная причина «Пересчитать прогноз висит вечность»):
        раньше ЕДИНСТВЕННЫЙ try/except оборачивал весь проход по active_sessions. Нашли
        реальный баг выше по стеку (FURNACE.ENSEMBLE VIOLATED без slack в node_card, см.
        src/agents/quality.py и src/xai/card.py) — но дело не только в НЁМ: КАЖДОЕ
        исключение внутри session.tick_single() (a) прерывало обработку остальных сессий
        в этом же проходе (вложенный for ломался целиком через внешний try/except) и (б)
        оставляло session.last_tick_time непродвинутым, потому что эта метка
        проставляется в САМОМ КОНЦЕ tick_single() — уже после graph.invoke(), т.е. после
        точки падения. На следующем проходе метронома (через 0.2с) elapsed снова >=
        seconds_per_tick, такт падал заново — и так бесконечно, без единой паузы. Каждая
        попытка реально исполняла ~1-1.5с CPU-работы (data_guard/estimate/negotiation)
        перед падением, так что фоновый поток был занят НЕПРЕРЫВНО, а не раз в
        seconds_per_tick — отсюда перманентный голод по GIL для вообще любого
        конкурентного запроса (в т.ч. /api/console/preview), который выглядел как
        «зависло навсегда», а не как обычная деградация задержки.
        Теперь: падение одной сессии (а) не мешает тактам остальных сессий в этом же
        проходе и (б) не приводит к горячему повтору — last_tick_time продвигается и
        при ошибке, так что следующая попытка этой сессии произойдёт не раньше обычного
        интервала seconds_per_tick; ошибка при этом не проглатывается молча, а идёт в
        лог, чтобы подобный баг не приходилось находить настройкой браузера под нагрузкой.
        """
        while True:
            time.sleep(0.2)
            now_ts = time.time()
            try:
                with self._lock:
                    active_sessions = list(self._sessions.values())
            except Exception:
                logger.exception("Метроном: не удалось снять снимок списка активных сессий")
                continue

            for session in active_sessions:
                if session.seconds_per_tick <= 0:
                    continue
                elapsed = now_ts - session.last_tick_time
                if elapsed < session.seconds_per_tick:
                    continue
                try:
                    session.tick_single(warmup=False)
                except Exception:
                    with session.lock:
                        session.last_tick_time = now_ts
                    logger.exception(
                        "Метроном: такт упал для сессии %s — отложено до следующего обычного интервала (%.1fс)",
                        session.session_id,
                        session.seconds_per_tick,
                    )


REGISTRY: SessionRegistry = SessionRegistry()
