"""Прогнозирование траекторий и расчет preview «что если» (R2).

Строит динамические квантильные траектории (P10, P50, P90) для CV и MV на 4 часа вперед (25 точек),
оценивает риски превышения лимитов и эффект управляющих воздействий.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
import math
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import scipy.stats as stats

from src.agents.contracts import (
    ArbitrationDecision,
    Candidate,
    DecisionStatus,
    Merit,
    Tier,
)
from src.agents.economics import MarginModel
from src.agents.estimation import (
    LINEAR_PROP_CONFIG,
    Q_DRIFT_LOG,
    SIGMA_PAK_LOG,
)
from src.agents.policy import PolicyConfig
from src.agents.registry import ALL_SPECS, REGISTRY_ADAPTER, T1_SPECS, T2_SPECS
from src.agents.twin_view import TwinView
from src.console import corridor
from src.console.contracts import (
    Band,
    CorridorViolation,
    Effect,
    KernelCheckDTO,
    KernelInfo,
    LimitedStep,
    Point,
    PreviewRequest,
    PreviewResult,
    Risk,
    Trajectory,
)
from src.safety_kernel.kernel import SafetyKernel
from src.twin.params import load_params

if TYPE_CHECKING:
    from src.console.runtime import ConsoleSession


# Квантиль нормального распределения для интервала P10-P90 (80% доверительный интервал)
Z_80: float = 1.2816


# Общий адаптер реестра (src/agents/registry.py::RegistryAdapter) — тот же экземпляр,
# что теперь используется независимым ядром безопасности внутри графа core_v3
# (src/agents/graph.py::node_safety_kernel), чтобы предпросмотр и боевое решение
# проверялись одной и той же логикой T0.bounds/T0.rate/оборудования-качества.
_REGISTRY_ADAPTER = REGISTRY_ADAPTER

# Спеки ядра, нарушение которых в ручном пульте оператора показывается как
# предупреждение, а не как блокировка «Применить» (по решению оператора продукта:
# оператор технологической установки видит риск по сере и явно решает применить ход
# сам, вместо жёсткого отказа). См. can_commit_with_ack ниже — это поле читает ТОЛЬКО
# ручной коммит человека (src/console/service.py::commit()). can_commit сам по себе
# остаётся строгим (verdict.passed), поэтому ни автономный АВТОМАТ консоли
# (src/console/auto.py, который проверяет can_commit напрямую), ни боевой контур
# core_v3 (src/agents/graph.py::node_safety_kernel, отдельный вызов SafetyKernel.verify())
# этим послаблением не затрагиваются.
_SOFT_WARNING_SPECS = frozenset({"GODT.S_MAX"})


def _get_cv_limit(key: str) -> Tuple[float, str]:
    """Возвращает нормативный предел и смысл ограничения ('max' или 'min') из реестра."""
    if key == "sulfur":
        spec = next((s for s in T2_SPECS if s.key == "GODT.S_MAX"), None)
        return (spec.limit if spec else 0.0, spec.sense if spec else "max")
    elif key == "flash":
        spec = next((s for s in T2_SPECS if s.key == "GODT.FLASH_MIN"), None)
        return (spec.limit if spec else 0.0, spec.sense if spec else "min")
    elif key == "dp":
        spec = next((s for s in T1_SPECS if s.key == "RX.DP_MAX"), None)
        return (spec.limit if spec else 0.0, spec.sense if spec else "max")
    return (0.0, "max")


def _calc_uncertainties(
    session: ConsoleSession, horizon_steps: int = 24
) -> Tuple[List[float], List[float]]:
    """
    Рассчитывает динамику сигма на k шагов горизонта для серы и вспышки.
    """
    est = getattr(session, "last_estimate", None)

    # 1. Сера (лог-домен)
    s_est = est.quality.get("GODT.S") if (est and hasattr(est, "quality")) else None
    if s_est is not None:
        sigma_meas_s = s_est.sigma_meas
        base_sigma_calib_sq = s_est.sigma_calib ** 2
    else:
        sigma_meas_s = SIGMA_PAK_LOG
        base_sigma_calib_sq = 0.01

    dt_per_step_h = session.tick_delta.total_seconds() / 3600.0
    sigmas_sulfur: List[float] = []
    for k in range(horizon_steps + 1):
        dt_h = k * dt_per_step_h
        sigma_calib_k_sq = base_sigma_calib_sq + Q_DRIFT_LOG * dt_h
        sigma_tot = math.sqrt(sigma_meas_s ** 2 + sigma_calib_k_sq)
        sigmas_sulfur.append(sigma_tot)

    # 2. Вспышка (линейный домен)
    flash_cfg = LINEAR_PROP_CONFIG["FLASH"]
    base_sigma_f = flash_cfg["sigma_meas"]
    drift_f = flash_cfg["drift_per_h"]

    sigmas_flash: List[float] = []
    for k in range(horizon_steps + 1):
        dt_h = k * dt_per_step_h
        sigma_tot_f = math.sqrt(base_sigma_f ** 2 + drift_f * dt_h)
        sigmas_flash.append(sigma_tot_f)

    return sigmas_sulfur, sigmas_flash


def _anchor_now_value(session: ConsoleSession, cv_key: str, model_value: float) -> float:
    """
    Возвращает значение точки k=0 (СЕЙЧАС) для графика CV.

    Прогнозная (синяя) линия должна начинаться ровно там, где заканчивается фактическая
    (чёрная) — иначе на графике виден разрыв в точке "СЕЙЧАС" (тем заметнее, чем сильнее шум
    ПАК серы q21_noise_ppm или коррекция est.factors разошлись с чистым модельным значением на
    клоне двойника). Поэтому якорим на последнюю ТОЧКУ ИСТОРИИ cv_history — то самое число, что
    уже нарисовано как "факт" — а не на свежий independent пересчёт модели. Модельное значение
    остаётся запасным вариантом только когда истории ещё нет или последняя точка невалидна
    (MISSING/BAD, напр. отказ ПАК — там начинать с модели адекватнее, чем с NaN).
    """
    history = session.cv_history.get(cv_key)
    if history:
        last_point = history[-1]
        if last_point.v is not None and last_point.quality == "GOOD":
            return float(last_point.v)
    return model_value


def build_trajectory(
    session: ConsoleSession,
    u_target: Dict[str, float],
    kind: str,
) -> Trajectory:
    """
    Строит согласованную динамическую траекторию CV и MV на 4 часа вперед (25 точек).
    Использует клон двойника без побочных эффектов на сессию.
    """
    # Проверяем кэш сессии
    cache_key = (session.tick, tuple(sorted(u_target.items())), kind)
    traj_cache = getattr(session, "_traj_cache", None)
    if traj_cache is not None and cache_key in traj_cache:
        return traj_cache[cache_key]

    # Полная копия двойника для изолированного прогноза
    twin_clone = session.plant.twin.clone()
    est = getattr(session, "last_estimate", None)
    view = TwinView(twin_clone, estimate=est)

    horizon = 24
    cand = Candidate(
        signature=f"traj_{kind}_{session.tick}",
        delta_u={k: u_target[k] - session.u_current.get(k, u_target[k]) for k in u_target},
        origin="LOCAL",  # type: ignore
        proposed_by="forecast",
    )

    pred = view.predict(cand, horizon_steps=horizon, store_traj=True)
    traj_dict = pred.trajectory or {}

    s_sigmas, f_sigmas = _calc_uncertainties(session, horizon_steps=horizon)

    # Формируем точки now и future (всего 25 точек: k=0..24)
    sulfur_bands: List[Band] = []
    flash_bands: List[Band] = []
    dp_bands: List[Band] = []
    mv_plan: Dict[str, List[Point]] = {sp: [] for sp in session.u_current.keys()}

    s_lim, s_sense = _get_cv_limit("sulfur")
    f_lim, f_sense = _get_cv_limit("flash")
    dp_lim, dp_sense = _get_cv_limit("dp")

    # Значения в точке k=0 (СЕЙЧАС): один вызов twin_clone.step() на клоне двойника, не
    # затрагивающем сессию. Раньше здесь ЕЩЁ ДВАЖДЫ вызывался session.plant.measure() — для
    # flash и dp — что (а) не находило тег HT_FLASH (в ONLINE_OUTPUT_TAGS он пишется в self.tags
    # под именем HT_T18, а не HT_FLASH — см. src/twin/plant.py) и подставляло произвольную
    # заглушку 61.5 вместо реального текущего значения, из-за чего прогнозная (синяя) линия
    # визуально «проваливалась» в точке СЕЙЧАС относительно факта; и (б) реально продвигало
    # НАСТОЯЩИЙ симулятор сессии на лишний шаг при каждом построении траектории (measure()
    # мутирует self.plant, а не клон) — на каждый /preview и /state незаметно расходуя лишние
    # такты помимо tick_single().
    now_out = twin_clone.step(session.u_current)
    s_now_model = float(now_out.get("HT_S_PRODUCT", 9.0))
    # Скорректированное текущее
    if est and "GODT.S" in est.factors:
        s_now_model *= est.factors["GODT.S"]
    f_now_model = float(now_out.get("HT_FLASH", 61.5))
    dp_now_model = float(now_out.get("HT_DP_KPA", 177.0))

    # Якорим точку "СЕЙЧАС" на последнюю точку истории (факт), а не на чистый модельный
    # пересчёт — иначе шум ПАК серы (q21_noise_ppm) или коррекция est.factors создают видимый
    # разрыв между чёрной (факт) и синей (прогноз) линией именно в точке СЕЙЧАС. См. _anchor_now_value.
    s_now = _anchor_now_value(session, "sulfur", s_now_model)
    f_now = _anchor_now_value(session, "flash", f_now_model)
    dp_now = _anchor_now_value(session, "dp", dp_now_model)

    # Поправку якоря нужно пронести через ВЕСЬ горизонт (k=1..24), а не только k=0 — иначе
    # синяя линия совпадает с фактом ровно в точке "СЕЙЧАС" и тут же скачком уходит на
    # несдвинутую модельную траекторию на следующем шаге (виден острый излом сразу после
    # "СЕЙЧАС"). Сера — в лог-домене (её полосы неопределённости уже считаются через exp/log
    # ниже), поэтому сдвигаем множителем; вспышка/перепад — в линейном, сдвигаем на разницу.
    s_ratio = (s_now / s_now_model) if s_now_model > 1e-6 else 1.0
    f_offset = f_now - f_now_model
    dp_offset = dp_now - dp_now_model

    s_vals = [s_now] + [v * s_ratio for v in traj_dict.get("HT_S_PRODUCT", [s_now_model] * horizon)]
    f_vals = [f_now] + [v + f_offset for v in traj_dict.get("HT_FLASH", [f_now_model] * horizon)]
    dp_vals = [dp_now] + [v + dp_offset for v in traj_dict.get("HT_DP_KPA", [dp_now_model] * horizon)]

    # Сборка полос квантилей
    now_dt = session.now
    for k in range(25):
        t_str = (now_dt + session.tick_delta * k).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Сера (лог-домен)
        s_p50 = max(0.01, s_vals[k])
        sig_s = s_sigmas[k]
        s_p10 = math.exp(math.log(s_p50) - Z_80 * sig_s)
        s_p90 = math.exp(math.log(s_p50) + Z_80 * sig_s)
        sulfur_bands.append(Band(t=t_str, p10=round(s_p10, 3), p50=round(s_p50, 3), p90=round(s_p90, 3)))

        # Вспышка (линейный домен)
        f_p50 = f_vals[k]
        sig_f = f_sigmas[k]
        f_p10 = f_p50 - Z_80 * sig_f
        f_p90 = f_p50 + Z_80 * sig_f
        flash_bands.append(Band(t=t_str, p10=round(f_p10, 2), p50=round(f_p50, 2), p90=round(f_p90, 2)))

        # Перепад давления (без сигма)
        dp_p50 = dp_vals[k]
        dp_bands.append(Band(t=t_str, p10=None, p50=round(dp_p50, 1), p90=None))

        # MV ступеньки: на шаге 0 текущее, с шага 1 целевое
        for sp in session.u_current.keys():
            val = session.u_current[sp] if k == 0 else u_target.get(sp, session.u_current[sp])
            mv_plan[sp].append(Point(t=t_str, v=round(val, 3), quality="GOOD"))

    # Расчет рисков
    # 1. Сера
    s_risks_k = []
    s_first_breach = None
    for k in range(25):
        p50_k = sulfur_bands[k].p50
        p90_k = sulfur_bands[k].p90 or p50_k
        sig_s_k = s_sigmas[k]
        prob_k = float(1.0 - stats.norm.cdf((math.log(s_lim) - math.log(p50_k)) / sig_s_k))
        s_risks_k.append(prob_k)
        if s_first_breach is None and p90_k > s_lim:
            s_first_breach = sulfur_bands[k].t

    # 2. Вспышка
    f_risks_k = []
    f_first_breach = None
    for k in range(25):
        p50_f_k = flash_bands[k].p50
        p10_f_k = flash_bands[k].p10 or p50_f_k
        sig_f_k = f_sigmas[k]
        prob_f_k = float(stats.norm.cdf((f_lim - p50_f_k) / sig_f_k))
        f_risks_k.append(prob_f_k)
        if f_first_breach is None and p10_f_k < f_lim:
            f_first_breach = flash_bands[k].t

    # 3. Перепад
    dp_risks_k = [1.0 if dp_bands[k].p50 > dp_lim else 0.0 for k in range(25)]
    dp_first_breach = next((dp_bands[k].t for k in range(25) if dp_bands[k].p50 > dp_lim), None)

    risks = [
        Risk(cv="sulfur", probability=round(max(s_risks_k), 4), first_breach_at=s_first_breach, limit=s_lim),
        Risk(cv="flash", probability=round(max(f_risks_k), 4), first_breach_at=f_first_breach, limit=f_lim),
        Risk(cv="dp", probability=round(max(dp_risks_k), 4), first_breach_at=dp_first_breach, limit=dp_lim),
    ]

    traj = Trajectory(
        kind=kind,  # type: ignore
        u_target=u_target,
        cv={"sulfur": sulfur_bands, "flash": flash_bands, "dp": dp_bands},
        mv_plan=mv_plan,
        risk=risks,
        sigma_source="estimation.py:Q_DRIFT_LOG",
    )

    if traj_cache is not None:
        traj_cache[cache_key] = traj

    return traj


def build_plan_trajectory(
    session: ConsoleSession, steps: List[Dict[str, float]]
) -> Trajectory:
    """
    Строит динамическую траекторию для многошагового плана автомата.
    """
    if not steps:
        return build_trajectory(session, session.u_current, "auto_plan")
    final_u = steps[-1]
    return build_trajectory(session, final_u, "auto_plan")


def effect_of(traj: Trajectory, hold: Trajectory, session: ConsoleSession) -> Effect:
    """
    Рассчитывает ожидаемый эффект управляющего воздействия относительно hold.
    """
    sulfur_4h = traj.cv["sulfur"][-1].p50

    flash_4h = traj.cv["flash"][-1].p50
    flash_cfg = LINEAR_PROP_CONFIG["FLASH"]
    sigma_flash = flash_cfg["sigma_meas"]
    flash_lim, _ = _get_cv_limit("flash")
    flash_margin_c = round(flash_4h - 2.0 * sigma_flash - flash_lim, 1)

    # Дельта маржи
    try:
        econ_params = load_params().economics
        reactor_params = load_params().reactor
        mm = MarginModel(econ_params, reactor_params)
        twin_clone = session.plant.twin.clone()
        ss_curr = twin_clone.steady_state(session.u_current)
        ss_target = twin_clone.steady_state(traj.u_target)
        breakdown = mm.evaluate(ss_target, ss_curr, traj.u_target, session.u_current)
        margin_delta = round(breakdown.total, 0)
    except Exception:
        margin_delta = 0.0

    return Effect(
        sulfur_4h=sulfur_4h,
        flash_margin_c=flash_margin_c,
        margin_delta_rub_h=margin_delta,
    )


def preview(session: ConsoleSession, req: PreviewRequest) -> PreviewResult:
    """
    Выполняет быстрый расчет последствий операторской правки «что если».
    Проверяет коридор шага, ограничения ядра безопасности и строит траекторию.
    """
    t_start = time.perf_counter()

    # 1. Валидация входных данных
    for sp_name, val in req.u_target.items():
        if not math.isfinite(val):
            raise ValueError(f"Недопустимое нечисловое значение для {sp_name}: {val}")

    # 2. Проверка коридора шага и T0
    violations_raw = corridor.check_step(session.u_current, req.u_target)
    corridor_violations = [
        CorridorViolation(
            sp=v["sp"],
            requested_step=v["requested_step"],
            max_step=v["max_step"],
            text=v["text"],
        )
        for v in violations_raw
    ]
    corridor_ok = len(corridor_violations) == 0

    # 3. Проверка ядром безопасности SafetyKernel
    delta_u = {sp: req.u_target[sp] - session.u_current.get(sp, req.u_target[sp]) for sp in req.u_target}
    delta_u = {k: v for k, v in delta_u.items() if abs(v) > 1e-4}
    decision = ArbitrationDecision(
        status=DecisionStatus.SUCCESS,
        selected="operator_preview",
        delta_u=delta_u,
        merit=None,
        hold_merit=Merit(v=(0.0, 0.0, 0.0, 0.0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0),
    )

    est = getattr(session, "last_estimate", None)
    data = getattr(session, "last_data_assessment", None)
    policy = getattr(session, "policy", PolicyConfig())

    verdict = SafetyKernel.verify(
        decision=decision,
        estimate=est,
        data=data,
        registry=_REGISTRY_ADAPTER,
        policy=policy,
        twin_factory=lambda e: session.plant.twin.clone(),
    )

    kernel_checks = [
        KernelCheckDTO(name=c.name, passed=c.passed, detail=c.detail)
        for c in verdict.checks
    ]

    # Вердикт ядра для УДЕРЖАНИЯ текущего режима (delta_u={}) — нужен только для того, чтобы
    # отличить "мой ход нарушает лимит, которого раньше не было" от "лимит уже нарушен ПРЯМО
    # СЕЙЧАС, независимо от моего хода" (например, сценарий S5, выход за огибающую
    # оборудования: AVT_T55/HT_DP_KPA уже выше предела до всякого вмешательства оператора).
    # Во втором случае жёсткая блокировка "Применить" не защищает установку — она просто не
    # дает оператору вообще ничего сделать, включая ходы, которые реально снижают нарушение
    # (см. reliability.py::_apply_transient_rule и директиву оператора 2026-09-22 по сценарию
    # S5 — агенты не должны отсекать улучшающие решения как неликвидные только из-за того, что
    # предел не достигнут за один такт; тот же принцип применяется здесь к самому шагу
    # применения уставок). См. _SOFT_WARNING_SPECS ниже — расширена этой же логикой.
    hold_decision = ArbitrationDecision(
        status=DecisionStatus.SUCCESS,
        selected="operator_preview_hold",
        delta_u={},
        merit=None,
        hold_merit=Merit(v=(0.0, 0.0, 0.0, 0.0), utility_rub_h=0.0, min_slack=1.0, move_norm=0.0),
    )
    hold_verdict = SafetyKernel.verify(
        decision=hold_decision,
        estimate=est,
        data=data,
        registry=_REGISTRY_ADAPTER,
        policy=policy,
        twin_factory=lambda e: session.plant.twin.clone(),
    )
    already_failing_specs = frozenset(c.name for c in hold_verdict.checks if not c.passed)

    limited_steps: List[LimitedStep] = []
    kernel_info = KernelInfo(passed=verdict.passed, checks=kernel_checks, limited=limited_steps)

    # 4. Построение траектории и эффекта
    traj = build_trajectory(session, req.u_target, "operator")
    hold = getattr(session, "hold_trajectory", None)
    if hold is None:
        hold = build_trajectory(session, session.u_current, "hold")
    effect = effect_of(traj, hold, session)

    # 5. Принятие решения о возможности отправки.
    # can_commit остаётся строгим вердиктом (verdict.passed) — его читает не только ручной
    # коммит, но и автономный контур АВТОМАТ (src/console/auto.py: execute_auto_step()), для
    # которого это единственный гейт перед безнадзорным применением шага. Слабить его здесь
    # означало бы разрешить AUTO самому проехать через нарушение спека без человека — этого мы
    # не делаем.
    stale = False
    if req.cycle_id is not None and getattr(session, "last_cycle_id", None) is not None:
        stale = req.cycle_id != session.last_cycle_id

    can_commit = verdict.passed and corridor_ok and not stale

    # Отдельно вычисляем «мягкую» блокировку: причина отказа — либо спек из
    # _SOFT_WARNING_SPECS (сера GODT.S_MAX), либо ограничение, уже нарушенное ПРЯМО СЕЙЧАС при
    # удержании текущего режима (already_failing_specs — см. hold_verdict выше), а не что-то
    # НОВОЕ, привнесенное именно этой правкой. Только эти ситуации оператор может обойти
    # вручную, явно приняв риск — см. can_commit_with_ack и src/console/service.py::commit().
    # auto.py эту флаг-переменную не читает и продолжает смотреть только на строгий can_commit,
    # так что автономный контур не затрагивается: АВТОМАТ по-прежнему не может сам "проехать"
    # через уже нарушенный предел без подтверждения человека.
    def _is_soft(name: str) -> bool:
        return name in _SOFT_WARNING_SPECS or name in already_failing_specs

    blocking_checks = [c for c in verdict.checks if not c.passed and not _is_soft(c.name)]
    soft_checks = [c for c in verdict.checks if not c.passed and _is_soft(c.name)]
    can_commit_with_ack = (
        not can_commit
        and corridor_ok
        and not stale
        and not blocking_checks
        and bool(soft_checks)
    )
    pre_existing_soft = [c for c in soft_checks if c.name in already_failing_specs and c.name not in _SOFT_WARNING_SPECS]
    quality_warning = None
    if soft_checks:
        lead = pre_existing_soft[0] if pre_existing_soft else soft_checks[0]
        if lead.name in already_failing_specs and lead.name not in _SOFT_WARNING_SPECS:
            quality_warning = (
                f"Ограничение уже нарушено при удержании текущего режима ({lead.name}): {lead.detail}. "
                f"Ход не устраняет нарушение полностью за один такт, но не отсекается как неликвидный — "
                f"применение постепенного восстановления на ответственности оператора."
            )
        else:
            quality_warning = (
                f"Прогноз нарушает лимит качества ({lead.name}): {lead.detail}. "
                f"Применение — на ответственности оператора."
            )

    blocking_reason = None
    if not corridor_ok:
        blocking_reason = corridor_violations[0].text
    elif not verdict.passed:
        failed_checks = [c.detail for c in verdict.checks if not c.passed]
        blocking_reason = failed_checks[0] if failed_checks else "Отклонено ядром безопасности"
    elif stale:
        blocking_reason = "Рекомендация устарела (начался новый такт)"

    compute_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

    return PreviewResult(
        trajectory=traj,
        kernel=kernel_info,
        corridor_ok=corridor_ok,
        corridor_violations=corridor_violations,
        effect=effect,
        can_commit=can_commit,
        can_commit_with_ack=can_commit_with_ack,
        blocking_reason=blocking_reason,
        quality_warning=quality_warning,
        compute_ms=compute_ms,
    )
