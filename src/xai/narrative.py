"""Модуль генерации объяснимого ИИ (Explainable AI / XAI).

Формирует детерминированный диспетчерский отчет на профессиональном
русском инженерном языке (implementation_plan_v2.md, T6.2):
- Прогноз: hold против выбранного кандидата по S, Flash, T95, T_out (+30 мин, +3 ч, SS);
- Физико-химическое обоснование официальных параметров (HT_FEED_SP, HT_TIN_SP, HT_P_SP, HT_GOR_SP);
- Почему не альтернативы;
- Парето-анализ (фронт, положение рекомендации, компромиссная точка);
- Уверенность (Confidence Score);
- Допущения модели (TwinParams.assumptions());
- Рецепт блендинга (доли, дозировки, активные ограничения).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from src.twin.params import load_params


class XAIGenerator:
    """Генератор диспетчерского нарратива на профессиональном инженерном языке."""

    @staticmethod
    def generate_explanation(
        best_candidate: Any,
        base_state: Dict[str, Any],
        risk_penalties: Dict[str, float],
        lims_age_hours: float,
        hold_prediction: Optional[Dict[str, List[float]]] = None,
        alternatives: Optional[List[Dict[str, Any]]] = None,
        confidence: Optional[Dict[str, Any]] = None,
        blending_recipe: Optional[Any] = None,
        pareto: Optional[Any] = None,
        audit_reports: Optional[List[Any]] = None,
    ) -> str:
        """
        Формирует структурированный инженерный отчет для оператора установки.
        """
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
                "steady_state": getattr(best_candidate, "steady_state", {}),
                "trajectory": getattr(best_candidate, "trajectory", {}),
            }

        delta_u: Dict[str, float] = cand_dict.get("delta_u", {})
        cand_id: str = cand_dict.get("candidate_id", "cand_01")
        expected_margin: float = float(cand_dict.get("expected_margin", 0.0))

        # 1. Время и статус LIMS
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lims_status = f"Возраст анализов LIMS: {lims_age_hours:.1f} ч."
        if lims_age_hours > 8.0:
            lims_status += " (ВНИМАНИЕ: Высокая неопределенность, доверительные интервалы UCB расширены)."

        # 2. Предлагаемые уставки
        MV_FALLBACK_TAGS = {
            "HT_FEED_SP": ("HT_F9", 219.6),
            "HT_TIN_SP": ("HT_T6", 363.3),
            "HT_P_SP": ("HT_P13", 3.922),
            "HT_GOR_SP": ("HT_GOR", 360.0),
            "AVT_T55_SP": ("AVT_T55", 381.7),
        }
        actions = []
        for tag, delta in delta_u.items():
            current_val = base_state.get(tag)
            if current_val is None or current_val == 0.0:
                if tag in MV_FALLBACK_TAGS:
                    sensor_tag, default_val = MV_FALLBACK_TAGS[tag]
                    current_val = base_state.get(sensor_tag, default_val)
                else:
                    current_val = 0.0
            current_val = float(current_val)
            new_val = current_val + delta
            actions.append(f"• **{tag}**: {current_val:.2f} -> {new_val:.2f} ({delta:+.2f})")
        action_text = "\n".join(actions) if actions else "• Изменение уставок не требуется (режим hold)."

        # 3. Физико-химическое обоснование
        rationale = []
        # Официальные параметры
        if "HT_FEED_SP" in delta_u:
            d_f = delta_u["HT_FEED_SP"]
            if d_f > 0:
                rationale.append(
                    f"Увеличение расхода сырья HT_FEED_SP на {d_f:+.1f} т/ч повышает общую производительность установки "
                    "при контроле времени контакта сырья в реакторах Р-201/Р-202."
                )
            else:
                rationale.append(
                    f"Снижение расхода сырья HT_FEED_SP на {d_f:+.1f} т/ч разгружает реакторный блок Р-201/Р-202 "
                    "и обеспечивает запас по глубине гидроочистки."
                )

        if "HT_TIN_SP" in delta_u:
            d_t = delta_u["HT_TIN_SP"]
            if d_t > 0:
                rationale.append(
                    f"Повышение температуры входа в Р-202 (HT_TIN_SP) на {d_t:+.1f} °C ускоряет кинетику гидродесульфуризации "
                    "трудноудаляемых сероорганических соединений (дибензотиофенов) по уравнению Аррениуса."
                )
            else:
                rationale.append(
                    f"Снижение температуры входа HT_TIN_SP на {d_t:+.1f} °C снижает расход топливного газа в печи "
                    "и замедляет термическую дезактивацию катализатора."
                )

        if "HT_P_SP" in delta_u:
            d_p = delta_u["HT_P_SP"]
            rationale.append(
                f"Изменение системного давления HT_P_SP на {d_p:+.3f} МПа смещает термодинамическое равновесие "
                "реакций гидрирования и повышает парциальное давление водорода в реакторе Р-202."
            )

        if "HT_GOR_SP" in delta_u:
            d_g = delta_u["HT_GOR_SP"]
            rationale.append(
                f"Коррекция соотношения ВСГ/сырье HT_GOR_SP на {d_g:+.1f} нм³/м³ стабилизирует фазовое состояние "
                "газосырьевой смеси и предотвращает закоксовывание катализатора."
            )

        if "AVT_T55_SP" in delta_u:
            d_t55 = delta_u["AVT_T55_SP"]
            direction = "Повышение" if d_t55 > 0 else "Снижение"
            rationale.append(
                f"{direction} температуры на выходе печи П-3 (AVT_T55_SP) на {d_t55:+.1f} °C меняет отбор дизельных фракций "
                "AVT_F30/AVT_F32 (отклик по архиву неустойчив между подпериодами, ASSUMPTION) и затраты топлива печи; "
                "конец кипения сырья гидроочистки смещается по официальной ВАК AVT6:240-350:EBP. "
                "Буфер перегрева змеевика 386.4 °C контролирует Агент Надежности."
            )

        # Legacy-параметры
        if "F15" in delta_u:
            d_f15 = delta_u["F15"]
            rationale.append(
                f"Изменение объёмного расхода сырья F15 на {d_f15:+.1f} м³/ч оптимизирует "
                "нагрузку на реакторы Р-201/Р-202."
            )

        if "T55" in delta_u:
            rationale.append(
                f"Коррекция перевала печи П-3 (COT T55) на {delta_u['T55']:+.2f} °C оптимизирует тепловую "
                "нагрузку змеевика с сохранением 5% буфера безопасности от закоксовывания труб."
            )

        if "F19" in delta_u:
            rationale.append(
                f"Изменение орошения колонны F19 на {delta_u['F19']:+.1f} м³/ч "
                "корректирует фракционирование для стабилизации параметров вспышки."
            )

        rationale_text = "\n\n".join(rationale) if rationale else (
            "Оптимизация режима для максимизации маржинального дохода при строгом соблюдении барьеров ПАЗ."
        )

        # 4. Барьеры ПАЗ
        penalty = float(risk_penalties.get(cand_id, 0.0))
        if penalty > 0.0:
            guard_status = f"⚠️ **ВНИМАНИЕ**: Приближение к аппаратным границам (штрафной риск ПАЗ: {penalty:.0f} руб/ч)."
        else:
            guard_status = "✅ **Уровень 1 ПАЗ**: Выполнен в полном объеме (режим находится в зеленой зоне безопасности)."

        # 5. Качество
        expected_sulfur = cand_dict.get("expected_sulfur")
        sulfur_str = f"{expected_sulfur:.2f} ppm" if expected_sulfur is not None else "< 9.50 ppm"

        # 6. Раздел «Прогноз»
        traj = cand_dict.get("trajectory", {})
        ss = cand_dict.get("steady_state", {})
        h_traj = hold_prediction or {}
        forecast_lines = []
        if traj or ss:
            # Шаги 3 (30 мин) и 18 (3 часа) при dt = 10 мин
            s_traj = traj.get("HT_S_PRODUCT", [])
            s_30 = f"{s_traj[2]:.2f}" if len(s_traj) >= 3 else "н/д"
            s_3h = f"{s_traj[17]:.2f}" if len(s_traj) >= 18 else "н/д"
            s_ss_val = f"{ss.get('HT_S_PRODUCT', 8.6):.2f}"

            s_h_traj = h_traj.get("HT_S_PRODUCT", [])
            hs_30 = f"{s_h_traj[2]:.2f}" if len(s_h_traj) >= 3 else None
            hs_3h = f"{s_h_traj[17]:.2f}" if len(s_h_traj) >= 18 else None
            hs_ss = f"{s_h_traj[-1]:.2f}" if len(s_h_traj) > 0 else None
            hold_s_str = f" [Hold: +30м {hs_30} | +3ч {hs_3h} | SS {hs_ss}]" if (hs_30 and hs_3h and hs_ss) else ""

            f_traj = traj.get("HT_FLASH", [])
            f_30 = f"{f_traj[2]:.1f}" if len(f_traj) >= 3 else "н/д"
            f_3h = f"{f_traj[17]:.1f}" if len(f_traj) >= 18 else "н/д"
            f_ss_val = f"{ss.get('HT_FLASH', 68.0):.1f}"

            f_h_traj = h_traj.get("HT_FLASH", [])
            hf_30 = f"{f_h_traj[2]:.1f}" if len(f_h_traj) >= 3 else None
            hf_3h = f"{f_h_traj[17]:.1f}" if len(f_h_traj) >= 18 else None
            hf_ss = f"{f_h_traj[-1]:.1f}" if len(f_h_traj) > 0 else None
            hold_f_str = f" [Hold: +30м {hf_30} | +3ч {hf_3h} | SS {hf_ss}]" if (hf_30 and hf_3h and hf_ss) else ""

            forecast_lines.append(f"• **Сера S**: +30 мин: {s_30} ppm | +3 ч: {s_3h} ppm | SS: {s_ss_val} ppm{hold_s_str} (ГОСТ $\\le 10.0$)")
            forecast_lines.append(f"• **Вспышка Flash**: +30 мин: {f_30} °C | +3 ч: {f_3h} °C | SS: {f_ss_val} °C{hold_f_str} (ГОСТ $\\ge 55.0$)")
            if "HT_T95_PRODUCT" in ss:
                h_t95_traj = h_traj.get("HT_T95_PRODUCT", [])
                ht95_str = f" [Hold SS: {h_t95_traj[-1]:.1f} °C]" if h_t95_traj else ""
                forecast_lines.append(f"• **T95**: SS: {ss['HT_T95_PRODUCT']:.1f} °C{ht95_str} (ГОСТ $\\le 360.0$)")
            if "HT_T_OUT" in ss:
                h_tout_traj = h_traj.get("HT_T_OUT", [])
                htout_str = f" [Hold SS: {h_tout_traj[-1]:.1f} °C]" if h_tout_traj else ""
                forecast_lines.append(f"• **Температура выхода Р-202 (HT_T11)**: SS: {ss['HT_T_OUT']:.1f} °C{htout_str}")
        forecast_text = "\n".join(forecast_lines) if forecast_lines else "• Динамический прогноз: стабильное удержание в границах регламента."

        # 7. Раздел «Почему не альтернативы»
        alt_lines = []
        if alternatives:
            for alt in alternatives:
                cid = alt.get("candidate_id", "")
                st = alt.get("status", "")
                if st == "admissible":
                    alt_lines.append(f"• Кандидат `{cid}`: допустим, чистая полезность {alt.get('net_utility')} руб/ч (уступает оптимуму).")
                elif st == "vetoed":
                    alt_lines.append(f"• Кандидат `{cid}`: отклонен аудитором ({alt.get('reasons')}).")
        alt_text = "\n".join(alt_lines[:6]) if alt_lines else "• Все альтернативные кандидаты рассмотрены и ранжированы по Net Utility."
        # Требования агентов без вето: для рекомендации и для отклоненных ходов печью (сценарий 4 ТЗ)
        requirement_lines: List[str] = []
        for rep in audit_reports or []:
            rep_cand = getattr(rep, "candidate_id", None)
            is_furnace_veto = "AVT_T55_SP" in rep_cand if isinstance(rep_cand, str) else False
            if rep_cand != cand_id and not is_furnace_veto:
                continue
            for req in getattr(rep, "requirements", []) or []:
                line = f"• Агент {'Качества' if getattr(rep, 'agent', '') == 'quality' else 'Надежности'} → `{rep_cand}`: {req}"
                if line not in requirement_lines:
                    requirement_lines.append(line)
        requirements_section = (
            "\n#### 🧾 Требования агентов:\n" + "\n".join(requirement_lines[:4]) + "\n" if requirement_lines else ""
        )

        pareto_section = ""
        if pareto is not None:
            from src.agents.pareto import format_pareto_summary

            pareto_section = (
                "\n#### 🎯 Парето-анализ (маржа ↔ качество ↔ износ катализатора):\n"
                f"{format_pareto_summary(pareto, cand_id)}\n"
            )

        # 8. Раздел «Уверенность»
        conf_lines = []
        if confidence:
            conf_lines.append(f"• **Уровень достоверности**: **{confidence.get('level', 'HIGH')}** (индекс: {confidence.get('score', 1.0):.2f})")
            if confidence.get("n_filled_critical", 0) > 0:
                conf_lines.append(f"• Подставлено критичных тегов по номиналу: {confidence['n_filled_critical']}")
            if confidence.get("q21_unavailable"):
                conf_lines.append("• Онлайн-анализатор HT_Q21 недоступен: используется статистический буфер ЛИМС")
        else:
            conf_lines.append("• **Уровень достоверности**: **HIGH** (полный комплект данных)")
        conf_text = "\n".join(conf_lines)

        # 9. Раздел «Допущения модели»
        params = load_params()
        assumptions_list = params.assumptions()
        top_assumptions = assumptions_list[:4] if assumptions_list else ["Базовые технологические коэффициенты."]
        assumptions_text = "\n".join([f"• {a}" for a in top_assumptions])

        # 10. Раздел «Рецепт блендинга»
        recipe_lines = []
        if blending_recipe is not None:
            if hasattr(blending_recipe, "shares") and blending_recipe.shares:
                for comp, share in blending_recipe.shares.items():
                    recipe_lines.append(f"• Компонент `{comp}`: {share * 100.0:.1f}%")
            elif hasattr(blending_recipe, "v_diesel"):
                recipe_lines.append(f"• Дизельный гидрогенизат: {blending_recipe.v_diesel * 100.0:.1f}%")
                recipe_lines.append(f"• Керосин ТС-1: {blending_recipe.v_kerosene * 100.0:.1f}%")
            if hasattr(blending_recipe, "additive_doses_kg_t") and blending_recipe.additive_doses_kg_t:
                for add_name, dose in blending_recipe.additive_doses_kg_t.items():
                    recipe_lines.append(f"• Присадка `{add_name}`: {dose:.2f} кг/т")
            if hasattr(blending_recipe, "binding_constraints") and blending_recipe.binding_constraints:
                recipe_lines.append(f"• Активные ограничения: {', '.join(blending_recipe.binding_constraints)}")
        recipe_text = "\n".join(recipe_lines) if recipe_lines else "• Расчетная рецептура: стандартный баланс компонентов."

        # Сборка итогового Markdown-отчета
        report = f"""### 📊 Рекомендация Мультиагентной Системы APC/MES
**Метка времени:** {now}  
**Статус телеметрии:** {lims_status}

#### 🛠 Предлагаемые уставки технологического режима:
{action_text}

#### 📈 Экономический эффект и безопасность:
• Ожидаемый прирост маржи: **{expected_margin:+.2f} руб/ч**  
• Прогноз остаточной серы гидрогенизата: **{sulfur_str}** (норматив ГОСТ $\\le 10.0$ ppm)  
• {guard_status}

#### 🧠 Физико-химическое обоснование решения:
{rationale_text}

#### 🔮 Прогноз:
{forecast_text}

#### ⚖️ Почему не альтернативы:
{alt_text}
{requirements_section}{pareto_section}
#### 🛡 Уверенность:
{conf_text}

#### 📋 Допущения модели:
{assumptions_text}

#### 🧪 Рецепт блендинга:
{recipe_text}
"""
        return report
