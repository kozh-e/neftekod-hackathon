"""Хранилище состояний цифрового двойника между циклами управления (TwinSessionStore).

Обеспечивает непрерывность динамического состояния между вызовами API/UI (ADR-8):
- Сохранение внутренних состояний буферов задержек и фильтров FOPDT;
- Коррекция расхождений измерений (bias) в каждом цикле;
- Фиксация утвержденных оператором управляющих воздействий (commit_applied_move).
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

from src.twin.params import TwinParams, load_params
from src.twin.chain import FullChainTwin


class TwinSessionStore:
    """
    Сессионное хранилище экземпляров FullChainTwin.
    """

    def __init__(self, params: Optional[TwinParams] = None):
        self.params = params or load_params()
        self._sessions: Dict[str, FullChainTwin] = {}

    def get(
        self, session_id: Optional[str], tags: Mapping[str, float]
    ) -> Tuple[FullChainTwin, List[str]]:
        """
        Возвращает двойник для заданной сессии.

        - Если session_id is None: создается одноразовый двойник, выдается предупреждение STATELESS_NO_PENDING_MOVES.
        - При первом вызове с session_id: двойник инициализируется по tags.
        - При повторных вызовах: двойник делает шаг forward с текущими уставками u_current и обновляет bias.
        """
        if session_id is None:
            twin = FullChainTwin(self.params)
            warnings = twin.initialize(tags)
            warnings.append("STATELESS_NO_PENDING_MOVES")
            return twin, warnings

        if session_id not in self._sessions:
            twin = FullChainTwin(self.params)
            warnings = twin.initialize(tags)
            self._sessions[session_id] = twin
            return twin, warnings

        # Сессия уже существует: шаг по времени и усвоение свежих замеров
        twin = self._sessions[session_id]
        twin.step(twin.u_current)
        twin.assimilate(tags)
        return twin, []

    def commit_applied_move(self, session_id: str, u_applied: Mapping[str, float]) -> None:
        """
        Фиксация применения уставок оператором (кнопка «ОДОБРИТЬ» в HITL).
        Поддерживает как абсолютные значения уставок (u_abs), так и приращения (delta_u).
        """
        if session_id in self._sessions:
            twin = self._sessions[session_id]
            for k, v in u_applied.items():
                if k not in twin._u_current:
                    continue
                v_float = float(v)
                curr = twin._u_current[k]
                # Распознаем, передано ли приращение delta_u или абсолютная уставка
                is_delta = False
                if k in ("HT_TIN_SP", "HT_FEED_SP", "HT_GOR_SP") and abs(v_float) < 50.0:
                    is_delta = True
                elif k == "HT_P_SP" and abs(v_float) < 1.0:
                    is_delta = True

                if is_delta:
                    twin._u_current[k] = curr + v_float
                else:
                    twin._u_current[k] = v_float

    def reset(self, session_id: str) -> None:
        """Сброс сессии."""
        self._sessions.pop(session_id, None)


# Модульный синглтон сессионного хранилища
TWIN_STORE = TwinSessionStore()
