"""Детерминированные пакеты доказательств для LLM-супервизора (EvidencePackage).

Обеспечивает формирование компактного среза данных (до 4-5k токенов)
из DecisionStore с однозначной адресацией каждого факта через EvidenceRef.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from src.agents.contracts import DecisionStatus, DecisionTrace
from src.agents.decision_store import DecisionStore, DEFAULT_STORE
from src.agents.policy import PolicyConfig, PolicyStore


class EvidenceRef(BaseModel):
    """Единица доказательства с уникальным адресом и физическим значением."""
    ref: str
    value: Any
    unit: Optional[str] = None
    description: Optional[str] = None

    def format_line(self) -> str:
        u_str = f" {self.unit}" if self.unit else ""
        desc_str = f" ({self.description})" if self.description else ""
        return f"[{self.ref}] = {self.value}{u_str}{desc_str}"


class EvidencePackage(BaseModel):
    """Компактный детерминированный пакет доказательств для подачи в контекст LLM."""
    as_of: str
    trigger: str
    code_version: str = "1.0.0"
    policy_version: str = "1.0.0"
    items: Dict[str, EvidenceRef] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)

    def add(
        self,
        ref: str,
        value: Any,
        unit: Optional[str] = None,
        description: Optional[str] = None,
    ) -> EvidenceRef:
        item = EvidenceRef(ref=ref, value=value, unit=unit, description=description)
        self.items[ref] = item
        return item

    def addresses(self) -> Set[str]:
        """Возвращает множество всех допустимых адресов доказательств."""
        return set(self.items.keys())

    def values(self) -> List[Any]:
        """Возвращает список всех числовых и строковых значений доказательств."""
        vals = []
        for item in self.items.values():
            vals.append(item.value)
        return vals

    def canonical_json(self) -> str:
        """Детерминированное каноническое представление пакета доказательств."""
        raw_items = {k: self.items[k].model_dump() for k in sorted(self.items.keys())}
        canonical_dict = {
            "as_of": self.as_of,
            "trigger": self.trigger,
            "code_version": self.code_version,
            "policy_version": self.policy_version,
            "summary": self.summary,
            "items": raw_items,
        }
        return json.dumps(canonical_dict, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def canonical_hash(self) -> str:
        """Криптографический отпечаток пакета доказательств."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def as_prompt_block(self) -> str:
        """Форматирует пакет доказательств в текстовый блок для системного промпта LLM."""
        lines = [
            f"=== ПАКЕТ ДОКАЗАТЕЛЬСТВ ТЕХНОЛОГИЧЕСКОГО КОМПЛЕКСА ===",
            f"Временная метка сбора (as_of): {self.as_of}",
            f"Триггер вызова: {self.trigger}",
            f"Версия кода: {self.code_version} | Версия политики: {self.policy_version}",
            f"Контрольная сумма доказательств (hash): {self.canonical_hash()[:16]}",
            "",
            "--- РЕЕСТР АДРЕСУЕМЫХ ДАННЫХ (EvidenceRef) ---",
        ]

        for ref_key in sorted(self.items.keys()):
            lines.append(self.items[ref_key].format_line())

        lines.append("")
        lines.append("--- СВОДНЫЕ АГРЕГАТЫ ОКНА НАБЛЮДЕНИЯ ---")
        lines.append(json.dumps(self.summary, indent=2, ensure_ascii=False))
        lines.append("=====================================================")
        return "\n".join(lines)


