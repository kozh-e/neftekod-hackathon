"""Архитектурные тесты изоляции слоев (P0.3 / ADR-22 / ADR-24).

Проверяет запрещенные зависимости по AST:
1. Модули в src/agents/ не должны импортировать LLM-клиенты (openai, anthropic) и супервизор (src.supervisor).
   Ядро МАС должно быть детерминированным и автономным от внешних языковых моделей.
2. Модули в src/safety_kernel/ (ядро безопасности) не должны импортировать модули переговоров и агентов:
   src.agents.negotiation, arbitration, repair, generator, global_search, recovery.
   Ядро безопасности должно быть независимо верифицируемым и изолированным от оптимизаторов.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent


def get_imports_from_file(file_path: Path) -> List[Tuple[int, str]]:
    """Извлекает все имена импортируемых модулей и номера строк через AST."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(file_path))

    imports: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.append((node.lineno, mod))
            for alias in node.names:
                full_name = f"{mod}.{alias.name}" if mod else alias.name
                imports.append((node.lineno, full_name))
    return imports


def test_agents_do_not_import_llm_or_supervisor():
    """src/agents/ не зависит от openai, anthropic и src.supervisor."""
    agents_dir = ROOT_DIR / "src" / "agents"
    assert agents_dir.exists(), f"Директория {agents_dir} не найдена"

    forbidden_prefixes = (
        "openai",
        "anthropic",
        "src.supervisor",
        "supervisor",
    )

    violations: List[str] = []
    for py_file in sorted(agents_dir.glob("**/*.py")):
        if py_file.name == "__init__.py":
            continue
        rel_path = py_file.relative_to(ROOT_DIR)
        for lineno, imp_name in get_imports_from_file(py_file):
            for forbidden in forbidden_prefixes:
                if imp_name == forbidden or imp_name.startswith(f"{forbidden}."):
                    violations.append(f"{rel_path}:{lineno} запрещенный импорт '{imp_name}'")

    assert not violations, "Обнаружены запрещенные зависимости в src/agents/:\n" + "\n".join(violations)


def test_safety_kernel_isolation():
    """src/safety_kernel/ не импортирует модули переговоров, оптимизации и восстановления."""
    kernel_dir = ROOT_DIR / "src" / "safety_kernel"
    if not kernel_dir.exists():
        # Директория еще не создана до этапа P3.7
        return

    forbidden_prefixes = (
        "src.agents.negotiation",
        "src.agents.arbitration",
        "src.agents.repair",
        "src.agents.generator",
        "src.agents.global_search",
        "src.agents.recovery",
    )

    violations: List[str] = []
    for py_file in sorted(kernel_dir.glob("**/*.py")):
        if py_file.name == "__init__.py":
            continue
        rel_path = py_file.relative_to(ROOT_DIR)
        for lineno, imp_name in get_imports_from_file(py_file):
            for forbidden in forbidden_prefixes:
                if imp_name == forbidden or imp_name.startswith(f"{forbidden}."):
                    violations.append(f"{rel_path}:{lineno} запрещенный импорт '{imp_name}'")

    assert not violations, "Обнаружены запрещенные зависимости в src/safety_kernel/:\n" + "\n".join(violations)
