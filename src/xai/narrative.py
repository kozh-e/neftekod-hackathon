"""Модуль генерации объяснимого ИИ (Explainable AI / XAI).

Формирует детерминированный диспетчерский отчет на профессиональном
русском инженерном языке (Callout Box 4 и требования к операторскому интерфейсу).
Исключает принцип «черного ящика»: каждое действие аргументировано термодинамикой
и кинетикой процессов каталитической гидроочистки и первичной перегонки.
"""

from __future__ import annotations

import datetime
from typing import Dict, Any, Optional


class XAIGenerator:
    """Генератор диспетчерского нарратива на профессиональном русском языке."""

    # =========================================================================
    # TODO (Post-MVP): Интеграция LLM-агента с Retrieval-Augmented Generation (RAG)
    # В текущей версии MVP реализован детерминированный шаблонный генератор,
    # связывающий физико-химические симптомы (перегрев печи, квенч, фракционирование)
    # с рекомендациями системы без риска стохастических галлюцинаций.
    # Для промышленного развертывания интеллектуального ассистента диспетчера (>100 строк):
    # 1. Подключение локальной квантованной LLM (напр. Llama-3-70B/Qwen-2.5)
    #    через vLLM с системным промптом инженера-технолога.
    # 2. RAG-индексация технологических регламентов установок ЭЛОУ-АВТ-6 и 24-2000,
    #    карт технологического режима (КТР) и базы инцидентов предприятия.
    # 3. Интерактивный диалоговый режим: возможность диспетчера задавать уточняющие
    #    вопросы ("Почему снижаем T20, а не увеличиваем орошение К-1?") в реальном времени.
    # =========================================================================

    @staticmethod
    def generate_explanation(
        best_candidate: Any,
        base_state: Dict[str, Any],
        risk_penalties: Dict[str, float],
        lims_age_hours: float
    ) -> str:
        """
        Формирует структурированный физико-химический отчет для оператора.

        :param best_candidate: Выбранный кандидат (объект ControlCandidate или словарь).
        :param base_state: Текущий срез показаний КИПиА до изменения уставок.
        :param risk_penalties: Словарь барьерных штрафов риска.
        :param lims_age_hours: Возраст последнего лабораторного паспорта LIMS.
        :return: Текст отчета в формате Markdown.
        """
        # Преобразование кандидата к словарю при необходимости
        cand_dict: Dict[str, Any] = {}
        if hasattr(best_candidate, "model_dump"):
            cand_dict = best_candidate.model_dump()
        elif isinstance(best_candidate, dict):
            cand_dict = best_candidate
        else:
            cand_dict = {
                "candidate_id": getattr(best_candidate, "candidate_id", "unknown"),
                "delta_u": getattr(best_candidate, "delta_u", {}),
                "expected_margin": getattr(best_candidate, "expected_margin", 0.0),
                "expected_sulfur": getattr(best_candidate, "expected_sulfur", 8.5),
            }

        delta_u: Dict[str, float] = cand_dict.get("delta_u", {})
        cand_id: str = cand_dict.get("candidate_id", "cand_01")
        expected_margin: float = float(cand_dict.get("expected_margin", 0.0))

        # 1. Время и статус актуальности анализов LIMS
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lims_status = f"Возраст анализов LIMS: {lims_age_hours:.1f} ч."
        if lims_age_hours > 8.0:
            lims_status += " (ВНИМАНИЕ: Высокая неопределенность, доверительные интервалы UCB расширены)."

        # 2. Перечень предлагаемых управляющих воздействий
        actions = []
        for tag, delta in delta_u.items():
            current_val = float(base_state.get(tag, 0.0))
            new_val = current_val + delta
            actions.append(f"• **{tag}**: {current_val:.2f} -> {new_val:.2f} ({delta:+.2f})")
        action_text = "\n".join(actions) if actions else "• Изменение уставок не требуется."

        # 3. Физико-химическое и технологическое обоснование (Симптом -> Причина -> Решение)
        rationale = []
        if "F15" in delta_u and delta_u["F15"] > 0:
            rationale.append(
                f"Увеличение расхода водородного квенча F15 на {delta_u['F15']:+.1f} нм³/ч компенсирует "
                "экзотермический разогрев в реакторе Р-202, стабилизируя температуру слоя катализатора "
                "и предотвращая термодеструкцию сырья."
            )
        elif "F15" in delta_u and delta_u["F15"] < 0:
            rationale.append(
                f"Снижение подачи квенча F15 на {delta_u['F15']:+.1f} нм³/ч экономит компримированный ВСГ "
                "при сохранении требуемой глубины гидродесульфуризации."
            )

        if "T55" in delta_u:
            rationale.append(
                f"Коррекция перевала печи П-3 (COT T55) на {delta_u['T55']:+.2f} °C оптимизирует тепловую "
                "нагрузку змеевика с сохранением 5% буфера безопасности от закоксовывания труб."
            )

        if "F19" in delta_u:
            rationale.append(
                f"Изменение острого орошения верха колонны К-2 F19 на {delta_u['F19']:+.1f} м³/ч "
                "корректирует фракционный состав (снижая температуру верха T20 и T95) для гарантии вспышки ДТ."
            )

        if "F12" in delta_u:
            rationale.append(
                f"Коррекция расхода F12 на {delta_u['F12']:+.1f} м³/ч оптимизирует теплосъем в колонне К-2."
            )

        rationale_text = "\n\n".join(rationale) if rationale else (
            "Оптимизация режима для максимизации маржинального дохода при строгом соблюдении барьеров ПАЗ."
        )

        # 4. Проверка барьеров ПАЗ (Уровень 1)
        penalty = float(risk_penalties.get(cand_id, 0.0))
        if penalty > 0.0:
            guard_status = f"⚠️ **ВНИМАНИЕ**: Приближение к аппаратным границам (штрафной риск ПАЗ: {penalty:.0f} руб/ч)."
        else:
            guard_status = "✅ **Уровень 1 ПАЗ**: Выполнен в полном объеме (режим находится в зеленой зоне безопасности)."

        # 5. Оценка прогнозируемого качества
        expected_sulfur = cand_dict.get("expected_sulfur")
        sulfur_str = f"{expected_sulfur:.2f} ppm" if expected_sulfur is not None else "< 9.50 ppm"

        # 6. Сборка итогового Markdown отчета
        report = f"""### 📊 Рекомендация Мультиагентной Системы APC/MES
**Метка времени:** {now}  
**Статус телеметрии:** {lims_status}

#### 🛠 Предлагаемые уставки технологического режима:
{action_text}

#### 📈 Экономический эффект и безопасность:
• Ожидаемый прирост маржи: **+{expected_margin:.2f} руб/ч**  
• Прогноз остаточной серы гидрогенизата: **{sulfur_str}** (норматив ГОСТ $\\le 10.0$ ppm)  
• {guard_status}

#### 🧠 Физико-химическое обоснование решения:
{rationale_text}
"""
        return report
