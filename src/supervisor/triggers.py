"""Триггеры и управление охлаждением (cooldown) вызовов LLM-супервизора (triggers.py).

Соответствует спецификации §6.3 implementation_plan_v3.md:
Определяет условия автоматического запуска супервизора при инцидентах
и предотвращает лавинообразные повторные вызовы.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pydantic import BaseModel

from src.agents.contracts import DecisionStatus, DecisionTrace


class TriggerDefinition(BaseModel):
    """Описание триггера и длительности интервала охлаждения."""
    name: str
    cooldown_seconds: float
    description: str


TRIGGER_REGISTRY: Dict[str, TriggerDefinition] = {
    "KERNEL_OVERRIDE": TriggerDefinition(
        name="KERNEL_OVERRIDE",
        cooldown_seconds=0.0,
        description="Срабатывание противоаварийной защиты ядра безопасности (каждый случай)",
    ),
    "REPEATED_REFUSAL": TriggerDefinition(
        name="REPEATED_REFUSAL",
        cooldown_seconds=3600.0, # 60 мин
        description="Серия из >= 3 тактов подряд в статусе REFUSAL_*",
    ),
    "RECOVERY_STALLED": TriggerDefinition(
        name="RECOVERY_STALLED",
        cooldown_seconds=3600.0, # 60 мин
        description="Застревание процесса восстановления технологического режима",
    ),
    "LIMS_PAK_CONFLICT": TriggerDefinition(
        name="LIMS_PAK_CONFLICT",
        cooldown_seconds=7200.0, # 120 мин
        description="Конфликт пробы ЛИМС и поточного анализатора ПАК > 3σ",
    ),
    "CALIBRATION_DRIFT": TriggerDefinition(
        name="CALIBRATION_DRIFT",
        cooldown_seconds=43200.0, # 12 часов
        description="Дрейф калибровочного смещения за 24 ч выше допустимого",
    ),
    "OPERATOR_REJECTIONS": TriggerDefinition(
        name="OPERATOR_REJECTIONS",
        cooldown_seconds=3600.0, # 60 мин
        description=">= 2 отклонённые оператором рекомендации подряд",
    ),
    "BLEND_INFEASIBLE": TriggerDefinition(
        name="BLEND_INFEASIBLE",
        cooldown_seconds=7200.0, # 120 мин
        description="Недопустимость рецептуры смешения на hold >= 3 тактов",
    ),
    "SHIFT_END": TriggerDefinition(
        name="SHIFT_END",
        cooldown_seconds=36000.0, # 10 часов
        description="Плановая передача смены (08:00 / 20:00)",
    ),
    "OPERATOR_QUESTION": TriggerDefinition(
        name="OPERATOR_QUESTION",
        cooldown_seconds=120.0, # ограничение частоты: не чаще 1 раза в 2 мин
        description="Прямой запрос оператора из интерфейса",
    ),
}


class TriggerManager:
    """Менеджер отслеживания условий триггеров и периодов охлаждения."""

    def __init__(self):
        self._last_fired: Dict[str, datetime] = {}

    def can_fire(self, trigger_name: str, now: Optional[datetime] = None) -> bool:
        """Проверяет, истек ли период охлаждения для триггера."""
        if trigger_name not in TRIGGER_REGISTRY:
            return True

        current_time = now or datetime.now()
        def_obj = TRIGGER_REGISTRY[trigger_name]
        if def_obj.cooldown_seconds <= 0.0:
            return True

        last_time = self._last_fired.get(trigger_name)
        if last_time is None:
            return True

        elapsed = (current_time - last_time).total_seconds()
        return elapsed >= def_obj.cooldown_seconds

    def mark_fired(self, trigger_name: str, now: Optional[datetime] = None) -> None:
        """Фиксирует время срабатывания триггера."""
        self._last_fired[trigger_name] = now or datetime.now()

    def detect_active_triggers(
        self,
        traces: List[DecisionTrace],
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Анализирует последние трассы решений и возвращает список готовых к срабатыванию триггеров."""
        active: List[str] = []
        if not traces:
            return active

        current_time = now or datetime.now()
        latest = traces[0]

        # 1. Проверка KERNEL_OVERRIDE
        if latest.kernel and not latest.kernel.passed:
            if self.can_fire("KERNEL_OVERRIDE", current_time):
                active.append("KERNEL_OVERRIDE")

        # 2. Проверка REPEATED_REFUSAL (>= 3 такта подряд)
        if len(traces) >= 3:
            three_recent = traces[:3]
            if all("REFUSAL" in (t.decision.status.value if hasattr(t.decision.status, "value") else str(t.decision.status)) for t in three_recent):
                if self.can_fire("REPEATED_REFUSAL", current_time):
                    active.append("REPEATED_REFUSAL")

        # 3. Проверка LIMS_PAK_CONFLICT (расхождение > 3 * sigma)
        if latest.estimate and "GODT.S" in latest.estimate.quality:
            q_s = latest.estimate.quality["GODT.S"]
            if q_s.calib_age_h < 1.0 and q_s.sigma_calib > 0.0:
                # Если в первый час после пробы расхождение превышает 3σ
                if abs(q_s.value - 9.0) > 3.0 * q_s.sigma_calib:
                    if self.can_fire("LIMS_PAK_CONFLICT", current_time):
                        active.append("LIMS_PAK_CONFLICT")

        return active
