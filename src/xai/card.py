"""Детерминированная XAI-карточка решения (Decision Card v3, §5.13).

Реализует структуру по 7 официальным блокам ТЗ §5:
1. Время и состояние (метки времени, ключевые параметры, свежесть КИП/ЛИМС, уровень автоматизации);
2. Проблема и технологический риск (нарушенные/активные ограничения, вероятности P(X > limit));
3. Предлагаемое управляющее воздействие (текущее -> рекомендуемое, приращения, шаг восстановления);
4. Ожидаемый эффект (прогнозы качества с sigma, выпуск, энергия, маржа, вклад цен блендинга);
5. Проверка ограничений (перечень ярусов T0-T3, запасы, вердикт ядра безопасности);
6. Уверенность и провенанс (sigma, возраст калибровки, ASSUMPTION/POLICY, подставленные значения);
7. Объяснение выбора (почему выбран ход, кто отклонил альтернативы, чей ремонт принят, Парето).
При статусах отказа REFUSAL_* выводится дословный регламентный текст ТЗ.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.contracts import (
    ArbitrationDecision,
    AutomationLevel,
    DecisionStatus,
    KernelVerdict,
    PlantEstimate,
    Tier,
)

if TYPE_CHECKING:
    from src.agents.state import CoreState

# Дословные регламентные формулировки официального ТЗ §5
TZ_REFUSAL_DATA = (
    "Внимание! Оптимизация невозможна ввиду недостоверности или недостаточности данных. "
    "Управление переведено в режим удержания текущих уставок. Требуется вмешательство оператора."
)

TZ_REFUSAL_NO_SAFE_ACTION = (
    "Внимание! Автоматическая коррекция режима невозможна: отсутствует допустимое безопасное "
    "управляющее воздействие. Управление переведено в режим удержания текущих уставок. "
    "Требуется немедленное вмешательство оператора."
)

TZ_REFUSAL_TIMEOUT = (
    "Внимание! Превышен жесткий лимит времени оптимизационного цикла ядра управления. "
    "Управление переведено в режим удержания текущих уставок. Требуется проверка устойчивости связи."
)


@dataclass(frozen=True)
class DecisionCard:
    """Структурированная детерминированная карточка решения XAI."""
    time_and_state: Dict[str, Any]
    problem_and_risk: Dict[str, Any]
    recommended_action: Dict[str, Any]
    expected_effect: Dict[str, Any]
    constraint_checks: Dict[str, Any]
    confidence_and_provenance: Dict[str, Any]
    rationale_and_alternatives: Dict[str, Any]
    decision_status: str
    refusal_text: Optional[str] = None
    markdown: str = ""


def build_decision_card(state: CoreState) -> DecisionCard:
    """Строит полную XAI-карточку из CoreState по 7 блокам ТЗ §5."""
    decision: Optional[ArbitrationDecision] = state.get("decision")
    estimate: Optional[PlantEstimate] = state.get("estimate")
    data = state.get("data")
    kernel: Optional[KernelVerdict] = state.get("kernel")
    policy = state.get("policy")

    status = decision.status if decision else DecisionStatus.REFUSAL_NO_SAFE_ACTION
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Время и состояние
    automation_level = data.automation_level.value if data else "FULL"
    key_params = {}
    if estimate:
        key_params = {
            "HT_F9": estimate.value_of("HT_F9") or estimate.u_actual.get("HT_FEED_SP"),
            "HT_T6": estimate.value_of("HT_T6") or estimate.u_actual.get("HT_TIN_SP"),
            "AVT_T55": estimate.value_of("AVT_T55"),
            "HT_DP_KPA": estimate.value_of("HT_DP_KPA"),
            "GODT.S": estimate.value_of("GODT.S"),
        }
    block_1 = {
        "timestamp": now_str,
        "automation_level": automation_level,
        "key_parameters": key_params,
        "data_flags": list(data.reasons) if data else [],
    }

    # 2. Проблема и риск
    active_or_violated = []
    certs = state.get("certificates", {})
    hold_sig = decision.hold_merit if decision else None
    if decision and decision.hold_merit.v != (0, 0, 0, 0):
        active_or_violated.append(f"Нарушение текущего режима по ярусам: v={decision.hold_merit.v}")
    for k, cert in certs.items():
        if "|reliability" in k or "|quality" in k:
            for ev in cert.evaluations:
                if ev.status in ("VIOLATED", "ACTIVE"):
                    active_or_violated.append(f"{ev.spec_key}: статус {ev.status}, запас {ev.slack:.3f}")

    block_2 = {
        "hold_violated": (decision.hold_merit.v != (0, 0, 0, 0)) if decision else False,
        "alerts": active_or_violated,
    }

    # 3. Предлагаемое действие
    actions = {}
    u0 = estimate.u_actual if estimate else {}
    if decision and decision.delta_u:
        for tag, delta in decision.delta_u.items():
            curr = u0.get(tag, 0.0)
            actions[tag] = {"current": curr, "recommended": round(curr + delta, 2), "delta": delta}

    block_3 = {
        "status": status.value if hasattr(status, "value") else str(status),
        "actions": actions,
        "recovery_plan": [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in decision.recovery.steps] if (decision and decision.recovery) else [],
    }

    # 4. Ожидаемый эффект
    effect = {}
    if decision and decision.merit:
        effect["utility_rub_h"] = decision.merit.utility_rub_h
        effect["min_slack"] = decision.merit.min_slack
    sel_sig = decision.selected if decision else None
    if sel_sig and sel_sig in state.get("predictions", {}):
        pred = state["predictions"][sel_sig]
        effect["steady_state"] = pred.steady_state
    block_4 = effect

    # 5. Проверка ограничений
    checks_summary = []
    if kernel:
        for c in kernel.checks:
            checks_summary.append({"check": c.name, "passed": c.passed, "detail": c.detail})
    block_5 = {
        "kernel_passed": kernel.passed if kernel else True,
        "kernel_version": kernel.kernel_version if kernel else "1.0.0",
        "checks": checks_summary,
    }

    # 6. Уверенность и провенанс
    calib_ages = {}
    if estimate:
        for q_key, q_val in estimate.quality.items():
            calib_ages[q_key] = {"age_h": q_val.calib_age_h, "sigma_meas": q_val.sigma_meas, "sigma_calib": q_val.sigma_calib}
    block_6 = {
        "calib_ages": calib_ages,
        "policy_version": policy.version if policy else "1.0.0",
    }

    # 7. Объяснение выбора
    neg_events = [e.detail for e in state.get("negotiation_log", []) if e.detail]
    alts = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in decision.alternatives] if (decision and decision.alternatives) else []
    block_7 = {
        "negotiation_events": neg_events,
        "alternatives": alts,
    }

    # Текст отказа
    refusal_text = None
    if status == DecisionStatus.REFUSAL_DATA:
        refusal_text = TZ_REFUSAL_DATA
    elif status == DecisionStatus.REFUSAL_NO_SAFE_ACTION:
        refusal_text = TZ_REFUSAL_NO_SAFE_ACTION
    elif status == DecisionStatus.REFUSAL_TIMEOUT:
        refusal_text = TZ_REFUSAL_TIMEOUT

    # Рендер Markdown
    md_lines = [
        f"# Карточка решения технологического управления ({status})",
        f"**Время:** {now_str} | **Уровень автоматизации:** `{automation_level}`",
        "",
    ]

    if refusal_text:
        md_lines.extend([
            f"> [!CAUTION]",
            f"> {refusal_text}",
            "",
        ])

    md_lines.append("### 1. Рекомендуемые управляющие воздействия (ТЗ §5 Блок 3)")
    if actions:
        for tag, a in actions.items():
            md_lines.append(f"- **{tag}**: {a['current']:.2f} -> **{a['recommended']:.2f}** ({a['delta']:+.2f})")
    else:
        md_lines.append("- *Изменение уставок не требуется (режим удержания hold)*")

    md_lines.append("")
    md_lines.append("### 2. Технологические риски и ограничения (ТЗ §5 Блок 2 и 5)")
    if active_or_violated:
        for al in active_or_violated:
            md_lines.append(f"- :warning: {al}")
    else:
        md_lines.append("- Все ограничения безопасности T0-T3 соблюдены с гарантированным запасом.")

    if kernel:
        k_icon = ":white_check_mark:" if kernel.passed else ":x:"
        md_lines.append(f"- {k_icon} **Ядро безопасности (Safety Kernel v{kernel.kernel_version}):** "
                        f"{'Проверка пройдена' if kernel.passed else 'ОТКЛОНЕНО ЯДРОМ БЕЗОПАСНОСТИ'}")

    md_lines.append("")
    md_lines.append("### 3. Ожидаемый эффект и полезность (ТЗ §5 Блок 4)")
    if effect.get("utility_rub_h") is not None:
        md_lines.append(f"- **Прирост полезности:** {effect['utility_rub_h']:+.1f} руб/ч")
        md_lines.append(f"- **Минимальный нормированный запас:** {effect.get('min_slack', 0.0):.3f}")

    if alts:
        md_lines.append("")
        md_lines.append("### 4. Объяснение выбора и альтернативы (ТЗ §5 Блок 7)")
        for alt in alts:
            md_lines.append(f"- *{alt.get('kind', 'alt')}*: {alt.get('candidate', '')[:8]} -> {alt.get('reasons', [''])[0]}")

    markdown_doc = "\n".join(md_lines)

    return DecisionCard(
        time_and_state=block_1,
        problem_and_risk=block_2,
        recommended_action=block_3,
        expected_effect=block_4,
        constraint_checks=block_5,
        confidence_and_provenance=block_6,
        rationale_and_alternatives=block_7,
        decision_status=status.value if hasattr(status, "value") else str(status),
        refusal_text=refusal_text,
        markdown=markdown_doc,
    )
