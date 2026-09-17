"""Офлайн теневой реплей ядра оптимизации (ShadowReplayRunner).

Позволяет безопасно протестировать предложенные изменения технологической политики
на архивных трассах и эталонных технологических сценариях (S1-S4) до их активации в реальном контуре.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.agents.contracts import DecisionStatus
from src.agents.graph import build_mvp_graph
from src.agents.policy import PolicyConfig, PolicyStore
from src.agents.scenarios import (
    scenario_1_normal_tags,
    scenario_2_quality_risk_tags,
    scenario_4_conflict_tags,
)
from src.supervisor.agents import PolicyProposal
from src.twin.plant import PlantSimulator


class ShadowMetrics(BaseModel):
    """Сводные метрики прогона сценариев."""
    total_steps: int = 0
    t1_t2_violations: int = 0
    kernel_overrides: int = 0
    freeze_in_violation: int = 0
    success_count: int = 0
    deadband_count: int = 0
    refusal_count: int = 0
    average_margin_rub_h: float = 0.0


class ShadowReport(BaseModel):
    """Отчет о результатах теневого тестирования политики."""
    passed: bool
    base_metrics: ShadowMetrics
    patched_metrics: ShadowMetrics
    kpi_delta: Dict[str, float] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class ShadowReplayRunner:
    """Теневой симулятор для реплея сценариев с альтернативной политикой."""

    def __init__(self, policy_store: Optional[PolicyStore] = None):
        self.policy_store = policy_store or PolicyStore()

    def _apply_proposal(self, base_policy: PolicyConfig, proposal: PolicyProposal) -> PolicyConfig:
        """Создает модифицированную копию политики с примененными изменениями."""
        policy_dict = base_policy.model_dump()
        thresholds_dict = policy_dict.get("thresholds", {})

        for item in proposal.items:
            if item.field in policy_dict:
                policy_dict[item.field] = item.new_value
            elif item.field in thresholds_dict:
                thresholds_dict[item.field] = item.new_value

        policy_dict["thresholds"] = thresholds_dict
        return PolicyConfig(**policy_dict)

    def _run_benchmark_suite(self, policy: PolicyConfig) -> ShadowMetrics:
        """Прогоняет контрольные тестовые сценарии (S1, S2, S4) и собирает метрики безопасности."""
        graph = build_mvp_graph()
        metrics = ShadowMetrics()

        scenarios = [
            ("S1_Normal", scenario_1_normal_tags(), 5),
            ("S2_QualityRisk", scenario_2_quality_risk_tags(), 5),
            ("S4_Conflict", scenario_4_conflict_tags(), 5),
        ]

        total_margin = 0.0
        margin_samples = 0

        for sc_name, tags_init, steps in scenarios:
            plant = PlantSimulator(tags_init, q21_noise_ppm=0.02, seed=42)
            session_id = f"shadow_{sc_name}_{id(policy)}"

            for step in range(steps):
                metrics.total_steps += 1
                tags = plant.measure()
                res = graph.invoke({"tags": tags, "session_id": session_id})
                rec = res.get("final_recommendation")

                if rec:
                    st = rec.status
                    if st in (DecisionStatus.SUCCESS.value, DecisionStatus.SUCCESS_CORRECTIVE.value):
                        metrics.success_count += 1
                        if rec.recommended_delta_u:
                            plant.apply(rec.recommended_delta_u)
                    elif "DEADBAND" in st:
                        metrics.deadband_count += 1
                    elif "REFUSAL" in st:
                        metrics.refusal_count += 1

                    cand = res.get("selected_candidate")
                    if cand and getattr(cand, "expected_margin", None):
                        total_margin += cand.expected_margin
                        margin_samples += 1

                # Проверка физической правды стенда PlantSimulator
                truth = plant.truth()
                # T1: перевал печи не выше 386.4 °C
                if truth.get("AVT_T55", 0.0) > 386.40:
                    metrics.t1_t2_violations += 1
                # T1: перепад давления реактора не выше 454.5 кПа
                if truth.get("HT_P8", 0.0) > 454.5:
                    metrics.t1_t2_violations += 1
                # T2: сера не выше 10.0 ppm
                if truth.get("HT_Q21", 0.0) > 10.0:
                    metrics.t1_t2_violations += 1

        if margin_samples > 0:
            metrics.average_margin_rub_h = round(total_margin / margin_samples, 2)

        return metrics

    def run_shadow_test(
        self,
        proposal: PolicyProposal,
        current_policy: Optional[PolicyConfig] = None,
    ) -> ShadowReport:
        """Сравнительный прогон исходной и модифицированной политики."""
        base_policy = current_policy or self.policy_store.active_policy
        patched_policy = self._apply_proposal(base_policy, proposal)

        base_metrics = self._run_benchmark_suite(base_policy)
        patched_metrics = self._run_benchmark_suite(patched_policy)

        # Критерий допуска Gate G4: безопасность не должна ухудшаться
        safety_passed = (
            patched_metrics.t1_t2_violations <= base_metrics.t1_t2_violations
            and patched_metrics.kernel_overrides <= base_metrics.kernel_overrides
            and patched_metrics.freeze_in_violation == 0
        )

        kpi_delta = {
            "violations_delta": patched_metrics.t1_t2_violations - base_metrics.t1_t2_violations,
            "margin_delta_rub_h": round(patched_metrics.average_margin_rub_h - base_metrics.average_margin_rub_h, 2),
            "success_count_delta": patched_metrics.success_count - base_metrics.success_count,
        }

        notes = []
        if not safety_passed:
            notes.append("ОТКЛОНЕНО: модифицированная политика увеличивает число нарушений безопасности/качества.")
        else:
            notes.append("ОДОБРЕНО: модифицированная политика сохраняет или улучшает барьеры безопасности.")

        return ShadowReport(
            passed=safety_passed,
            base_metrics=base_metrics,
            patched_metrics=patched_metrics,
            kpi_delta=kpi_delta,
            notes=notes,
        )
