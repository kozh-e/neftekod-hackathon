"""Хранилище состояний цифрового двойника между циклами управления (TwinSessionStore).

Обеспечивает непрерывность динамического состояния между вызовами API/UI (ADR-8):
1. Шаги двойника по физическим меткам времени (с защитой от разрывов > 2 ч);
2. Пересинхронизация u_current по измеренным MV каждый такт;
3. Обнаружение ручных изменений оператора (вне рекомендаций);
4. Фиксация примененных воздействий через commit_applied_move(session_id, delta_u=...);
5. Детекция залипания приводов и отсутствия отклика установки (RECOVERY_STALLED).
"""

from __future__ import annotations

from collections import deque
import datetime
import math
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

from src.twin.params import TwinParams, load_params
from src.twin.chain import FullChainTwin

# Соответствие управляющих воздействий и датчиков КИПиА
MV_TAGS: Dict[str, str] = {
    "HT_FEED_SP": "HT_F9",
    "HT_TIN_SP": "HT_T6",
    "HT_P_SP": "HT_P13",
    "HT_GOR_SP": "HT_GOR",
    "AVT_T55_SP": "AVT_T55",
}

RESYNC_TOL: Dict[str, float] = {
    "HT_FEED_SP": 1.0,
    "HT_TIN_SP": 0.5,
    "HT_P_SP": 0.02,
    "HT_GOR_SP": 5.0,
    "AVT_T55_SP": 0.5,
}

MAX_GAP_HOURS: float = 2.0


