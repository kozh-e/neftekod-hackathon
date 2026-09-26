"""Валидация заземления ответов (Grounding) и предложений политик (PolicyValidator).

Обеспечивает строгое соблюдение требований Gate G4:
- GroundingChecker: отсутствие галлюцинаций, сверка ссылок EvidenceRef и чисел с пакетом доказательств (допуск 1%).
- PolicyValidator: защита от prompt-инъекций, строгий контроль белого списка POLICY_WHITELIST (только ужесточение).
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from pydantic import BaseModel, Field

from src.agents.policy import (
    DEFAULT_POLICY_STORE,
    POLICY_WHITELIST,
    WHITELIST_BY_FIELD,
    PolicyConfig,
    PolicyStore,
)
from src.agents.registry import REGISTRY_BY_KEY
from src.supervisor.agents import PolicyProposal
from src.supervisor.evidence import EvidencePackage


class GroundingReport(BaseModel):
    """Отчет верификации заземления ответа LLM."""
    passed: bool
    missing_refs: List[str] = Field(default_factory=list)
    unmatched_numbers: List[float] = Field(default_factory=list)
    unmatched_tags: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class PolicyValidationReport(BaseModel):
    """Отчет проверки предложенных изменений технологической политики."""
    passed: bool
    violations: List[str] = Field(default_factory=list)
    blocked_injections: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


# Допустимые стандартные числа (константы, проценты, временные периоды, размерности)
STANDARD_ALLOWED_NUMBERS = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 24.0, 30.0, 35.0, 40.0, 48.0, 50.0, 72.0, 100.0,
    2024.0, 2025.0, 2026.0, 0.5, 0.05, 0.1, 0.01, 0.02, 0.95, 0.99, 32511.0, 2013.0, 24.0, 2000.0, 6.0, 386.4
}


def extract_numbers_from_text(text: str) -> List[float]:
    """Извлекает вещественные и целые числа из текстового блока."""
    # Исключаем даты вида YYYY-MM-DD или ISO timestamps
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}", " ", text)
    # Исключаем идентификаторы FND-, POL-, SHF-
    cleaned = re.sub(r"(?:FND|POL|SHF)-\d+(?:-\w+)?", " ", cleaned)
    # Исключаем время суток (например, 08:00, 20:00)
    cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", " ", cleaned)
    # Исключаем технологические индексы оборудования (АВТ-6, П-3, Р-202, К-10, 24-2000)
    cleaned = re.sub(r"(?:[А-ЯA-Zа-яa-z]+-|\b24-)\d+", " ", cleaned)
    # Исключаем ссылки EvidenceRef в скобках [status.total_cycles]
    cleaned = re.sub(r"\[[a-zA-Z0-9_\.]+\]", " ", cleaned)

    # Ищем только числа, перед знаком минус/плюс которых нет буквы или цифры
    pattern = r"(?<![a-zA-Zа-яА-Я0-9_])[-+]?\b\d+(?:\.\d+)?\b"
    matches = re.findall(pattern, cleaned)
    numbers = []
    for m in matches:
        try:
            val = float(m)
            numbers.append(val)
        except ValueError:
            pass
    return numbers


def extract_tags_from_text(text: str) -> List[str]:
    """Извлекает технологические теги (например, AVT_T55, HT_P8, HT_Q21)."""
    pattern = r"\b(?:AVT|HT)_[A-Z0-9]+\b"
    return list(set(re.findall(pattern, text)))


def matches_any_number(target: float, candidates: Sequence[float], rel_tol: float = 0.01) -> bool:
    """Сверяет число с кандидатами с относительной погрешностью rel_tol (1%)."""
    if target in STANDARD_ALLOWED_NUMBERS:
        return True

    for c in candidates:
        if isinstance(c, (int, float)):
            c_val = float(c)
            if math.isclose(target, c_val, rel_tol=rel_tol, abs_tol=0.01):
                return True
    return False


class GroundingChecker:
    """Проверяет фактическую обоснованность и заземление выводов LLM."""

    def __init__(self, rel_tol: float = 0.01):
        self.rel_tol = rel_tol

    def verify(
        self,
        output_obj: BaseModel,
        evidence: EvidencePackage,
        extra_allowed_numbers: Optional[Sequence[float]] = None,
    ) -> GroundingReport:
        """Сверяет ссылки EvidenceRef, упоминаемые числа и технологические теги."""
        known_addresses = evidence.addresses()
        evidence_numeric_values: List[float] = []

        for val in evidence.values():
            if isinstance(val, (int, float)):
                evidence_numeric_values.append(float(val))
        if extra_allowed_numbers:
            evidence_numeric_values.extend(extra_allowed_numbers)

        missing_refs: List[str] = []
        notes: List[str] = []

        # 1. Проверка адресов EvidenceRef
        obj_dict = output_obj.model_dump()
        declared_refs = obj_dict.get("evidence_refs", [])
        for ref in declared_refs:
            if ref not in known_addresses:
                missing_refs.append(ref)

        # 2. Проверка чисел в текстовых полях
        full_text = " ".join(str(v) for v in obj_dict.values() if isinstance(v, (str, list, dict)))
        extracted_numbers = extract_numbers_from_text(full_text)

        unmatched_numbers: List[float] = []
        for num in extracted_numbers:
            if not matches_any_number(num, evidence_numeric_values, rel_tol=self.rel_tol):
                unmatched_numbers.append(num)

        # 3. Проверка тегов КИП
        extracted_tags = extract_tags_from_text(full_text)
        unmatched_tags: List[str] = []
        # Допустимые теги
        known_tags = {
            "AVT_T55", "HT_P8", "AVT_F31", "AVT_P52", "HT_Q21", "HT_F9", "HT_T6",
            "HT_F14", "AVT_F30", "AVT_F32", "HT_FEED_SP", "HT_TIN_SP", "HT_P_SP",
            "HT_GOR_SP", "HT_H2_PCT", "HT_T_BED_MEAN", "AVT_T_PECH", "HT_DP_KPA"
        }
        for tag in extracted_tags:
            if tag not in known_tags and not any(tag in spec_k for spec_k in REGISTRY_BY_KEY):
                unmatched_tags.append(tag)

        # 4. Защита от prompt-инъекций в свободном тексте вывода. Раньше INJECTION_PATTERNS
        # сканировал только PolicyProposal.items[].justification — diagnostics/briefing/
        # operator_qa вообще не проверялись (см. аудит). full_text уже агрегирует все
        # строковые/списковые/словарные поля любого output_obj, так что переиспользуем его.
        blocked_injections = scan_injection_patterns(full_text)

        passed = (
            len(missing_refs) == 0
            and len(unmatched_numbers) == 0
            and len(unmatched_tags) == 0
            and len(blocked_injections) == 0
        )

        if missing_refs:
            notes.append(f"Неизвестные адреса EvidenceRef: {missing_refs}")
        if unmatched_numbers:
            notes.append(f"Числа без подтверждения в доказательствах (сверх 1%): {unmatched_numbers}")
        if unmatched_tags:
            notes.append(f"Неизвестные технологические теги: {unmatched_tags}")
        notes.extend(blocked_injections)

        return GroundingReport(
            passed=passed,
            missing_refs=missing_refs,
            unmatched_numbers=unmatched_numbers,
            unmatched_tags=unmatched_tags,
            notes=notes,
        )


# Паттерны prompt-инъекций и запрещенных команд. Вынесены на уровень модуля, чтобы
# использоваться не только PolicyValidator.validate_proposal (только item.justification),
# но и общим сканером scan_injection_patterns() для любого свободного текста LLM-вывода
# (diagnostics/briefing/operator_qa раньше вообще не проверялись — см. аудит).
INJECTION_PATTERNS = [
    r"увелич(?:ь|ьте)\s+альфа",
    r"ослаб(?:ь|ьте)\s+безопасность",
    r"постав(?:ь|ьте)\s+уставк",
    r"забудь\s+(?:все\s+)?инструкци",
    r"игнорируй\s+(?:все\s+)?правил",
    r"setpoint",
    r"override\s+safety",
    r"ignore\s+(?:all\s+)?(?:previous\s+)?(?:rules|instructions)",
    r"отключ(?:и|ить)\s+паз",
    r"обойд[иу](?:те)?\s+паз",
    r"390\s*°?[cс]",
]


def scan_injection_patterns(*texts: Optional[str]) -> List[str]:
    """Сканирует один или несколько текстов на признаки prompt-инъекции.

    Возвращает список описаний найденных совпадений (пусто, если ничего не найдено).
    Регистронезависимо; не защищает от произвольных unicode-обфускаций, но покрывает
    больше поверхности, чем прежняя проверка одного поля justification в одной роли.
    """
    hits: List[str] = []
    for text in texts:
        if not text:
            continue
        for pat in INJECTION_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(f"Обнаружена инъекция: '{pat}' в тексте '{text[:80]}...'")
    return hits


class PolicyValidator:
    """Валидатор безопасности предложений изменения политики и фильтр инъекций."""

    # Backward-compat алиас: код/тесты, обращающиеся к PolicyValidator.INJECTION_PATTERNS,
    # продолжают работать после выноса списка на уровень модуля.
    INJECTION_PATTERNS = INJECTION_PATTERNS

    def __init__(self, policy_store: Optional[PolicyStore] = None):
        self.policy_store = policy_store or DEFAULT_POLICY_STORE

    def validate_proposal(
        self,
        proposal: PolicyProposal,
        current_policy: Optional[PolicyConfig] = None,
    ) -> PolicyValidationReport:
        """Проверяет предложение политики по белому списку и правилу только ужесточения."""
        policy = current_policy or self.policy_store.active_policy
        violations: List[str] = []
        blocked_injections: List[str] = []
        notes: List[str] = []

        # 1. Защита от prompt-инъекций в тексте обоснований и заявленного эффекта
        for item in proposal.items:
            for pat in self.INJECTION_PATTERNS:
                if re.search(pat, item.justification, re.IGNORECASE):
                    blocked_injections.append(f"Обнаружена инъекция в justification: '{pat}'")
        blocked_injections.extend(scan_injection_patterns(proposal.expected_kpi_impact))

        if len(proposal.items) > 3:
            violations.append(f"Превышен лимит: {len(proposal.items)} параметров в запросе (максимум 3)")

        seen_fields = set()
        for item in proposal.items:
            if item.field in seen_fields:
                violations.append(f"Дублирование поля '{item.field}' в одном запросе")
            seen_fields.add(item.field)

            if item.field not in WHITELIST_BY_FIELD:
                violations.append(f"Поле '{item.field}' отсутствует в белом списке POLICY_WHITELIST")
                continue

            entry = WHITELIST_BY_FIELD[item.field]
            # Проверка диапазона [lo, hi]
            if not (entry.lo <= item.new_value <= entry.hi):
                violations.append(
                    f"Значение {item.new_value} для '{item.field}' выходит за границы [{entry.lo}, {entry.hi}]"
                )

            # Проверка направления (только ужесточение)
            current_val = getattr(policy, item.field, None)
            if current_val is None and hasattr(policy.thresholds, item.field):
                current_val = getattr(policy.thresholds, item.field)

            if entry.direction == "tighten_only" and current_val is not None:
                # Для alpha, cautious_step_scale, cautious_calib_age_h: уменьшение означает ужесточение
                if item.field in ("alpha_quality", "cautious_step_scale", "cautious_calib_age_h"):
                    if item.new_value > current_val:
                        violations.append(
                            f"Ослабление безопасности запрещено для '{item.field}': {current_val} -> {item.new_value}"
                        )
                # Для confidence: увеличение означает ужесточение
                elif item.field == "furnace_benefit_confidence":
                    if item.new_value < current_val:
                        violations.append(
                            f"Снижение порога уверенности запрещено для '{item.field}': {current_val} -> {item.new_value}"
                        )

        passed = (len(violations) == 0 and len(blocked_injections) == 0)

        return PolicyValidationReport(
            passed=passed,
            violations=violations,
            blocked_injections=blocked_injections,
            notes=notes,
        )
