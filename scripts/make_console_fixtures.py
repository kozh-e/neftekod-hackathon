"""Генератор фикстур для пульта старшего оператора.

Создает эталонные JSON-файлы:
- tests/fixtures/console/advisory.json
- tests/fixtures/console/auto.json
- tests/fixtures/console/refusal.json
- tests/fixtures/console/preview_ok.json
- tests/fixtures/console/preview_blocked.json
и дублирует их в static/console/fixtures/.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from src.console.contracts import (
    AgentVote,
    Alarm,
    Alternative,
    AutoInfo,
    AutoNextStep,
    AutoPlanStep,
    Band,
    Banner,
    Change,
    Chip,
    ClockInfo,
    ConsoleState,
    Corridor,
    CorridorViolation,
    CvSeries,
    Effect,
    FeedItem,
    KernelCheckDTO,
    KernelInfo,
    LastAutoExit,
    Limit,
    Margin,
    ModeInfo,
    MvLine,
    MvSeries,
    Point,
    PreviewResult,
    Recommendation,
    Refusal,
    Risk,
    Scenario,
    Series,
    Trajectory,
)

BASE_T = datetime(2026, 9, 19, 12, 40, 0, tzinfo=timezone.utc)


def gen_time_series(base_t: datetime, n_pts: int, dt_min: int, start_val: float, end_val: float, quality: str = "GOOD"):
    points = []
    start_t = base_t - timedelta(minutes=(n_pts - 1) * dt_min)
    for i in range(n_pts):
        t_i = start_t + timedelta(minutes=i * dt_min)
        frac = i / max(1, n_pts - 1)
        val = start_val + (end_val - start_val) * frac
        points.append(Point(t=t_i.strftime("%Y-%m-%dT%H:%M:%SZ"), v=round(val, 3), quality=quality))
    return points


def gen_traj(base_t: datetime, kind: str, s_end: float, flash_end: float, dp_end: float, u_target: dict, risk_prob: float = 0.01, breach_at: str | None = None):
    # 25 points from now to now + 4h
    sulfur_bands = []
    flash_bands = []
    dp_bands = []
    mv_plan = {k: [] for k in u_target}

    for i in range(25):
        t_i = (base_t + timedelta(minutes=i * 10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        frac = i / 24.0
        
        # sulfur
        s_p50 = 9.2 + (s_end - 9.2) * frac
        s_p10 = round(s_p50 * 0.92, 3)
        s_p90 = round(s_p50 * 1.08, 3)
        sulfur_bands.append(Band(t=t_i, p10=s_p10, p50=round(s_p50, 3), p90=s_p90))

        # flash
        f_p50 = 61.5 + (flash_end - 61.5) * frac
        f_p10 = round(f_p50 - 2.5, 2)
        f_p90 = round(f_p50 + 2.5, 2)
        flash_bands.append(Band(t=t_i, p10=f_p10, p50=round(f_p50, 2), p90=f_p90))

        # dp
        dp_p50 = 177.0 + (dp_end - 177.0) * frac
        dp_bands.append(Band(t=t_i, p10=None, p50=round(dp_p50, 1), p90=None))

        # mv plan
        for k, v in u_target.items():
            mv_plan[k].append(Point(t=t_i, v=v, quality="GOOD"))

    risks = [
        Risk(cv="sulfur", probability=risk_prob, first_breach_at=breach_at, limit=10.0),
        Risk(cv="flash", probability=0.001, first_breach_at=None, limit=55.0),
        Risk(cv="dp", probability=0.001, first_breach_at=None, limit=454.5),
    ]

    return Trajectory(
        kind=kind,
        u_target=u_target,
        cv={"sulfur": sulfur_bands, "flash": flash_bands, "dp": dp_bands},
        mv_plan=mv_plan,
        risk=risks,
        sigma_source="estimation.py:Q_DRIFT_LOG",
    )


def make_corridors():
    return {
        "HT_FEED_SP": Corridor(max_step_per_tick=10.0, lo=158.7, hi=252.8, source="registry.py:RATE.HT_FEED_SP.MAX"),
        "HT_TIN_SP": Corridor(max_step_per_tick=3.0, lo=346.5, hi=380.5, source="registry.py:RATE.HT_TIN_SP.MAX"),
        "HT_P_SP": Corridor(max_step_per_tick=None, lo=3.755, hi=4.009, source="registry.py:MV.HT_P_SP.MIN"),
        "HT_GOR_SP": Corridor(max_step_per_tick=None, lo=313.0, hi=490.0, source="registry.py:MV.HT_GOR_SP.MIN"),
        "AVT_T55_SP": Corridor(max_step_per_tick=3.0, lo=375.0, hi=395.0, source="registry.py:RATE.AVT_T55_SP.MAX"),
    }


def make_mv_series(u_current: dict, frozen: bool = False):
    corrs = make_corridors()
    lines_map = {
        "HT_FEED_SP": [MvLine(value=158.7, kind="T0", label="T0 min"), MvLine(value=252.8, kind="T0", label="T0 max")],
        "HT_TIN_SP": [MvLine(value=346.5, kind="T0", label="T0 min"), MvLine(value=380.5, kind="T0", label="T0 max")],
        "HT_P_SP": [MvLine(value=3.755, kind="T0", label="T0 min"), MvLine(value=4.009, kind="T0", label="T0 max")],
        "HT_GOR_SP": [MvLine(value=313.0, kind="T0", label="T0 min"), MvLine(value=490.0, kind="T0", label="T0 max")],
        "AVT_T55_SP": [
            MvLine(value=375.0, kind="T0", label="T0 min"),
            MvLine(value=380.0, kind="warn", label="Зона предупр."),
            MvLine(value=386.4, kind="T1", label="T1 макс"),
            MvLine(value=395.0, kind="T0", label="T0 ПАЗ"),
        ],
    }
    meta = {
        "HT_FEED_SP": ("HT_F9", "Сырьё ГО", "т/ч", 1),
        "HT_TIN_SP": ("HT_T6", "Вход Р-202", "°C", 1),
        "HT_P_SP": ("HT_P13", "Давление", "МПа", 3),
        "HT_GOR_SP": ("HT_GOR", "Кратность ВСГ", "нм³/м³", 0),
        "AVT_T55_SP": ("AVT_T55", "Перевал П-3", "°C", 1),
    }

    mv_list = []
    for sp, (tag, title, unit, decimals) in meta.items():
        val = u_current[sp]
        sp_pts = gen_time_series(BASE_T, 73, 10, val, val)
        pv_pts = gen_time_series(BASE_T, 73, 10, val * 0.998, val * 1.002)
        mv_list.append(
            MvSeries(
                sp=sp,
                tag=tag,
                title=title,
                unit=unit,
                decimals=decimals,
                sp_history=sp_pts,
                pv_history=pv_pts,
                corridor=corrs[sp],
                lines=lines_map[sp],
                frozen=frozen,
            )
        )
    return mv_list


def build_advisory_fixture() -> ConsoleState:
    u_current = {"HT_FEED_SP": 219.6, "HT_TIN_SP": 363.3, "HT_P_SP": 3.922, "HT_GOR_SP": 360.0, "AVT_T55_SP": 381.7}
    u_rec = {"HT_FEED_SP": 219.6, "HT_TIN_SP": 365.0, "HT_P_SP": 3.922, "HT_GOR_SP": 380.0, "AVT_T55_SP": 381.7}

    # CV series
    s_hist = gen_time_series(BASE_T, 73, 10, 7.8, 9.2)
    f_hist = gen_time_series(BASE_T, 73, 10, 62.0, 61.5)
    dp_hist = gen_time_series(BASE_T, 73, 10, 175.0, 177.0)

    cv_series = [
        CvSeries(
            key="sulfur",
            tag="HT_Q21",
            title="СЕРА",
            unit="ppm",
            limit=Limit(value=10.0, sense="max", label="ГОСТ ≤ 10.0 ppm", tier="T2", source="registry.py:GODT.S_MAX"),
            history=s_hist,
            chip=Chip(text="растёт +0.5 ppm/ч", severity="warn"),
            note="без изменений пробьёт 10 ppm через 1 ч 50 мин",
            y_range=[5.0, 13.0],
        ),
        CvSeries(
            key="flash",
            tag="HT_FLASH",
            title="ВСПЫШКА",
            unit="°C",
            limit=Limit(value=55.0, sense="min", label="ГОСТ ≥ 55.0 °C", tier="T2", source="registry.py:GODT.FLASH_MIN"),
            history=f_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[50.0, 70.0],
        ),
        CvSeries(
            key="dp",
            tag="HT_P8",
            title="ПЕРЕПАД Р-202",
            unit="кПа",
            limit=Limit(value=454.5, sense="max", label="T1 ≤ 454.5 кПа", tier="T1", source="registry.py:RX.DP_MAX"),
            history=dp_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[100.0, 500.0],
        ),
    ]

    hold_traj = gen_traj(BASE_T, "hold", 10.8, 61.0, 178.0, u_current, risk_prob=0.92, breach_at="2026-09-19T14:30:00Z")
    rec_traj = gen_traj(BASE_T, "recommendation", 8.9, 61.2, 179.0, u_rec, risk_prob=0.008, breach_at=None)

    # 3 alternatives
    alt_max_margin = Alternative(
        choice="max_margin",
        label="Макс. маржа",
        available=True,
        changes=[
            Change(sp="HT_FEED_SP", tag="HT_F9", title="Сырьё ГО", unit="т/ч", current=219.6, target=226.0, delta=6.4, decimals=1),
            Change(sp="HT_TIN_SP", tag="HT_T6", title="Вход Р-202", unit="°C", current=363.3, target=365.8, delta=2.5, decimals=1),
        ],
        effect=Effect(sulfur_4h=9.4, flash_margin_c=4.2, margin_delta_rub_h=42000.0),
        trajectory=gen_traj(BASE_T, "alternative", 9.4, 60.8, 184.0, {"HT_FEED_SP": 226.0, "HT_TIN_SP": 365.8, "HT_P_SP": 3.922, "HT_GOR_SP": 360.0, "AVT_T55_SP": 381.7}, risk_prob=0.04),
    )
    alt_balanced = Alternative(
        choice="balanced",
        label="Сбалансировано",
        available=True,
        changes=[
            Change(sp="HT_TIN_SP", tag="HT_T6", title="Вход Р-202", unit="°C", current=363.3, target=365.0, delta=1.7, decimals=1),
            Change(sp="HT_GOR_SP", tag="HT_GOR", title="Кратность ВСГ", unit="нм³/м³", current=360.0, target=380.0, delta=20.0, decimals=0),
        ],
        effect=Effect(sulfur_4h=8.9, flash_margin_c=4.9, margin_delta_rub_h=-9000.0),
        trajectory=rec_traj,
    )
    alt_max_safety = Alternative(
        choice="max_safety",
        label="Макс. запас",
        available=True,
        changes=[
            Change(sp="HT_TIN_SP", tag="HT_T6", title="Вход Р-202", unit="°C", current=363.3, target=366.2, delta=2.9, decimals=1),
            Change(sp="HT_GOR_SP", tag="HT_GOR", title="Кратность ВСГ", unit="нм³/м³", current=360.0, target=400.0, delta=40.0, decimals=0),
        ],
        effect=Effect(sulfur_4h=8.3, flash_margin_c=5.5, margin_delta_rub_h=-24000.0),
        trajectory=gen_traj(BASE_T, "alternative", 8.3, 61.5, 180.0, {"HT_FEED_SP": 219.6, "HT_TIN_SP": 366.2, "HT_P_SP": 3.922, "HT_GOR_SP": 400.0, "AVT_T55_SP": 381.7}, risk_prob=0.001),
    )

    feed = [
        FeedItem(id="s2:1", t="12:40", kind="shield", text="Ядро безопасности ограничило шаг T6: +2.5 → +1.7 °C по теплонапряжению", source="kernel"),
        FeedItem(id="s2:2", t="12:40", kind="info", text="Сформирована рекомендация по возврату серы (цикл s2_cycle_48)", source="system"),
        FeedItem(id="s2:3", t="12:30", kind="warn", text="Детекция утяжеления сырья: T95 выросла на 2.4 °C", source="DataGuard"),
        FeedItem(id="s2:4", t="12:20", kind="ok", text="Оператор применил: GOR 350 -> 360 нм³/м³", source="operator"),
        FeedItem(id="s2:5", t="12:00", kind="info", text="Плановый цикл оптимизации без замечаний", source="system"),
    ]

    rec = Recommendation(
        cycle_id="s2_cycle_48",
        status="SUCCESS",
        created_at="2026-09-19T12:40:00Z",
        valid_until="2026-09-19T13:10:00Z",
        narrative="Сера растёт из-за утяжеления сырья (+0.5 ppm/ч). Поднимаем температуру входа Р-202 и кратность ВСГ, чтобы удержать серу ≤ 10 ppm.",
        changes=[
            Change(sp="HT_TIN_SP", tag="HT_T6", title="Вход Р-202", unit="°C", current=363.3, target=365.0, delta=1.7, decimals=1),
            Change(sp="HT_GOR_SP", tag="HT_GOR", title="Кратность ВСГ", unit="нм³/м³", current=360.0, target=380.0, delta=20.0, decimals=0),
        ],
        effect=Effect(sulfur_4h=8.9, flash_margin_c=4.9, margin_delta_rub_h=-9000.0),
        kernel=KernelInfo(
            passed=True,
            checks=[
                KernelCheckDTO(name="T0_BOUNDS", passed=True, detail="В допустимых диапазонах"),
                KernelCheckDTO(name="T1_EQUIPMENT", passed=True, detail="Запас по оборудованию соблюден"),
                KernelCheckDTO(name="T2_QUALITY", passed=True, detail="Прогноз серы 8.9 <= 10.0 ppm"),
            ],
            limited=[],
        ),
        agents=[
            AgentVote(agent="optimization", stance="for", text="Режим экономически обоснован с учетом удержания ГОСТ"),
            AgentVote(agent="reliability", stance="limit", text="Шаг по температуре ограничен до +1.7 °C"),
            AgentVote(agent="quality", stance="for", text="Гарантирует серу <= 10.0 ppm при p90 = 9.6 ppm"),
            AgentVote(agent="kernel", stance="for", text="Все проверки безопасности пройдены"),
        ],
        trajectory=rec_traj,
        alternatives=[alt_max_margin, alt_balanced, alt_max_safety],
        refusal=None,
    )

    return ConsoleState(
        _fixture=True,
        schema_version="1.0",
        session_id="demo",
        scenario=Scenario(id="S2", title="Риск качества: утяжеление сырья"),
        clock=ClockInfo(now="2026-09-19T12:40:00Z", tick=48, tick_minutes=10, seconds_per_tick=10.0, next_tick_in_s=7.5),
        mode=ModeInfo(current="ADVISORY", auto_available=True, auto_unavailable_reason=None, auto_since=None, last_auto_exit=None),
        margin=Margin(value_rub_h=1240000.0, delta_rub_h=-9000.0, delta_label="цена удержания ГОСТ"),
        alarms=[],
        banner=None,
        series=Series(cv=cv_series, mv=make_mv_series(u_current)),
        hold=hold_traj,
        recommendation=rec,
        auto=None,
        feed=feed,
    )


def build_auto_fixture() -> ConsoleState:
    u_current = {"HT_FEED_SP": 231.0, "HT_TIN_SP": 364.5, "HT_P_SP": 3.922, "HT_GOR_SP": 360.0, "AVT_T55_SP": 381.7}
    u_target = {"HT_FEED_SP": 236.0, "HT_TIN_SP": 364.5, "HT_P_SP": 3.922, "HT_GOR_SP": 360.0, "AVT_T55_SP": 381.7}

    s_hist = gen_time_series(BASE_T, 73, 10, 8.5, 8.8)
    f_hist = gen_time_series(BASE_T, 73, 10, 62.2, 61.8)
    dp_hist = gen_time_series(BASE_T, 73, 10, 180.0, 182.0)

    cv_series = [
        CvSeries(
            key="sulfur",
            tag="HT_Q21",
            title="СЕРА",
            unit="ppm",
            limit=Limit(value=10.0, sense="max", label="ГОСТ ≤ 10.0 ppm", tier="T2", source="registry.py:GODT.S_MAX"),
            history=s_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[5.0, 13.0],
        ),
        CvSeries(
            key="flash",
            tag="HT_FLASH",
            title="ВСПЫШКА",
            unit="°C",
            limit=Limit(value=55.0, sense="min", label="ГОСТ ≥ 55.0 °C", tier="T2", source="registry.py:GODT.FLASH_MIN"),
            history=f_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[50.0, 70.0],
        ),
        CvSeries(
            key="dp",
            tag="HT_P8",
            title="ПЕРЕПАД Р-202",
            unit="кПа",
            limit=Limit(value=454.5, sense="max", label="T1 ≤ 454.5 кПа", tier="T1", source="registry.py:RX.DP_MAX"),
            history=dp_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[100.0, 500.0],
        ),
    ]

    hold_traj = gen_traj(BASE_T, "hold", 8.8, 61.8, 182.0, u_current, risk_prob=0.005)
    plan_traj = gen_traj(BASE_T, "auto_plan", 9.1, 61.4, 186.0, u_target, risk_prob=0.01)

    corrs = make_corridors()
    auto_info = AutoInfo(
        next_step=AutoNextStep(
            at="2026-09-19T12:42:30Z",
            in_s=150.0,
            changes=[
                Change(sp="HT_FEED_SP", tag="HT_F9", title="Сырьё ГО", unit="т/ч", current=231.0, target=236.0, delta=5.0, decimals=1)
            ],
            narrative="Идём к режиму «Сбалансировано»: +38 тыс ₽/ч при прогнозе серы ≤ 9.1 ppm и запасе по вспышке +4.9 °C.",
        ),
        plan=[
            AutoPlanStep(
                at="2026-09-19T12:42:30Z",
                changes=[Change(sp="HT_FEED_SP", tag="HT_F9", title="Сырьё ГО", unit="т/ч", current=231.0, target=236.0, delta=5.0, decimals=1)],
            ),
            AutoPlanStep(
                at="2026-09-19T12:52:30Z",
                changes=[Change(sp="HT_FEED_SP", tag="HT_F9", title="Сырьё ГО", unit="т/ч", current=236.0, target=240.0, delta=4.0, decimals=1)],
            ),
        ],
        corridor=corrs,
        skipped_next=False,
    )

    feed = [
        FeedItem(id="s1:1", t="12:40", kind="ok", text="Автомат: F9 226.0 -> 231.0 т/ч. Сера в норме 8.8 ppm", source="auto"),
        FeedItem(id="s1:2", t="12:30", kind="shield", text="Коридор ограничил шаг F9 до +5.0 т/ч/такт", source="corridor"),
        FeedItem(id="s1:3", t="12:20", kind="veto", text="Агент надежности отклонил превышение температуры П-3", source="reliability"),
        FeedItem(id="s1:4", t="12:10", kind="ok", text="Автомат включен старшим оператором. Цель: «Сбалансировано»", source="operator"),
    ]

    return ConsoleState(
        _fixture=True,
        schema_version="1.0",
        session_id="demo",
        scenario=Scenario(id="S1", title="Нормальный режим: выход на оптимум"),
        clock=ClockInfo(now="2026-09-19T12:40:00Z", tick=48, tick_minutes=10, seconds_per_tick=10.0, next_tick_in_s=2.5),
        mode=ModeInfo(current="AUTO", auto_available=True, auto_unavailable_reason=None, auto_since="2026-09-19T12:10:00Z", last_auto_exit=None),
        margin=Margin(value_rub_h=1320000.0, delta_rub_h=38000.0, delta_label="рост к оптимуму"),
        alarms=[],
        banner=None,
        series=Series(cv=cv_series, mv=make_mv_series(u_current)),
        hold=hold_traj,
        recommendation=None,
        auto=auto_info,
        feed=feed,
    )


def build_refusal_fixture() -> ConsoleState:
    u_current = {"HT_FEED_SP": 219.6, "HT_TIN_SP": 363.3, "HT_P_SP": 3.922, "HT_GOR_SP": 360.0, "AVT_T55_SP": 381.7}

    # 73 points: last 7 points v=None, quality=MISSING
    s_hist = []
    for i in range(73):
        t_i = (BASE_T - timedelta(minutes=(72 - i) * 10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if i >= 66:  # last 7
            s_hist.append(Point(t=t_i, v=None, quality="MISSING"))
        else:
            s_hist.append(Point(t=t_i, v=8.4, quality="GOOD"))

    f_hist = gen_time_series(BASE_T, 73, 10, 61.5, 61.5)
    dp_hist = gen_time_series(BASE_T, 73, 10, 177.0, 177.0)

    cv_series = [
        CvSeries(
            key="sulfur",
            tag="HT_Q21",
            title="СЕРА",
            unit="ppm",
            limit=Limit(value=10.0, sense="max", label="ГОСТ ≤ 10.0 ppm", tier="T2", source="registry.py:GODT.S_MAX"),
            history=s_hist,
            chip=Chip(text="нет сигнала", severity="nodata"),
            note=None,
            y_range=[5.0, 13.0],
        ),
        CvSeries(
            key="flash",
            tag="HT_FLASH",
            title="ВСПЫШКА",
            unit="°C",
            limit=Limit(value=55.0, sense="min", label="ГОСТ ≥ 55.0 °C", tier="T2", source="registry.py:GODT.FLASH_MIN"),
            history=f_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[50.0, 70.0],
        ),
        CvSeries(
            key="dp",
            tag="HT_P8",
            title="ПЕРЕПАД Р-202",
            unit="кПа",
            limit=Limit(value=454.5, sense="max", label="T1 ≤ 454.5 кПа", tier="T1", source="registry.py:RX.DP_MAX"),
            history=dp_hist,
            chip=Chip(text="в норме", severity="ok"),
            note=None,
            y_range=[100.0, 500.0],
        ),
    ]

    hold_traj = gen_traj(BASE_T, "hold", 8.4, 61.5, 177.0, u_current, risk_prob=0.0)

    rec = Recommendation(
        cycle_id="s3d_cycle_48",
        status="REFUSAL_DATA",
        created_at="2026-09-19T12:40:00Z",
        valid_until="2026-09-19T13:10:00Z",
        narrative="Отказ от оптимизации: отсутствуют достоверные данные по сере.",
        changes=[],
        effect=Effect(sulfur_4h=None, flash_margin_c=None, margin_delta_rub_h=0.0),
        kernel=KernelInfo(
            passed=False,
            checks=[KernelCheckDTO(name="DATA_CONFIDENCE", passed=False, detail="Недостоверные данные по сере (HT_Q21 MISSING, LIMS > 24 ч)")],
            limited=[],
        ),
        agents=[
            AgentVote(agent="quality", stance="veto", text="Анализатор HT_Q21 недоступен, анализ LIMS устарел (26 ч)"),
            AgentVote(agent="kernel", stance="veto", text="Отказ ядра: перевод в safe-hold"),
        ],
        trajectory=None,
        alternatives=[],
        refusal=Refusal(
            reason_code="REFUSAL_DATA",
            text="Система не может оценить серу: онлайн-анализатор HT_Q21 не отвечает, а последний лабораторный анализ старше 24 ч. Уставки зафиксированы на значениях 12:38.",
            checklist=[
                "ПАК HT_Q21: питание, пробоотборная линия, связь с ПЛК",
                "Заказать внеочередной анализ LIMS на серу (гидрогенизат)",
                "Не повышать загрузку HT_F9 до восстановления данных",
            ],
            auto_resume_condition="ПАК HT_Q21 вернётся в норму или придёт анализ LIMS моложе 24 ч",
        ),
    )

    banner = Banner(
        id="b1",
        kind="AUTO_EXIT",
        title="АВТОМАТ ОТКЛЮЧЁН в 12:38",
        text="Нет достоверных данных по сере: ПАК HT_Q21 без сигнала с 11:50, последний анализ LIMS 26 ч назад. Уставки заморожены.",
        at="2026-09-19T12:38:00Z",
        ack_required=True,
    )

    feed = [
        FeedItem(id="s3d:1", t="12:38", kind="warn", text="Автовыход: отказ данных по сере (REFUSAL_DATA)", source="auto"),
        FeedItem(id="s3d:2", t="12:38", kind="shield", text="Уставки зафиксированы в safe-hold", source="kernel"),
        FeedItem(id="s3d:3", t="11:50", kind="warn", text="Потеря сигнала ПАК HT_Q21 (NaN)", source="DataGuard"),
    ]

    return ConsoleState(
        _fixture=True,
        schema_version="1.0",
        session_id="demo",
        scenario=Scenario(id="S3d", title="Отказ данных: потеря сигнала серы"),
        clock=ClockInfo(now="2026-09-19T12:40:00Z", tick=48, tick_minutes=10, seconds_per_tick=10.0, next_tick_in_s=None),
        mode=ModeInfo(
            current="ADVISORY",
            auto_available=False,
            auto_unavailable_reason="Нет достоверных данных по сере (анализатор не отвечает, LIMS > 24 ч)",
            auto_since=None,
            last_auto_exit=LastAutoExit(
                at="2026-09-19T12:38:00Z",
                reason_code="REFUSAL_DATA",
                text="Нет достоверных данных по сере: ПАК HT_Q21 без сигнала с 11:50, последний анализ LIMS 26 ч назад. Уставки заморожены.",
            ),
        ),
        margin=Margin(value_rub_h=1210000.0, delta_rub_h=0.0, delta_label="уставки зафиксированы"),
        alarms=[],
        banner=banner,
        series=Series(cv=cv_series, mv=make_mv_series(u_current, frozen=True)),
        hold=hold_traj,
        recommendation=rec,
        auto=None,
        feed=feed,
    )


def build_preview_ok_fixture() -> PreviewResult:
    u_edit = {"HT_FEED_SP": 219.6, "HT_TIN_SP": 364.5, "HT_P_SP": 3.922, "HT_GOR_SP": 380.0, "AVT_T55_SP": 381.7}
    traj = gen_traj(BASE_T, "operator", 9.0, 61.1, 179.0, u_edit, risk_prob=0.12, breach_at="2026-09-19T15:10:00Z")
    return PreviewResult(
        trajectory=traj,
        kernel=KernelInfo(
            passed=True,
            checks=[
                KernelCheckDTO(name="T0_BOUNDS", passed=True, detail="В границах"),
                KernelCheckDTO(name="T1_EQUIPMENT", passed=True, detail="В пределах"),
                KernelCheckDTO(name="T2_QUALITY", passed=True, detail="Сера <= 10.0 ppm"),
            ],
            limited=[],
        ),
        corridor_ok=True,
        corridor_violations=[],
        effect=Effect(sulfur_4h=9.0, flash_margin_c=4.8, margin_delta_rub_h=-6000.0),
        can_commit=True,
        blocking_reason=None,
        compute_ms=45.2,
    )


def build_preview_blocked_fixture() -> PreviewResult:
    u_blocked = {"HT_FEED_SP": 219.6, "HT_TIN_SP": 367.5, "HT_P_SP": 3.922, "HT_GOR_SP": 380.0, "AVT_T55_SP": 381.7}
    traj = gen_traj(BASE_T, "operator", 8.2, 61.1, 179.0, u_blocked, risk_prob=0.0)
    return PreviewResult(
        trajectory=traj,
        kernel=KernelInfo(
            passed=True,
            checks=[KernelCheckDTO(name="T0_BOUNDS", passed=True, detail="В границах")],
            limited=[],
        ),
        corridor_ok=False,
        corridor_violations=[
            CorridorViolation(
                sp="HT_TIN_SP",
                requested_step=4.2,
                max_step=3.0,
                text="Шаг изменения HT_TIN_SP 4.2 °C превышает максимальный 3.0 °C/такт",
            )
        ],
        effect=Effect(sulfur_4h=8.2, flash_margin_c=4.8, margin_delta_rub_h=-12000.0),
        can_commit=False,
        blocking_reason="Шаг HT_TIN_SP превышает допустимый коридор",
        compute_ms=38.4,
    )


def main():
    test_dir = Path("tests/fixtures/console")
    static_dir = Path("static/console/fixtures")
    test_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

    fixtures = {
        "advisory.json": build_advisory_fixture().model_dump(mode="json"),
        "auto.json": build_auto_fixture().model_dump(mode="json"),
        "refusal.json": build_refusal_fixture().model_dump(mode="json"),
        "preview_ok.json": build_preview_ok_fixture().model_dump(mode="json"),
        "preview_blocked.json": build_preview_blocked_fixture().model_dump(mode="json"),
    }

    for name, data in fixtures.items():
        dst_test = test_dir / name
        dst_static = static_dir / name
        with open(dst_test, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.copyfile(dst_test, dst_static)
        print(f"Generated {dst_test} and {dst_static}")


if __name__ == "__main__":
    main()