def parse_timestamp(val: Any) -> datetime.datetime:
    """Парсит ISO 8601 строку или возвращает текущее время UTC."""
    if isinstance(val, datetime.datetime):
        return val if val.tzinfo else val.replace(tzinfo=datetime.timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            pass
    return datetime.datetime.now(datetime.timezone.utc)


class TwinSessionStore:
    """
    Сессионное хранилище экземпляров FullChainTwin с физической привязкой ко времени.
    """

    def __init__(self, params: Optional[TwinParams] = None):
        self.params = params or load_params()
        self._sessions: Dict[str, FullChainTwin] = {}
        self._t_last: Dict[str, datetime.datetime] = {}
        self._applied_moves: Dict[str, List[Dict[str, float]]] = {}
        self._proposed_moves: Dict[str, List[Dict[str, float]]] = {}
        self._tag_history: Dict[str, Deque[Dict[str, float]]] = {}
        self._manual_changes: Dict[str, List[str]] = {}
        self._events: Dict[str, List[str]] = {}

    def get(
        self, session_id: Optional[str], tags: Mapping[str, float]
    ) -> Tuple[FullChainTwin, List[str]]:
        """
        Возвращает двойник для заданной сессии.

        - Если session_id is None: создается одноразовый двойник, выдается предупреждение STATELESS_NO_PENDING_MOVES.
        - При первом вызове с session_id: двойник инициализируется по tags.
        - При повторных вызовах: делает шаги по прошедшему физическому времени, сверяет u_current и ассимилирует замеры.
        """
        t_now = parse_timestamp(tags.get("timestamp"))

        if session_id is None:
            twin = FullChainTwin(self.params)
            warnings = twin.initialize(tags)
            warnings.append("STATELESS_NO_PENDING_MOVES")
            return twin, warnings

        if session_id not in self._sessions:
            twin = FullChainTwin(self.params)
            warnings = twin.initialize(tags)
            self._sessions[session_id] = twin
            self._t_last[session_id] = t_now
            self._applied_moves[session_id] = []
            self._proposed_moves[session_id] = []
            self._tag_history[session_id] = deque(maxlen=20)
            self._tag_history[session_id].append(dict(tags))
            self._manual_changes[session_id] = []
            self._events[session_id] = []
            return twin, warnings

        # Сессия уже существует
        twin = self._sessions[session_id]
        t_prev = self._t_last.get(session_id, t_now)
        self._t_last[session_id] = t_now

        dt_sec = max(0.0, (t_now - t_prev).total_seconds())
        # Если время явно передано и разрыв > 2 часов -> переинициализация
        dt_hours = dt_sec / 3600.0
        if dt_hours > MAX_GAP_HOURS:
            warnings = twin.initialize(tags)
            self._applied_moves[session_id].clear()
            self._tag_history[session_id].clear()
            self._tag_history[session_id].append(dict(tags))
            return twin, warnings

        # Шаги по времени (если время не менялось или равно 0, делаем 1 регламентный шаг)
        step_dt_min = twin.dt_min
        n_steps = max(1, round(dt_sec / (step_dt_min * 60.0))) if dt_sec > 0.0 else 1
        n_steps = min(n_steps, 120)

        for _ in range(n_steps):
            twin.step(twin.u_current)

        # Сверка измеренных MV и детекция ручных изменений
        manual_detected: List[str] = []
        u_meas: Dict[str, float] = {}
        for mv, tag_key in MV_TAGS.items():
            if tag_key in tags and isinstance(tags[tag_key], (int, float)) and not math.isnan(tags[tag_key]):
                val = float(tags[tag_key])
                u_meas[mv] = val
                tol = RESYNC_TOL.get(mv, 1.0)
                curr_sp = twin._u_current.get(mv, val)
                if abs(val - curr_sp) > tol:
                    manual_detected.append(mv)

        self._manual_changes[session_id] = manual_detected
        # Ресинхронизация уставок двойника с фактическими уставками
        for mv, val in u_meas.items():
            twin._u_current[mv] = val

        # Ассимиляция свежих замеров
        twin.assimilate(tags)

        # История тегов для контроля отклика оборудования
        history = self._tag_history[session_id]
        history.append(dict(tags))

        # Детекция зависания приводов / отсутствия отклика (RECOVERY_STALLED)
        # Если были применены или рекомендованы команды, но датчик не реагирует 3+ шага
        has_active_moves = bool(self._applied_moves.get(session_id)) or bool(self._proposed_moves.get(session_id))
        if len(history) >= 3 and has_active_moves:
            last_tags = list(history)[-3:]
            for sensor in ("HT_T6", "HT_F9", "AVT_T55"):
                vals = [h.get(sensor) for h in last_tags if sensor in h and isinstance(h.get(sensor), (int, float))]
                if len(vals) >= 3:
                    # Дисперсия показаний датчика
                    mean_val = sum(vals) / len(vals)
                    var = sum((x - mean_val) ** 2 for x in vals) / len(vals)
                    # Если датчик строго зафиксирован (var == 0) при наличии активных управляющих воздействий
                    if var < 1e-8:
                        if "RECOVERY_STALLED" not in self._events[session_id]:
                            self._events[session_id].append("RECOVERY_STALLED")

        return twin, []

    def record_recommended_move(
        self,
        session_id: str,
        delta_u: Optional[Mapping[str, float]] = None,
    ) -> None:
        """Регистрация предложенного арбитражем воздействия для отслеживания отклика оборудования."""
        if not session_id or not delta_u:
            return
        if session_id not in self._proposed_moves:
            self._proposed_moves[session_id] = []
        self._proposed_moves[session_id].append(dict(delta_u))

    def commit_applied_move(
        self,
        session_id: str,
        u_applied: Optional[Mapping[str, float]] = None,
        delta_u: Optional[Mapping[str, float]] = None,
    ) -> None:
        """
        Фиксация применения уставок оператором (кнопка «ОДОБРИТЬ» в HITL).
        Поддерживает как абсолютные значения уставок (u_applied), так и приращения (delta_u).
        """
        move_dict = dict(delta_u or u_applied or {})
        if not move_dict:
            return

        if session_id in self._sessions:
            twin = self._sessions[session_id]
            for k, v in move_dict.items():
                if k not in twin._u_current:
                    continue
                v_float = float(v)
                curr = twin._u_current[k]

                # Если передан delta_u или распознаем по величине
                is_delta = False
                if delta_u is not None:
                    is_delta = True
                elif k in ("HT_TIN_SP", "HT_FEED_SP", "HT_GOR_SP", "AVT_T55_SP") and abs(v_float) < 50.0:
                    is_delta = True
                elif k == "HT_P_SP" and abs(v_float) < 1.0:
                    is_delta = True

                if is_delta:
                    twin._u_current[k] = curr + v_float
                else:
                    twin._u_current[k] = v_float

            if session_id in self._applied_moves:
                self._applied_moves[session_id].append(move_dict)

    def get_events(self, session_id: Optional[str]) -> List[str]:
        """Возвращает накопленные события для сессии."""
        if session_id and session_id in self._events:
            return list(self._events[session_id])
        return []

    def get_manual_changes(self, session_id: Optional[str]) -> List[str]:
        if session_id and session_id in self._manual_changes:
            return list(self._manual_changes[session_id])
        return []

    def reset(self, session_id: str) -> None:
        """Сброс сессии."""
        self._sessions.pop(session_id, None)
        self._t_last.pop(session_id, None)
        self._applied_moves.pop(session_id, None)
        self._tag_history.pop(session_id, None)
        self._manual_changes.pop(session_id, None)
        self._events.pop(session_id, None)


# Модульный синглтон сессионного хранилища
TWIN_STORE = TwinSessionStore()
