"""Консоль оператора технологического комплекса (Streamlit HITL).

Реализует требования Шага 6 и implementation_plan_v2.md (T6.4):
- Официальные параметры КИПиА в боковой панели (HT_F9, HT_T6, HT_P13, HT_GOR, HT_Q21, HT_P8, AVT_T55);
- Отображение вердиктов APC/MES (SUCCESS, SUCCESS_CORRECTIVE, SAFE_HOLD, DEADBAND);
- График динамического прогноза серы и вспышки (hold против выбранного кандидата);
- Таблица альтернатив для оператора (Explainable AI);
- Рецептура блендинга из резервуаров (3 компонента + присадки А/Б);
- Парето-фронт допустимых режимов (3D, 2D-проекция, параллельные координаты, таблица);
- Кнопка «ОДОБРИТЬ» фиксирует уставки через TwinSessionStore (TWIN_STORE.commit_applied_move).
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
import sys
import time

# Обеспечиваем доступность корневого пакета src при запуске через streamlit
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st

from src.agents.graph import build_mvp_graph
from src.agents.pareto import (
    build_parallel_coordinates_figure,
    build_pareto_2d_figure,
    build_pareto_3d_figure,
    trade_off_alternatives,
)
from src.agents.state import RawTelemetry
from src.twin.params import load_params
from src.twin.session import TWIN_STORE
from src.twin.tags import NOMINAL_OPERATING_POINT

st.set_page_config(
    page_title="Dark Factory MES - Диспетчерская",
    page_icon="🛢️",
    layout="wide",
)

# -----------------------------------------------------------------------------
# Инициализация состояния сессии Streamlit
# -----------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = f"session_{int(time.time())}"

if "system_status" not in st.session_state:
    st.session_state.system_status = "NORMAL"

if "last_applied_time" not in st.session_state:
    st.session_state.last_applied_time = None

if "overrides" not in st.session_state:
    st.session_state.overrides = {}

if "compiled_graph" not in st.session_state:
    st.session_state.compiled_graph = build_mvp_graph()

# -----------------------------------------------------------------------------
# Боковая панель: Входная телеметрия процесса (официальные теги §7.1)
# -----------------------------------------------------------------------------
st.sidebar.header("⚙️ Входные параметры КИПиА")

feed_f9 = st.sidebar.number_input(
    "Расход сырья 24-2000 HT_F9 (т/ч)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_F9", 219.6)),
    step=1.0,
    min_value=50.0,
    max_value=300.0,
)
temp_t6 = st.sidebar.number_input(
    "Температура входа Р-202 HT_T6 (°C)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_T6", 363.3)),
    step=0.5,
    min_value=320.0,
    max_value=400.0,
)
press_p13 = st.sidebar.number_input(
    "Давление входа Р-202 HT_P13 (МПа)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_P13", 3.922)),
    step=0.01,
    min_value=3.0,
    max_value=5.0,
)
gor_val = st.sidebar.number_input(
    "Кратность ВСГ/сырье HT_GOR (нм³/м³)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_GOR", 360.0)),
    step=5.0,
    min_value=200.0,
    max_value=600.0,
)
sulfur_q21 = st.sidebar.number_input(
    "Сера онлайн-анализатора HT_Q21 (ppm)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_Q21", 8.43)),
    step=0.1,
    min_value=0.0,
    max_value=50.0,
)
dp_p8 = st.sidebar.number_input(
    "Перепад давления Р-202 HT_P8 (МПа)",
    value=float(NOMINAL_OPERATING_POINT.get("HT_P8", 0.177)),
    step=0.005,
    min_value=0.05,
    max_value=0.50,
)
cot_t55 = st.sidebar.number_input(
    "Перевал печи П-3 AVT_T55 (°C)",
    value=float(NOMINAL_OPERATING_POINT.get("AVT_T55", 381.7)),
    step=0.5,
    min_value=350.0,
    max_value=400.0,
)
lims_age = st.sidebar.slider(
    "Возраст анализов LIMS (часы)",
    min_value=0.0,
    max_value=30.0,
    value=2.0,
    step=0.5,
)

# -----------------------------------------------------------------------------
# Боковая панель: Рыночные цены и экономика процесса
# -----------------------------------------------------------------------------
twin_defaults = load_params()
econ_defaults = twin_defaults.economics
blend_defaults = twin_defaults.blend

st.sidebar.markdown("---")
with st.sidebar.expander("💰 Параметры рынка и тарифов", expanded=False):
    st.caption("Цены СПбМТСБ, тарифы ФАС и себестоимость энергоносителей")

    st.markdown("##### Сырье и дистилляты (СПбМТСБ)")
    price_godt = st.number_input(
        "ГО ДТ Евро-5 (руб/т)",
        value=float(econ_defaults.price_godt),
        step=500.0,
        min_value=30000.0,
        max_value=120000.0,
        key="econ_price_godt",
    )
    price_straight = st.number_input(
        "Прямогонный дизель F30+F32 (руб/т)",
        value=float(econ_defaults.price_straight_run),
        step=500.0,
        min_value=25000.0,
        max_value=100000.0,
        key="econ_price_straight",
    )
    price_crude = st.number_input(
        "Сырая нефть Urals (руб/т)",
        value=float(econ_defaults.price_crude_oil),
        step=500.0,
        min_value=20000.0,
        max_value=80000.0,
        key="econ_price_crude",
    )
    price_kerosene = st.number_input(
        "Керосин ТС-1 (руб/т)",
        value=float(econ_defaults.price_kerosene),
        step=500.0,
        min_value=40000.0,
        max_value=150000.0,
        key="econ_price_kerosene",
    )
    price_gasoil = st.number_input(
        "Газойль вторичный (руб/т)",
        value=float(econ_defaults.price_gasoil),
        step=500.0,
        min_value=25000.0,
        max_value=100000.0,
        key="econ_price_gasoil",
    )

    st.markdown("##### Присадки блендинга")
    price_ddp = st.number_input(
        "Депрессорная ДДП A (руб/т)",
        value=float(blend_defaults.additive_a_price_rub_t),
        step=5000.0,
        min_value=100000.0,
        max_value=1000000.0,
        key="econ_price_ddp",
    )
    price_cetane = st.number_input(
        "Цетаноповышающая B (руб/т)",
        value=float(blend_defaults.additive_b_price_rub_t),
        step=5000.0,
        min_value=100000.0,
        max_value=800000.0,
        key="econ_price_cetane",
    )

    st.markdown("##### Энергоресурсы и катализатор")
    fuel_gas_mwh = st.number_input(
        "Тепловая энергия печей (руб/МВт·ч)",
        value=float(econ_defaults.fuel_rub_mwh),
        step=50.0,
        min_value=1000.0,
        max_value=10000.0,
        key="econ_fuel_rub_mwh",
    )
    power_kwh = st.number_input(
        "Тариф на э/э (руб/кВт·ч)",
        value=float(econ_defaults.power_rub_kwh),
        step=0.1,
        min_value=2.0,
        max_value=20.0,
        key="econ_power_kwh",
    )
    h2_cost = st.number_input(
        "Водород КЦА (руб/нм³)",
        value=float(econ_defaults.h2_rub_per_nm3),
        step=0.5,
        min_value=5.0,
        max_value=50.0,
        key="econ_h2_cost",
    )
    cat_cost = st.number_input(
        "Дезактивация катализатора (руб/(ч·°C))",
        value=float(econ_defaults.catalyst_rub_h_per_degC),
        step=25.0,
        min_value=50.0,
        max_value=2000.0,
        key="econ_cat_cost",
    )
    min_margin = st.number_input(
        "Порог Deadband (руб/ч)",
        value=float(econ_defaults.min_margin_improvement),
        step=100.0,
        min_value=0.0,
        max_value=10000.0,
        key="econ_deadband",
    )

    crack_straight_run = price_godt - price_straight
    crack_crude = (price_godt * econ_defaults.y_liq) - price_crude
    st.markdown("---")
    st.markdown(f"**Спред Прямогон → ДТ:** `{crack_straight_run:,.0f}` руб/т")
    st.markdown(f"**Спред Нефть → ДТ:** `{crack_crude:,.0f}` руб/т")

    if st.button("🔄 Сбросить экономику к бенчмаркам"):
        for k in (
            "econ_price_godt", "econ_price_straight", "econ_price_crude",
            "econ_price_kerosene", "econ_price_gasoil", "econ_price_ddp", "econ_price_cetane",
            "econ_fuel_rub_mwh", "econ_power_kwh", "econ_h2_cost", "econ_cat_cost", "econ_deadband"
        ):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

economics_input = {
    "price_godt": price_godt,
    "price_straight_run": price_straight,
    "price_crude_oil": price_crude,
    "price_kerosene": price_kerosene,
    "price_gasoil": price_gasoil,
    "additive_a_price_rub_t": price_ddp,
    "additive_b_price_rub_t": price_cetane,
    "fuel_rub_mwh": fuel_gas_mwh,
    "power_rub_kwh": power_kwh,
    "compressor_rub_per_nm3": round(power_kwh * 0.05, 4),
    "h2_rub_per_nm3": h2_cost,
    "catalyst_rub_h_per_degC": cat_cost,
    "min_margin_improvement": min_margin,
}


# -----------------------------------------------------------------------------
# Подготовка входной телеметрии и вызов графа
# -----------------------------------------------------------------------------
effective_d10 = st.session_state.overrides.get("D10", 840.0)
effective_p52 = st.session_state.overrides.get("P52", 0.045)

tags_input = {
    "HT_F9": feed_f9,
    "HT_T6": temp_t6,
    "HT_P13": press_p13,
    "HT_GOR": gor_val,
    "HT_Q21": sulfur_q21,
    "HT_P8": dp_p8,
    "AVT_T55": cot_t55,
    "lims_age_hours": lims_age,
    "AVT_P52": effective_p52,
    "AVT_D10": effective_d10,
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "P52": effective_p52,
    "D10": effective_d10,
    "F15": 3400.0,
    "T55": cot_t55,
}

raw_telemetry = RawTelemetry(
    timestamp=tags_input["timestamp"],
    P52=effective_p52,
    D10=effective_d10,
    F15=3400.0,
    T55=cot_t55,
    F5=25.0,
    F26=feed_f9,
    lims_age_hours=lims_age,
)

state_input = {
    "raw_telemetry": raw_telemetry,
    "tags": tags_input,
    "session_id": st.session_state.session_id,
    "economics": economics_input,
}

graph_result = st.session_state.compiled_graph.invoke(state_input)
final_rec = graph_result.get("final_recommendation")
data_quality = graph_result.get("data_quality")
blending_recipe = graph_result.get("blending_recipe")
selected_cand = graph_result.get("selected_candidate")
hold_prediction = graph_result.get("hold_prediction", {})
alternatives = graph_result.get("alternatives", [])

# -----------------------------------------------------------------------------
# Главный заголовок и KPI метрики (технологические + экономические)
# -----------------------------------------------------------------------------
st.title("🛢️ Нефтекод: Панель Диспетчера")
st.caption("Автономный комплекс управления качеством: ЭЛОУ-АВТ-6 → Гидроочистка 24-2000 → Инлайн-блендинг Евро-5")

col1, col2, col3, col4 = st.columns(4)
col1.metric(label="Сырье (HT_F9)", value=f"{feed_f9:.1f} т/ч", delta="В норме")

sulfur_delta = f"{sulfur_q21 - 8.6:+.2f} ppm"
col2.metric(
    label="Сера онлайн (HT_Q21)",
    value=f"{sulfur_q21:.2f} ppm",
    delta=sulfur_delta,
    delta_color="inverse" if sulfur_q21 > 9.5 else "normal",
)

lims_delta = (
    "Требуется отбор пробы!"
    if lims_age >= 24.0
    else ("Высокая погрешность" if lims_age > 8.0 else "Паспорт актуален")
)
col3.metric(
    label="LIMS Возраст",
    value=f"{lims_age:.1f} ч",
    delta=lims_delta,
    delta_color="inverse" if lims_age >= 24.0 else "normal",
)

cot_margin = 386.4 - cot_t55
col4.metric(
    label="Печь П-3 COT (AVT_T55)",
    value=f"{cot_t55:.1f} °C",
    delta=f"Запас до ПАЗ: {cot_margin:.1f} °C",
    delta_color="normal" if cot_margin >= 2.0 else "inverse",
)

# Экономические KPI (Crack-Spread и маржинальность процесса)
ecol1, ecol2, ecol3, ecol4 = st.columns(4)

gross_hourly_margin = feed_f9 * (price_godt * econ_defaults.y_liq - price_straight)
ecol1.metric(
    label="Спред Прямогон → ГО ДТ",
    value=f"{crack_straight_run:,.0f} ₽/т",
    delta="Маржа гидроочистки",
)
ecol2.metric(
    label="Сквозной спред Нефть → ГО ДТ",
    value=f"{crack_crude:,.0f} ₽/т",
    delta=f"Выход {econ_defaults.y_liq * 100:.0f}%",
)
ecol3.metric(
    label="Валовая маржа ГО",
    value=f"{gross_hourly_margin:,.0f} ₽/ч",
    delta=f"При {feed_f9:.1f} т/ч сырья",
)

net_utility_disp = (
    selected_cand.expected_margin
    if (selected_cand and selected_cand.expected_margin is not None)
    else 0.0
)
ecol4.metric(
    label="Δ Маржи рекомендации (Net Utility)",
    value=f"{net_utility_disp:+,.0f} ₽/ч",
    delta="К текущему hold" if abs(net_utility_disp) > 1e-3 else "В точке оптимума",
    delta_color="normal" if net_utility_disp >= 0 else "inverse",
)

st.divider()

st.subheader("🤖 Решение Мультиагентной Системы (APC/MES)")

if final_rec is None:
    st.warning("⚠️ Результат вычислений графа не получен.")
elif final_rec.status.startswith("SAFE_HOLD"):
    st.error("🚨 Режим БЕЗОПАСНОГО УДЕРЖАНИЯ (Safe Hold)")
    st.warning(final_rec.explanation)
    if data_quality and data_quality.refusal_reason:
        st.info(f"Диагностика Data Guard: {data_quality.refusal_reason}")

elif final_rec.status.startswith("DEADBAND"):
    st.info(f"⏸️ Зона нечувствительности (Deadband): {final_rec.status}")
    if getattr(final_rec, "markdown_report", None):
        st.markdown(final_rec.markdown_report)
    else:
        st.write(final_rec.explanation)

elif final_rec.status.startswith("SUCCESS"):
    if final_rec.status == "SUCCESS_CORRECTIVE":
        st.warning("⚠️ Режим АВТОМАТИЧЕСКОЙ КОРРЕКЦИИ: базовый режим нарушает нормативы качества/ПАЗ")
    else:
        st.success("✅ Найдена оптимальная стратегия управления технологическим комплексом")

    col_report, col_action = st.columns([2, 1])

    with col_report:
        if getattr(final_rec, "markdown_report", None):
            st.markdown(final_rec.markdown_report)
        else:
            st.markdown(final_rec.explanation)

        # График динамического прогноза
        if selected_cand and selected_cand.trajectory and hold_prediction:
            with st.expander("📈 Динамический прогноз: удержание (hold) vs рекомендация", expanded=True):
                cand_s = selected_cand.trajectory.get("HT_S_PRODUCT", [])
                hold_s = hold_prediction.get("HT_S_PRODUCT", [])
                if cand_s and hold_s:
                    steps = list(range(1, min(len(cand_s), len(hold_s)) + 1))
                    df_s = pd.DataFrame({
                        "Такт (x10 мин)": steps,
                        "Рекомендация (Сера, ppm)": cand_s[: len(steps)],
                        "Hold (Сера, ppm)": hold_s[: len(steps)],
                    }).set_index("Такт (x10 мин)")
                    st.line_chart(df_s)

        # Таблица альтернатив
        if alternatives:
            with st.expander("⚖️ Таблица альтернативных технологических ходов"):
                rows = []
                for a in alternatives:
                    rows.append({
                        "ID": a.get("candidate_id"),
                        "Статус": a.get("status"),
                        "Чистая маржа, руб/ч": a.get("net_utility"),
                        "Выбран": "Да" if a.get("is_selected") else "Нет",
                        "Причина / Штрафы": a.get("reasons") or f"Штраф: {a.get('risk_penalty', 0)} руб/ч",
                    })
                st.dataframe(pd.DataFrame(rows), width="stretch")

        # Детализация маржи кандидата
        if selected_cand and selected_cand.margin_breakdown:
            with st.expander("💰 Детализация операционной маржи (Margin Breakdown)"):
                mb = selected_cand.margin_breakdown
                df_mb = pd.DataFrame([
                    {"Статья": "📈 Сырьевой поток (Throughput)", "Вклад (руб/ч)": f"{mb.get('throughput', 0.0):+,.2f}"},
                    {"Статья": "🛢️ Отбор дизеля АВТ (печь П-3)", "Вклад (руб/ч)": f"{mb.get('avt_diesel', 0.0):+,.2f}"},
                    {"Статья": "🔥 Подогрев печи (Топливный газ)", "Вклад (руб/ч)": f"{-mb.get('furnace', 0.0):+,.2f}"},
                    {"Статья": "⚡ Компримирование ВСГ (Электроэнергия)", "Вклад (руб/ч)": f"{-mb.get('compressor', 0.0):+,.2f}"},
                    {"Статья": "🗜️ Системное давление", "Вклад (руб/ч)": f"{-mb.get('pressure', 0.0):+,.2f}"},
                    {"Статья": "💧 Водород КЦА", "Вклад (руб/ч)": f"{-mb.get('hydrogen', 0.0):+,.2f}"},
                    {"Статья": "🧪 Дезактивация катализатора", "Вклад (руб/ч)": f"{-mb.get('catalyst', 0.0):+,.2f}"},
                    {"Статья": "🏆 ИТОГО ЧИСТАЯ МАРЖА (Net Utility)", "Вклад (руб/ч)": f"{selected_cand.expected_margin:+,.2f}"},
                ])
                st.dataframe(df_mb, width="stretch", hide_index=True)
                if "furnace_fuel_gas_nm3" in mb and abs(mb.get("furnace_fuel_gas_nm3", 0.0)) > 1e-3:
                    st.caption(f"Изменение расхода топливного газа печи: {mb.get('furnace_fuel_gas_nm3'):+.1f} нм³/ч (тепловая нагрузка: {mb.get('furnace_mwh', 0.0):+.3f} МВт·ч)")


        # Рецептура блендинга
        if blending_recipe is not None and getattr(blending_recipe, "success", False):
            with st.expander("🧪 Оптимальная рецептура товарного блендинга (HiGHS LP)", expanded=True):
                shares = getattr(blending_recipe, "shares", {})
                if shares:
                    st.write("#### Компоненты топлива:")
                    for c_name, sh in shares.items():
                        st.write(f"• **{c_name}**: {sh * 100.0:.1f}%")
                doses = getattr(blending_recipe, "additive_doses_kg_t", {})
                if doses:
                    st.write("#### Дозировка присадок:")
                    for a_name, d_val in doses.items():
                        st.write(f"• **{a_name}**: {d_val:.2f} кг/т")

    with col_action:
        st.write("### 🎮 Решение Диспетчера (HITL)")

        # Ручное вмешательство (ISA-18.2 Override)
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
            if selected_cand and selected_cand.delta_u:
                # Фиксация уставок в двойнике
                TWIN_STORE.commit_applied_move(st.session_state.session_id, selected_cand.delta_u)
            with st.spinner("Безударная передача уставок в контроллеры DCS/APC..."):
                time.sleep(1)
            st.session_state.last_applied_time = datetime.datetime.now().strftime("%H:%M:%S")
            st.success(f"Уставки успешно переданы на нижний уровень в {st.session_state.last_applied_time}!")
            st.balloons()

        if st.button("🔴 ОТКЛОНИТЬ (Остаться на базе)", width="stretch"):
            st.warning("Рекомендация ИИ отклонена диспетчером. Технологический режим не изменен.")

# -----------------------------------------------------------------------------
# Парето-фронт: маржа ↔ качество ↔ износ катализатора (Критерий 4, п. 6.5 ТЗ)
# -----------------------------------------------------------------------------
pareto_analysis = graph_result.get("pareto")
if pareto_analysis is not None and pareto_analysis.points:
    st.divider()
    st.subheader("🎯 Парето-фронт допустимых режимов")
    selected_id = selected_cand.candidate_id if selected_cand else None
    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("Допустимых кандидатов", f"{pareto_analysis.n_admissible} из {len(pareto_analysis.points)}")
    pc2.metric("На Парето-фронте", len(pareto_analysis.front_ids))
    pc3.metric("Отклонено вето ПАЗ/ГОСТ", sum(1 for p in pareto_analysis.points if p.status == "vetoed"))
    if selected_id and pareto_analysis.is_on_front(selected_id):
        st.markdown(
            f"Рекомендация `{selected_id}` — **Парето-оптимальное (компромиссное) решение в допустимой зоне**: "
            "максимум маржи среди безопасных режимов, которые не доминируются другими."
        )
    reference_id = selected_id or next((p.candidate_id for p in pareto_analysis.points if p.is_hold), None)
    trade_offs = trade_off_alternatives(pareto_analysis, reference_id)
    if trade_offs:
        st.markdown("**Цена компромисса — ближайшие альтернативы на фронте:**")
        st.dataframe(pd.DataFrame([
            {
                "Альтернатива": "Безопаснее по сере" if a.kind == "safer_sulfur" else "Бережнее к катализатору",
                "Кандидат": a.candidate_id,
                "Δ маржи, руб/ч": a.delta_margin_rub_h,
                "Δ серы, ppm": a.delta_sulfur_ppm,
                "Риск P(S>10), %": f"{a.risk_from_pct:.2f} → {a.risk_to_pct:.2f}" if a.risk_from_pct is not None else None,
                "Δ WABT, °C": a.delta_wabt_c,
            }
            for a in trade_offs
        ]), width="stretch", hide_index=True)

    if pareto_analysis.n_admissible == 0:
        st.warning("Все кандидаты отклонены вето ПАЗ/ГОСТ: Парето-фронт пуст, компенсация риска экономикой запрещена.")

    tab_3d, tab_2d, tab_pc, tab_table = st.tabs(["3D-фронт", "2D-проекция", "Все метрики", "Таблица"])
    with tab_3d:
        st.plotly_chart(build_pareto_3d_figure(pareto_analysis, selected_id), width="stretch")
    with tab_2d:
        metric_labels = {m.key: f"{m.label}, {m.unit}" for m in pareto_analysis.metrics}
        keys = list(metric_labels)
        ax1, ax2 = st.columns(2)
        x_key = ax1.selectbox("Ось X", keys, index=keys.index("sulfur_giveaway"), format_func=metric_labels.get, key="pareto_x")
        y_key = ax2.selectbox("Ось Y", keys, index=keys.index("net_margin"), format_func=metric_labels.get, key="pareto_y")
        st.plotly_chart(build_pareto_2d_figure(pareto_analysis, selected_id, x=x_key, y=y_key), width="stretch")
    with tab_pc:
        st.plotly_chart(build_parallel_coordinates_figure(pareto_analysis, selected_id), width="stretch")
    with tab_table:
        status_names = {"pareto": "Фронт", "dominated": "Доминируемый", "vetoed": "Вето", "incomplete": "Нет данных"}
        rows = []
        for p in pareto_analysis.points:
            rows.append({
                "ID": p.candidate_id,
                "Статус": status_names.get(p.status, p.status),
                "Рекомендация": "⭐" if p.candidate_id == selected_id else "",
                **{metric_labels[k]: v for k, v in p.metrics.items()},
                "P(S>10), %": round(p.p_offspec * 100.0, 2) if p.p_offspec is not None else None,
                "Доминируется / причина": ", ".join(p.dominated_by) or "; ".join(p.veto_reasons),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(
        "Доминирование считается только среди допустимых кандидатов по целям: "
        + ", ".join(f"{'↑' if o.sense == 'max' else '↓'} {o.label}" for o in pareto_analysis.objectives)
        + f". Запас вето по сере 2σ_S = {pareto_analysis.sulfur_offset_ppm:.2f} ppm; переочистка — прогноз серы ниже "
        f"{pareto_analysis.giveaway_boundary_ppm:.2f} ppm (возраст измерения {pareto_analysis.sulfur_age_hours:.1f} ч)."
    )

# -----------------------------------------------------------------------------
# LLM-Супервизор: Асинхронный советник и диагност (Этап P4)
# -----------------------------------------------------------------------------
st.divider()
st.header("🤖 Асинхронный LLM-супервизор (Qwen 27B / Claude)")

st.info(
    "ℹ️ **ИИ-ассистент: рекомендательный статус, не является уставками** (advisory only, no setpoints). "
    "Агент работает вне цикла оптимизации, анализирует трассы решений и заземляет все выводы на факты из EvidenceRef."
)

from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
from src.supervisor.service import DEFAULT_SUPERVISOR_SERVICE

sup_tab_qa, sup_tab_briefing, sup_tab_findings, sup_tab_policy = st.tabs([
    "💬 Вопрос оператора (Q&A)",
    "📋 Сводка смены (Briefing)",
    "🔍 Диагностические находки (Findings)",
    "⚙️ Запросы на изменение политики (HITL)",
])

with sup_tab_qa:
    st.subheader("Консультация сменного инженера-технолога")
    user_q = st.text_input(
        "Задайте вопрос по поведению автоматики или ограничениям:",
        value="Почему не поднимается загрузка сырья?",
        key="supervisor_user_question",
    )
    if st.button("🔎 Получить ответ супервизора", type="primary", key="btn_ask_supervisor"):
        with st.spinner("Анализ трассы решения и проверка заземления..."):
            ans = DEFAULT_SUPERVISOR_SERVICE.answer_operator(user_q)
        if ans:
            st.success(f"**Ответ:** {ans.direct_answer}")
            st.markdown(f"**Техническое обоснование:** {ans.technical_explanation}")
            if ans.active_constraints_involved:
                st.markdown(f"**Задействованные ограничения:** `{', '.join(ans.active_constraints_involved)}`")
            st.info(f"**Рекомендация оператору:** {ans.operator_guidance}")
            if ans.evidence_refs:
                st.caption(f"Доказательства (EvidenceRef): {', '.join(ans.evidence_refs)}")

with sup_tab_briefing:
    st.subheader("Сводка передачи технологической смены")
    if st.button("🔄 Сформировать свежую сводку", key="btn_gen_briefing"):
        with st.spinner("Сборка пакета смены и структурированный синтез..."):
            brief = DEFAULT_SUPERVISOR_SERVICE.generate_shift_briefing()
            if brief:
                st.toast("Сводка смены успешно сформирована!")

    briefings = DEFAULT_SUPERVISOR_STORE.list_briefings(limit=5)
    if briefings:
        latest_b = briefings[0].briefing
        st.markdown(f"### {latest_b.shift_period} (`{latest_b.briefing_id}`)")
        st.markdown(f"**Резюме:** {latest_b.summary_text}")
        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("Успешных тактов", latest_b.status_counts.get("SUCCESS", 0))
        bc2.metric("В зоне нечувствительности", latest_b.status_counts.get("DEADBAND", 0))
        bc3.metric("Отказов", latest_b.status_counts.get("REFUSAL", 0))

        st.markdown(f"**Анализ качества ГОСТ:** {latest_b.quality_assessment}")
        st.markdown(f"**Анализ безопасности ПАЗ:** {latest_b.safety_assessment}")

        if latest_b.open_concerns:
            st.warning("**Факторы внимания:**\n- " + "\n- ".join(latest_b.open_concerns))
        if latest_b.incoming_recommendations:
            st.info("**Рекомендации заступающей смене:**\n- " + "\n- ".join(latest_b.incoming_recommendations))
    else:
        st.write("Сводки смен пока не сформированы.")

with sup_tab_findings:
    st.subheader("Журнал технологических находок и аномалий")
    findings = DEFAULT_SUPERVISOR_STORE.list_findings()
    if findings:
        for f in findings:
            badge = "🔴" if f.severity == "CRITICAL" else ("🟡" if f.severity == "WARNING" else "🔵")
            with st.expander(f"{badge} [{f.category}] {f.title} ({f.finding_id}) - Статус: {f.status}"):
                st.markdown(f"**Первопричина:** {f.root_cause}")
                st.markdown(f"**Оценка риска:** {f.safety_risk_assessment}")
                if f.checks_recommended:
                    st.markdown("**Рекомендуемые проверки:**\n- " + "\n- ".join(f.checks_recommended))
                if f.evidence_refs:
                    st.caption(f"Ссылки: {', '.join(f.evidence_refs)}")
    else:
        st.success("Открытых диагностических инцидентов и аномалий не зафиксировано.")

with sup_tab_policy:
    st.subheader("Запросы на изменение технологической политики (HITL)")
    reqs = DEFAULT_SUPERVISOR_STORE.list_change_requests()
    if reqs:
        for req in reqs:
            with st.expander(f"Запрос {req.request_id} — Статус: {req.status}"):
                st.markdown(f"**Прогноз эффекта:** {req.proposal.expected_kpi_impact}")
                st.markdown(f"**Теневой реплей (Shadow Replay):** {'✅ Пройден' if req.shadow_passed else '❌ Не пройден'}")
                for it in req.proposal.items:
                    st.markdown(f"- **{it.field}**: `{it.old_value}` → `{it.new_value}` (*{it.justification}*)")

                if req.status == "PENDING_APPROVAL":
                    c_app, c_rej = st.columns(2)
                    if c_app.button("✅ Утвердить изменение", key=f"app_{req.request_id}", type="primary"):
                        DEFAULT_SUPERVISOR_STORE.approve_change_request(req.request_id, approved_by="Главный технолог")
                        st.success("Политика успешно обновлена и активирована в контуре!")
                        st.rerun()
                    if c_rej.button("❌ Отклонить", key=f"rej_{req.request_id}"):
                        DEFAULT_SUPERVISOR_STORE.reject_change_request(req.request_id, rejected_by="Главный технолог")
                        st.warning("Запрос на изменение отклонен.")
                        st.rerun()
    else:
        st.write("Нет активных запросов на изменение технологической политики.")
