"""Хранилище артефактов супервизора (SupervisorStore).

Хранит технические находки (Finding), сводки смены (ShiftBriefing)
и запросы на изменение технологической политики (PolicyChangeRequest) с поддержкой полного жизненного цикла.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.agents.policy import DEFAULT_POLICY_STORE, PolicyConfig, PolicyStore
from src.supervisor.agents import DiagnosticReport, PolicyProposal, ShiftBriefing


class Finding(BaseModel):
    """Техническая находка или диагностический инцидент."""
    finding_id: str
    category: str
    severity: Literal["CRITICAL", "WARNING", "INFO"]
    title: str
    root_cause: str
    evidence_refs: List[str] = Field(default_factory=list)
    checks_recommended: List[str] = Field(default_factory=list)
    safety_risk_assessment: str
    status: Literal["OPEN", "ACKNOWLEDGED", "CLOSED"] = "OPEN"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None

    @classmethod
    def from_diagnostic_report(cls, rep: DiagnosticReport) -> Finding:
        return cls(
            finding_id=rep.finding_id,
            category=rep.category,
            severity=rep.severity,
            title=rep.title,
            root_cause=rep.root_cause,
            evidence_refs=rep.evidence_refs,
            checks_recommended=rep.checks_recommended,
            safety_risk_assessment=rep.safety_risk_assessment,
        )


class PolicyChangeRequest(BaseModel):
    """Запрос на изменение параметров технологической политики."""
    request_id: str
    proposal: PolicyProposal
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED"] = "PENDING_APPROVAL"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    shadow_passed: bool = False
    validation_notes: List[str] = Field(default_factory=list)


class ShiftBriefingRecord(BaseModel):
    """Запись сводки технологической смены."""
    briefing_id: str
    briefing: ShiftBriefing
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class SupervisorStore:
    """SQLite + JSONL хранилище состояния супервизора."""

    def __init__(self, db_path: Optional[Path] = None, jsonl_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "data" / "supervisor" / "supervisor.db"
        if jsonl_path is None:
            jsonl_path = Path(__file__).resolve().parent.parent.parent / "data" / "supervisor" / "supervisor.jsonl"

        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY,
                    category TEXT,
                    severity TEXT,
                    status TEXT,
                    payload TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS change_requests (
                    request_id TEXT PRIMARY KEY,
                    status TEXT,
                    payload TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS briefings (
                    briefing_id TEXT PRIMARY KEY,
                    payload TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()

    def _append_jsonl(self, entity_type: str, data: Dict[str, Any]) -> None:
        record = {
            "entity_type": entity_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_finding(self, finding: Finding) -> None:
        payload = finding.model_dump_json()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO findings (finding_id, category, severity, status, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (finding.finding_id, finding.category, finding.severity, finding.status, payload, finding.created_at))
            conn.commit()
        self._append_jsonl("finding", finding.model_dump())

    def list_findings(self, status: Optional[str] = None) -> List[Finding]:
        results: List[Finding] = []
        query = "SELECT payload FROM findings"
        params = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"

        with sqlite3.connect(self.db_path) as conn:
            for row in conn.execute(query, params).fetchall():
                try:
                    results.append(Finding.model_validate_json(row[0]))
                except Exception:
                    pass
        return results

    def update_finding_status(self, finding_id: str, status: str, user: Optional[str] = None) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT payload FROM findings WHERE finding_id = ?", (finding_id,))
            row = cursor.fetchone()
            if not row:
                return False
            finding = Finding.model_validate_json(row[0])
            finding.status = status # type: ignore
            if status == "CLOSED":
                finding.closed_at = datetime.now().isoformat()
                finding.closed_by = user
            conn.execute(
                "UPDATE findings SET status = ?, payload = ? WHERE finding_id = ?",
                (status, finding.model_dump_json(), finding_id),
            )
            conn.commit()
            return True

    def save_change_request(self, req: PolicyChangeRequest) -> None:
        payload = req.model_dump_json()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO change_requests (request_id, status, payload, created_at)
                VALUES (?, ?, ?, ?)
            """, (req.request_id, req.status, payload, req.created_at))
            conn.commit()
        self._append_jsonl("change_request", req.model_dump())

    def get_change_request(self, request_id: str) -> Optional[PolicyChangeRequest]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM change_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row:
                return PolicyChangeRequest.model_validate_json(row[0])
        return None

    def list_change_requests(self, status: Optional[str] = None) -> List[PolicyChangeRequest]:
        results: List[PolicyChangeRequest] = []
        query = "SELECT payload FROM change_requests"
        params = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"

        with sqlite3.connect(self.db_path) as conn:
            for row in conn.execute(query, params).fetchall():
                try:
                    results.append(PolicyChangeRequest.model_validate_json(row[0]))
                except Exception:
                    pass
        return results

    def approve_change_request(self, request_id: str, approved_by: str) -> bool:
        """Утверждает запрос на изменение и активирует новую политику в PolicyStore."""
        req = self.get_change_request(request_id)
        if not req or req.status != "PENDING_APPROVAL":
            return False

        # Единый синглтон DEFAULT_POLICY_STORE — тот же объект, что main.py отдаёт как
        # POLICY_STORE живому контуру /api/v1/optimize. Раньше здесь создавался одноразовый
        # PolicyStore(), поэтому утверждение никогда не долетало до реального контура решений.
        policy_store = DEFAULT_POLICY_STORE
        active = policy_store.active_policy
        p_dict = active.model_dump()
        th_dict = p_dict.get("thresholds", {})

        for item in req.proposal.items:
            if item.field in p_dict:
                p_dict[item.field] = item.new_value
            elif item.field in th_dict:
                th_dict[item.field] = item.new_value

        p_dict["thresholds"] = th_dict
        p_dict["version"] = f"{active.version}+patch_{request_id}"
        new_policy = PolicyConfig(**p_dict)
        policy_store.set_active_policy(new_policy)
        # Персист на диск, чтобы утверждённая политика пережила перезапуск процесса
        # (PolicyStore._init_default_policy читает ровно этот файл при старте).
        policy_store.save_to_file(new_policy, policy_store.base_dir / "policy_v1.json")

        # Обновляем статус запроса
        req.status = "APPROVED"
        req.decided_by = approved_by
        req.decided_at = datetime.now().isoformat()
        self.save_change_request(req)
        return True

    def reject_change_request(self, request_id: str, rejected_by: str) -> bool:
        """Отклоняет запрос на изменение политики."""
        req = self.get_change_request(request_id)
        if not req or req.status != "PENDING_APPROVAL":
            return False
        req.status = "REJECTED"
        req.decided_by = rejected_by
        req.decided_at = datetime.now().isoformat()
        self.save_change_request(req)
        return True

    def save_briefing(self, briefing: ShiftBriefing) -> ShiftBriefingRecord:
        rec = ShiftBriefingRecord(briefing_id=briefing.briefing_id, briefing=briefing)
        payload = rec.model_dump_json()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO briefings (briefing_id, payload, created_at)
                VALUES (?, ?, ?)
            """, (rec.briefing_id, payload, rec.created_at))
            conn.commit()
        self._append_jsonl("briefing", rec.model_dump())
        return rec

    def list_briefings(self, limit: int = 10) -> List[ShiftBriefingRecord]:
        results: List[ShiftBriefingRecord] = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT payload FROM briefings ORDER BY created_at DESC LIMIT ?", (limit,))
            for row in cursor.fetchall():
                try:
                    results.append(ShiftBriefingRecord.model_validate_json(row[0]))
                except Exception:
                    pass
        return results


DEFAULT_SUPERVISOR_STORE = SupervisorStore()
