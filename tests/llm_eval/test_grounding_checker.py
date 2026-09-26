"""Тесты верификатора заземления (GroundingChecker, Задача P4.6 / P4.12).

Проверяет:
1. Корректные ссылки и числа из пакета доказательств -> passed=True;
2. Вымышленные ссылки (hallucinated refs) -> passed=False, фиксация в missing_refs;
3. Числа, не подтвержденные доказательствами с допуском 1% -> passed=False;
4. Числа в пределах допуска 1% -> passed=True.
"""

from __future__ import annotations

import pytest
from src.supervisor.agents import DiagnosticReport, OperatorAnswer
from src.supervisor.evidence import EvidencePackage
from src.supervisor.validation import GroundingChecker


@pytest.fixture
def sample_evidence() -> EvidencePackage:
    pkg = EvidencePackage(
        as_of="2026-09-17T12:00:00",
        trigger="TEST",
        code_version="1.0.0",
        policy_version="1.0.0",
    )
    pkg.add("equipment.avt_t55.measured", 385.0, unit="°C")
    pkg.add("equipment.ht_p8.measured", 412.0, unit="кПа")
    pkg.add("quality.godt_s.value", 8.45, unit="мг/кг")
    pkg.add("quality.godt_s.sigma_calib", 0.82, unit="мг/кг")
    pkg.add("policy.alpha_quality", 0.0228)
    return pkg


def test_grounding_checker_valid(sample_evidence: EvidencePackage):
    """Корректный отчет с подтвержденными адресами и числами проходит проверку."""
    checker = GroundingChecker(rel_tol=0.01)
    rep = DiagnosticReport(
        finding_id="FND-20260917-001",
        category="LIMS_PAK_CONFLICT",
        severity="WARNING",
        title="Тест расхождения серы",
        root_cause="Сера составляет 8.45 мг/кг при температуре 385.0 °C",
        evidence_refs=["quality.godt_s.value", "equipment.avt_t55.measured"],
        checks_recommended=["Проверить анализатор"],
        safety_risk_assessment="В пределах допустимого",
    )
    result = checker.verify(rep, sample_evidence)
    assert result.passed is True
    assert len(result.missing_refs) == 0
    assert len(result.unmatched_numbers) == 0


def test_grounding_checker_missing_ref(sample_evidence: EvidencePackage):
    """Отчет с несуществующим EvidenceRef бракуется."""
    checker = GroundingChecker(rel_tol=0.01)
    rep = DiagnosticReport(
        finding_id="FND-20260917-002",
        category="LIMS_PAK_CONFLICT",
        severity="WARNING",
        title="Тест несуществующей ссылки",
        root_cause="Сера в норме",
        evidence_refs=["quality.godt_s.value", "non_existent_address_ref"],
        checks_recommended=["Проверка"],
        safety_risk_assessment="Норма",
    )
    result = checker.verify(rep, sample_evidence)
    assert result.passed is False
    assert "non_existent_address_ref" in result.missing_refs


def test_grounding_checker_unmatched_number(sample_evidence: EvidencePackage):
    """Отчет с числом, отсутствующим в доказательствах (сверх 1%), бракуется."""
    checker = GroundingChecker(rel_tol=0.01)
    rep = OperatorAnswer(
        question="Почему?",
        direct_answer="Температура печи составляет 999.0 °C",
        technical_explanation="Высокая температура",
        active_constraints_involved=[],
        evidence_refs=["equipment.avt_t55.measured"],
        operator_guidance="Ждать",
    )
    result = checker.verify(rep, sample_evidence)
    assert result.passed is False
    assert 999.0 in result.unmatched_numbers


def test_grounding_checker_number_tolerance(sample_evidence: EvidencePackage):
    """Число в пределах 1% погрешности (например 8.48 при базе 8.45) проходит проверку."""
    checker = GroundingChecker(rel_tol=0.01)
    rep = DiagnosticReport(
        finding_id="FND-20260917-003",
        category="CALIBRATION_DRIFT",
        severity="INFO",
        title="Тест погрешности 1%",
        root_cause="Текущее значение серы около 8.48 мг/кг",
        evidence_refs=["quality.godt_s.value"],
        checks_recommended=["Контроль"],
        safety_risk_assessment="Безопасно",
    )
    result = checker.verify(rep, sample_evidence)
    # 8.48 / 8.45 - 1 = 0.00355 (0.35% < 1%)
    assert result.passed is True
