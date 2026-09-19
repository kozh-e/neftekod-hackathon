"""Тест отсутствия захардкоженных лимитов в src/console/*.py (R6)."""

import ast
from pathlib import Path
import pytest

FORBIDDEN_LIMITS = {10.0, 55.0, 454.5, 386.4, 380.5, 252.8}


def test_no_hardcoded_limits():
    console_dir = Path("src/console")
    python_files = list(console_dir.glob("*.py"))
    assert len(python_files) >= 5, "Ожидаются модули в src/console"

    violations = []
    for p in python_files:
        # Пропускаем contracts.py (там могут быть метаданные или аннотации типов)
        if p.name == "contracts.py":
            continue

        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                # Исключаем строки (docstrings, комментарии)
                if isinstance(node.value, (int, float)):
                    if float(node.value) in FORBIDDEN_LIMITS:
                        violations.append(
                            f"{p.name}:{node.lineno}: захардкоженное число {node.value} "
                            "(должно считываться из src/agents/registry.py)"
                        )

    assert not violations, "Обнаружены захардкоженные лимиты:\n" + "\n".join(violations)
