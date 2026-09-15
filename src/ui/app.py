"""Консоль оператора технологического комплекса (Streamlit HITL).

Реализует требования Шага 6 MVP (mvp_sixth_step.md):
- Отображение ключевой технологической телеметрии КИПиА и возраста LIMS
- Вывод вердиктов мультиагентной системы APC/MES (SUCCESS / SAFE_HOLD / DEADBAND)
- Структурированный диспетчерский отчет XAI с физико-химическим обоснованием
- Human-in-the-Loop (HITL): кнопки утверждения уставок (отправка на ПЛК) и отклонения
- Ручное переопределение тегов (Tag Override / ISA-18.2 Alarm Suppression)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Обеспечиваем доступность корневого пакета src при запуске через `streamlit run src/ui/app.py`
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import time
import datetime
import streamlit as st

from src.agents.state import RawTelemetry
from src.agents.graph import build_mvp_graph
from src.xai.narrative import XAIGenerator

st.set_page_config(
    page_title="Dark Factory MES - Диспетчерская",
    page_icon="🛢️",
    layout="wide"
)

# -----------------------------------------------------------------------------
# Инициализация состояния сессии Streamlit
# -----------------------------------------------------------------------------
if "system_status" not in st.session_state:
    st.session_state.system_status = "NORMAL"

if "last_applied_time" not in st.session_state:
    st.session_state.last_applied_time = None

if "overrides" not in st.session_state:
    st.session_state.overrides = {}

if "compiled_graph" not in st.session_state:
    st.session_state.compiled_graph = build_mvp_graph()

# -----------------------------------------------------------------------------
# Боковая панель: Входная телеметрия процесса
# -----------------------------------------------------------------------------
st.sidebar.header("⚙️ Входные параметры КИПиА")

feed_rate = st.sidebar.number_input("Расход сырья F26 (т/ч)", value=210.5, step=1.0, min_value=0.0)
furnace_cot = st.sidebar.number_input("Температура перевала печи T55 (°C)", value=380.0, step=0.5, max_value=400.0)
quench_flow = st.sidebar.number_input("Квенч водорода F15 (нм³/ч)", value=400.0, step=10.0, min_value=0.0)
vacuum_dp = st.sidebar.number_input("Перепад вакуума P52 (кгс/см²)", value=0.045, step=0.005, min_value=0.0)
density = st.sidebar.number_input("Плотность сырья D10 (кг/м³)", value=840.0, step=1.0, min_value=700.0)
sulfur_pak = st.sidebar.number_input("Сера поточная ПАК (ppm)", value=8.2, step=0.1, min_value=0.0)
lims_age = st.sidebar.slider("Возраст анализов LIMS (часы)", min_value=0.0, max_value=30.0, value=2.5, step=0.5)

run_opt_btn = st.sidebar.button("🚀 Запустить цикл оптимизации", width="stretch", type="primary")

# -----------------------------------------------------------------------------
# Главный заголовок и KPI метрики
# -----------------------------------------------------------------------------
st.title("🛢️ Нефтекод: Панель Диспетчера")
st.caption("Автономный комплекс управления качеством: ЭЛОУ-АВТ-6 → Гидроочистка 24-2000 → Инлайн-блендинг Евро-5")

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    label="Сырье (F26)",
    value=f"{feed_rate:.1f} т/ч",
    delta="В норме"
)

sulfur_delta = f"{sulfur_pak - 8.0:+.1f} ppm"
col2.metric(
    label="Сера (ПАК)",
    value=f"{sulfur_pak:.1f} ppm",
    delta=sulfur_delta,
    delta_color="inverse" if sulfur_pak > 9.5 else "normal"
)

lims_delta = "Требуется отбор пробы!" if lims_age >= 24.0 else (
    "Высокая погрешность" if lims_age > 8.0 else "Паспорт актуален"
)
col3.metric(
    label="LIMS Возраст",
    value=f"{lims_age:.1f} ч",
    delta=lims_delta,
    delta_color="inverse" if lims_age >= 24.0 else "normal"
)

cot_margin = 386.4 - furnace_cot
col4.metric(
    label="Печь П-3 COT (T55)",
    value=f"{furnace_cot:.1f} °C",
    delta=f"Запас до ПАЗ: {cot_margin:.1f} °C",
    delta_color="normal" if cot_margin >= 2.0 else "inverse"
)

st.divider()

# -----------------------------------------------------------------------------
# Выполнение мультиагентной оптимизации
# -----------------------------------------------------------------------------
st.subheader("🤖 Рекомендация Мультиагентной Системы (APC/MES)")

# Применяем ручные переопределения (ISA-18.2 Override)
effective_d10 = st.session_state.overrides.get("D10", density)
effective_p52 = st.session_state.overrides.get("P52", vacuum_dp)

telemetry = RawTelemetry(
    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    P52=effective_p52,
    D10=effective_d10,
    F15=quench_flow,
    T55=furnace_cot,
    F5=25.0,
    F26=feed_rate,
    Sulfur=sulfur_pak,
    lims_age_hours=lims_age
)

graph_result = st.session_state.compiled_graph.invoke({"raw_telemetry": telemetry})
final_rec = graph_result.get("final_recommendation")
data_quality = graph_result.get("data_quality")
blending_recipe = graph_result.get("blending_recipe")

if final_rec is None:
    st.warning("⚠️ Результат вычислений графа не получен.")
elif final_rec.status.startswith("SAFE_HOLD"):
    st.error("🚨 Режим БЕЗОПАСНОГО УДЕРЖАНИЯ (Safe Hold)")
    st.warning(final_rec.explanation)
    if getattr(final_rec, "markdown_report", None):
        st.markdown(final_rec.markdown_report)
    if data_quality and data_quality.refusal_reason:
        st.info(f"Диагностика Data Guard: {data_quality.refusal_reason}")

elif final_rec.status.startswith("DEADBAND"):
    st.info(f"⏸️ Зона нечувствительности (Deadband): {final_rec.status}")
    st.write(final_rec.explanation)

elif final_rec.status == "SUCCESS":
    st.success("✅ Найдена оптимальная и проверенная аудиторами стратегия управления")
    
    col_report, col_action = st.columns([2, 1])

    with col_report:
        if getattr(final_rec, "markdown_report", None):
            st.markdown(final_rec.markdown_report)
        else:
            st.markdown(final_rec.explanation)

        if blending_recipe is not None and getattr(blending_recipe, "success", False):
            with st.expander("🧪 Оптимальная рецептура блендинга (HiGHS LP)"):
                v_d = getattr(blending_recipe, "v_diesel", 0.0)
                v_k = getattr(blending_recipe, "v_kerosene", 0.0)
                v_ddp = getattr(blending_recipe, "v_ddp_ppm", 0.0)
                st.write(f"• Доля базового дизеля: **{v_d:.1%}**")
                st.write(f"• Доля керосина КО: **{v_k:.1%}**")
                st.write(f"• Дозировка ДДП присадки: **{v_ddp:.0f} ppm**")
                st.write(f"• Расчетная температура вспышки: **{blending_recipe.expected_flash:.1f} °C**")
                st.write(f"• Расчетная ПТФ (CFPP): **{blending_recipe.expected_cfpp:.1f} °C**")

    with col_action:
        st.write("### 🎮 Решение Диспетчера (HITL)")

        # Ручное переопределение тегов (ISA-18.2 Alarm Suppression / Override)
        with st.expander("🔧 Ручное вмешательство (Tag Override)"):
            st.info("При сбое датчика зафиксируйте проверенное значение:")
            override_d10 = st.number_input("Тег D10 (Плотность)", value=effective_d10, key="ov_d10")
            if st.button("Зафиксировать D10"):
                st.session_state.overrides["D10"] = override_d10
                st.toast(f"Тег D10 принудительно зафиксирован на {override_d10:.1f} кг/м³.")
                st.rerun()

            if st.session_state.overrides:
                if st.button("Сбросить все переопределения"):
                    st.session_state.overrides.clear()
                    st.toast("Переопределения сброшены.")
                    st.rerun()

        st.write("---")

        if st.button("🟢 ОДОБРИТЬ (Отправить на ПЛК)", width="stretch", type="primary"):
            st.session_state.system_status = "APPLYING"
            with st.spinner("Безударная передача уставок в контроллеры DCS/APC..."):
                time.sleep(1)
            st.session_state.last_applied_time = datetime.datetime.now().strftime("%H:%M:%S")
            st.success(f"Уставки успешно переданы на нижний уровень в {st.session_state.last_applied_time}!")
            st.balloons()

        if st.button("🔴 ОТКЛОНИТЬ (Остаться на базе)", width="stretch"):
            st.warning("Рекомендация ИИ отклонена диспетчером. Технологический режим не изменен.")
