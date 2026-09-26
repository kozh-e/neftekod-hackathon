"""Тесты теневого симулятора политики (ShadowReplayRunner, Задача P4.7 / P4.12).

Проверяет:
1. Прогон эталонных технологических сценариев S1-S4 с исходной и модифицированной политикой;
2. Ужесточение политики не ухудшает безопасность (safety_ok=True, passed=True);
3. Формирование детерминированного отчета ShadowReport с дельтой KPI.
"""

from __future__ import annotations

import pytest
from src.agents.policy import PolicyConfig
from src.supervisor.agents import PolicyProposal, PolicyProposalItem
from src.supervisor.shadow import ShadowReplayRunner


def test_shadow_replay_tightening():
    """Ужесточение alpha_quality с 0.0228 до 0.015 успешно проходит теневой прогон без роста нарушений."""
    runner = ShadowReplayRunner()
    base_policy = PolicyConfig(alpha_quality=0.0228, deadband_rub_h=1000.0)

    proposal = PolicyProposal(
        proposal_id="POL-SHADOW-001",
        items=[
            PolicyProposalItem(
                field="alpha_quality",
                old_value=0.0228,
                new_value=0.015,
                justification="Теневой тест ужесточения",
            ),
        ],
        expected_kpi_impact="Повышение запаса",
    )

    report = runner.run_shadow_test(proposal, current_policy=base_policy)
    assert report.passed is True
    assert report.kpi_delta["violations_delta"] <= 0
    assert report.patched_metrics.freeze_in_violation == 0
    assert "ОДОБРЕНО" in report.notes[0]
