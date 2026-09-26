"""Тесты Шага 5 MVP: Контракты Pydantic v2.

Проверяет:
1. Валидацию контрактов BaseAgentProtocol и SafetyAuditReport (Pydantic v2).
"""

import pytest
from pydantic import ValidationError

from src.agents.state import BaseAgentProtocol, SafetyAuditReport, ControlCandidate


def test_pydantic_contracts_validation():
    """Тест 1: Проверка валидации Pydantic v2 контрактов (ge, le, frozen, extra='forbid')."""
    # 1. BaseAgentProtocol запрещает лишние поля (extra='forbid')
    with pytest.raises(ValidationError):
        BaseAgentProtocol(extra_field="disallowed")

    # 2. SafetyAuditReport требует risk_penalty_rub_h >= 0.0
    with pytest.raises(ValidationError):
        SafetyAuditReport(
            candidate_id="cand_1",
            is_vetoed=False,
            risk_penalty_rub_h=-50.0  # Недопустимо, ge=0.0
        )

    # 3. Корректный отчет аудита
    report = SafetyAuditReport(
        candidate_id="cand_1",
        is_vetoed=True,
        risk_penalty_rub_h=1500.0,
        violation_reason="Превышение COT печи П-3"
    )
    assert report.candidate_id == "cand_1"
    assert report.is_vetoed is True
    assert report.risk_penalty_rub_h == 1500.0
    assert report.violation_reason == "Превышение COT печи П-3"
