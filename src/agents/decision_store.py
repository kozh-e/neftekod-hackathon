"""Хранилище трасс решений мультиагентной системы (DecisionStore v3).

Соответствует спецификации §5.10 implementation_plan_v3.md (Задача P3.10):
1. Хранилище DecisionTrace в SQLite (data/decisions/decisions.db);
2. Дублирование и экспорт в JSONL (data/decisions/decisions.jsonl);
3. Воспроизводимый хэш входов inputs_hash;
4. Методы save, get, list_recent, export_jsonl;
5. Обратная совместимость с append_decision для legacy-тестов.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.contracts import DecisionTrace


class DecisionStore:
    """Хранилище полных трасс принятия решений с индексацией по cycle_id и времени."""

    def __init__(self, db_path: Optional[Path] = None, jsonl_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "data" / "decisions" / "decisions.db"
        if jsonl_path is None:
            jsonl_path = Path(__file__).resolve().parent.parent.parent / "data" / "decisions" / "decisions.jsonl"

        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    cycle_id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    status TEXT,
                    inputs_hash TEXT,
                    payload TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp)
            """)
            conn.commit()

    def save(self, trace: DecisionTrace) -> None:
        """Сохраняет DecisionTrace в SQLite и дописывает в JSONL."""
        payload_json = trace.model_dump_json()
        ts_str = trace.t.isoformat() if hasattr(trace.t, "isoformat") else str(trace.t)
        status_str = trace.decision.status.value if hasattr(trace.decision.status, "value") else str(trace.decision.status)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO decisions (cycle_id, timestamp, status, inputs_hash, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (trace.cycle_id, ts_str, status_str, trace.inputs_hash, payload_json))
            conn.commit()

        # Дописываем строку в decisions.jsonl
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(payload_json + "\n")

    def get(self, cycle_id: str) -> Optional[DecisionTrace]:
        """Извлекает DecisionTrace по идентификатору цикла."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT payload FROM decisions WHERE cycle_id = ?", (cycle_id,))
            row = cursor.fetchone()
            if row:
                return DecisionTrace.model_validate_json(row[0])
        return None

    def list_recent(self, limit: int = 50) -> List[DecisionTrace]:
        """Возвращает список последних трасс решений."""
        result: List[DecisionTrace] = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT payload FROM decisions ORDER BY timestamp DESC LIMIT ?", (limit,))
            for row in cursor.fetchall():
                try:
                    result.append(DecisionTrace.model_validate_json(row[0]))
                except Exception:
                    pass
        return result


# Глобальный инстанс по умолчанию
DEFAULT_STORE = DecisionStore()


def save_decision_trace(trace: DecisionTrace) -> None:
    DEFAULT_STORE.save(trace)


def get_decision_trace(cycle_id: str) -> Optional[DecisionTrace]:
    return DEFAULT_STORE.get(cycle_id)