def build_evidence_package(
    store: Optional[DecisionStore] = None,
    as_of: Optional[datetime] = None,
    trigger: str = "SCHEDULED",
    window_h: float = 24.0,
    policy: Optional[PolicyConfig] = None,
) -> EvidencePackage:
    """Детерминированная сборка компактного среза доказательств из DecisionStore."""
    store = store or DEFAULT_STORE
    as_of_dt = as_of or datetime.now()
    as_of_str = as_of_dt.isoformat()

    policy_store = PolicyStore()
    active_policy = policy or policy_store.active_policy

    pkg = EvidencePackage(
        as_of=as_of_str,
        trigger=trigger,
        code_version="1.0.0",
        policy_version=active_policy.version,
    )

    # 1. Параметры активной политики (POLICY)
    pkg.add("policy.alpha_quality", active_policy.alpha_quality, unit="", description="квантиль риска ГОСТ")
    pkg.add("policy.alpha_equipment", active_policy.alpha_equipment, unit="", description="квантиль риска ПАЗ")
    pkg.add("policy.deadband_rub_h", active_policy.deadband_rub_h, unit="руб/ч", description="порог зоны нечувствительности маржи")
    pkg.add("policy.cautious_step_scale", active_policy.cautious_step_scale, unit="", description="масштаб шага в режиме CAUTIOUS")
    pkg.add("policy.cautious_calib_age_h", active_policy.thresholds.cautious_calib_age_h, unit="ч", description="порог возраста калибровки для CAUTIOUS")
    pkg.add("policy.furnace_benefit_confidence", active_policy.furnace_benefit_confidence, unit="", description="порог уверенности выгоды печи")

    # 2. Извлечение недавних решений
    recent_traces: List[DecisionTrace] = store.list_recent(limit=100)

    total_cycles = len(recent_traces)
    pkg.add("status.total_cycles", total_cycles, unit="тактов", description="всего тактов в выборке")

    if total_cycles == 0:
        pkg.summary = {"note": "Журнал решений пуст, использованы базовые спецификации."}
        return pkg

    status_counts: Dict[str, int] = {}
    kernel_overrides = 0
    refusals_list: List[str] = []
    rejections_count = 0
    clamping_flags = 0

    latest_trace = recent_traces[0]

    for trace in recent_traces:
        st = trace.decision.status.value if hasattr(trace.decision.status, "value") else str(trace.decision.status)
        status_counts[st] = status_counts.get(st, 0) + 1

        if trace.kernel and not trace.kernel.passed:
            kernel_overrides += 1

        if "REFUSAL" in st:
            if len(refusals_list) < 10:
                refusals_list.append(f"{trace.cycle_id}:{st}")

        if trace.data and trace.data.automation_level.value == "REFUSAL_DATA":
            clamping_flags += 1

    # Запись статусов в EvidenceRef
    success_count = status_counts.get(DecisionStatus.SUCCESS.value, 0) + status_counts.get(DecisionStatus.SUCCESS_CORRECTIVE.value, 0)
    deadband_count = status_counts.get(DecisionStatus.NO_CHANGE_DEADBAND.value, 0)
    refusal_count = sum(cnt for st, cnt in status_counts.items() if "REFUSAL" in st)

    pkg.add("status.success_cycles", success_count, unit="тактов", description="успешных оптимизаций и коррекций")
    pkg.add("status.deadband_cycles", deadband_count, unit="тактов", description="заморозок в зоне нечувствительности")
    pkg.add("status.refusal_cycles", refusal_count, unit="тактов", description="число отказов автоматики")
    pkg.add("status.kernel_overrides", kernel_overrides, unit="раз", description="срабатываний предохранительного ядра ПАЗ")
    pkg.add("status.clamping_events", clamping_flags, unit="тактов", description="событий аппаратного клампинга телеметрии")

    # 3. Данные последней оценки технологического состояния (PlantEstimate)
    if latest_trace.estimate:
        est = latest_trace.estimate
        # Оценка серы гидрогенизата ГО ДТ
        if "GODT.S" in est.quality:
            q_s = est.quality["GODT.S"]
            pkg.add("quality.godt_s.value", round(q_s.value, 2), unit="мг/кг", description="оценка содержания серы ГО ДТ")
            pkg.add("quality.godt_s.sigma_calib", round(q_s.sigma_calib, 3), unit="мг/кг", description="неопределенность калибровки серы")
            pkg.add("quality.godt_s.calib_age_h", round(q_s.calib_age_h, 1), unit="ч", description="возраст последней пробы ЛИМС по сере")

        # Оценка вспышки ГО ДТ
        if "GODT.FLASH" in est.quality:
            q_fl = est.quality["GODT.FLASH"]
            pkg.add("quality.godt_flash.value", round(q_fl.value, 1), unit="°C", description="оценка температуры вспышки ГО ДТ")
            pkg.add("quality.godt_flash.sigma_calib", round(q_fl.sigma_calib, 2), unit="°C", description="неопределенность калибровки вспышки")
            pkg.add("quality.godt_flash.calib_age_h", round(q_fl.calib_age_h, 1), unit="ч", description="возраст пробы ЛИМС по вспышке")

        # Измеренные ограничения безопасности
        if "AVT_T55" in est.measured_constraints:
            t55 = est.measured_constraints["AVT_T55"]
            pkg.add("equipment.avt_t55.measured", round(t55, 1), unit="°C", description="температура перевала печи П-3 (AVT_T55)")
        if "HT_P8" in est.measured_constraints:
            dp = est.measured_constraints["HT_P8"]
            pkg.add("equipment.ht_p8.measured", round(dp, 1), unit="кПа", description="перепад давления реактора Р-202 (HT_P8)")
        if "AVT_F31" in est.measured_constraints:
            f31 = est.measured_constraints["AVT_F31"]
            pkg.add("equipment.avt_f31.measured", round(f31, 1), unit="т/ч", description="расход мазута в печь П-3 (AVT_F31)")

    # 4. Сводный словарь агрегатов для верхнеуровневого обзора
    pkg.summary = {
        "status_distribution": status_counts,
        "recent_refusals": refusals_list,
        "kernel_overrides_total": kernel_overrides,
        "latest_cycle_id": latest_trace.cycle_id,
        "latest_status": latest_trace.decision.status.value if hasattr(latest_trace.decision.status, "value") else str(latest_trace.decision.status),
    }

    return pkg
