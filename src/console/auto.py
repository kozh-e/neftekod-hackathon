"""Логика автоматического управления технологическим комплексом (R3).

Управляет переключением режимов СОВЕТ/АВТОМАТ, контролем автовыхода и исполнением шагов автомата.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from src.agents.contracts import AutomationLevel, DecisionStatus
from src.agents.policy import AutomationThresholds
from src.agents.registry import T0_SPECS, T1_SPECS, T2_SPECS
from src.console import corridor, forecast
from src.console.contracts import (
    AutoExitCode,
    AutoInfo,
    AutoNextStep,
    AutoPlanStep,
    Banner,
    Change,
    FeedItem,
    LastAutoExit,
    ModeInfo,
    PreviewRequest,
    PreviewResult,
    SP,
)

if TYPE_CHECKING:
    from src.console.runtime import ConsoleSession


class AutoUnavailable(Exception):
    """Исключение при попытке включить АВТОМАТ при недоступности условий."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# Пороги запаса до ПАЗ/пределов оборудования для автовыхода NEAR_LIMIT
AUTO_EXIT_MARGINS: Dict[str, Optional[float]] = {
    "AVT_T55": 2.0,  # Запас до ПАЗ печи П-3 (386.4 - 2.0 = 384.4 °C)
    "HT_P8": None,   # Для остальных параметров порог не задан в паспорте
    "HT_T11": None,
    "HT_GOR": None,
}


def is_auto_available(session: ConsoleSession) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, доступен ли режим АВТОМАТ для сессии.
    Возвращает (доступен, причина_недоступности).
    """
    # 1. Проверка статуса последнего решения
    last_res = getattr(session, "last_graph_result", None)
    if last_res:
        final_rec = last_res.get("final_recommendation")
        status = getattr(final_rec, "status", None)
        if status in (
            DecisionStatus.REFUSAL_DATA,
            DecisionStatus.REFUSAL_NO_SAFE_ACTION,
            DecisionStatus.REFUSAL_TIMEOUT,
            "SAFE_HOLD",
            "SAFE_HOLD_REFUSAL",
        ):
            return False, f"Решение системы в статусе {status}"
        if status == DecisionStatus.RECOVERY_ADVISORY:
            return False, "Активен аварийный план восстановления (RECOVERY_ADVISORY)"

    # 2. Проверка условий автовыхода
    exit_check = check_auto_exit_conditions(session)
    if exit_check is not None:
        _, reason_text = exit_check
        return False, reason_text

    return True, None


def check_auto_exit_conditions(session: ConsoleSession) -> Optional[Tuple[AutoExitCode, str]]:
    """
    Проверяет условия автоматического выхода из режима АВТОМАТ по строгому приоритету D5/R3:
    1. REFUSAL_DATA
    2. RECOVERY_ADVISORY
    3. NEAR_LIMIT
    4. LOW_CONFIDENCE
    5. LIMS_STALE
    """
    last_res = getattr(session, "last_graph_result", None)
    now_str = session.now.strftime("%H:%M")

    # 1. REFUSAL_DATA
    if last_res:
        final_rec = last_res.get("final_recommendation")
        status = getattr(final_rec, "status", None)
        if status in (DecisionStatus.REFUSAL_DATA, "SAFE_HOLD", "SAFE_HOLD_REFUSAL"):
            return "REFUSAL_DATA", "Отказ по данным КИПиА/LIMS (REFUSAL_DATA)"

    dq = last_res.get("data_quality") if last_res else None
    if dq and (
        getattr(dq, "automation_level", None) == AutomationLevel.REFUSAL_DATA
        or getattr(dq, "status_code", None) in ("REFUSAL_DATA", "SAFE_HOLD_REFUSAL")
        or not getattr(dq, "is_valid", True)
    ):
        return "REFUSAL_DATA", "DataGuard зафиксировал недостоверность ключевых данных"

    # 2. RECOVERY_ADVISORY
    if last_res:
        final_rec = last_res.get("final_recommendation")
        status = getattr(final_rec, "status", None)
        if status == DecisionStatus.RECOVERY_ADVISORY:
            return "RECOVERY_ADVISORY", "Система требует аварийного возврата в регламент"

    # 3. NEAR_LIMIT
    # session.last_measurement — последний снимок из measure() внутри tick_single(), а не новый
    # вызов measure() (см. комментарий у last_measurement в runtime.py: measure() мутирует
    # физическую модель и не защищён session.lock).
    measurements = session.last_measurement
    # Проверка печи П-3 AVT_T55
    t55_val = float(measurements.get("AVT_T55", session.u_current.get("AVT_T55_SP", 380.0)))
    cot_spec = next((s for s in T1_SPECS if s.key == "FURNACE.COT_MAX"), None)
    cot_limit = cot_spec.limit if cot_spec is not None else float("inf")
    t55_margin_thr = AUTO_EXIT_MARGINS.get("AVT_T55", 2.0) or 2.0
    if t55_val >= cot_limit - t55_margin_thr:
        return (
            "NEAR_LIMIT",
            f"Приближение к ПАЗ печи П-3: T55={t55_val:.1f} °C (предел {cot_limit} °C, запас < {t55_margin_thr} °C)",
        )

    # Проверка других T1 пределов
    dp_val = float(measurements.get("HT_DP_KPA", measurements.get("HT_P8", 177.0)))
    dp_spec = next((s for s in T1_SPECS if s.key == "RX.DP_MAX"), None)
    if dp_spec and dp_val > dp_spec.limit:
        return "NEAR_LIMIT", f"Превышен предел перепада Р-202: {dp_val:.1f} > {dp_spec.limit} кПа"

    # 4. LOW_CONFIDENCE (для серы p90 - p10 накрывает лимит)
    hold_traj = getattr(session, "hold_trajectory", None)
    if hold_traj is not None:
        sulfur_bands = hold_traj.cv.get("sulfur", [])
        s_spec = next((s for s in T2_SPECS if s.key == "GODT.S_MAX"), None)
        s_lim = s_spec.limit if s_spec is not None else float("inf")
        for b in sulfur_bands:
            if b.p10 is not None and b.p90 is not None:
                uncertainty_width = b.p90 - b.p10
                slack_to_limit = abs(s_lim - b.p50)
                if uncertainty_width > slack_to_limit and b.p90 > s_lim:
                    return (
                        "LOW_CONFIDENCE",
                        f"Высокая неопределенность серы: доверительный интервал [{b.p10:.1f}, {b.p90:.1f}] накрывает лимит {s_lim} ppm",
                    )

    # 5. LIMS_STALE
    lims_age = float(measurements.get("lims_age_hours", 0.0))
    guard_threshold = AutomationThresholds().refusal_lims_age_h_without_pak
    if lims_age > guard_threshold:
        return (
            "LIMS_STALE",
            f"Анализ LIMS устарел: возраст {lims_age:.1f} ч превышает регламентный порог {guard_threshold} ч",
        )

    return None


def check_auto_exit(session: ConsoleSession) -> Optional[Tuple[AutoExitCode, str]]:
    """
    Выполняет проверку условий автовыхода. Если условие сработало — производит выход из автомата,
    формирует баннер и добавляет запись в ленту.
    """
    trigger = check_auto_exit_conditions(session)
    if trigger is None:
        return None

    code, text = trigger
    now_iso = session.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_hm = session.now.strftime("%H:%M")

    session.mode = "ADVISORY"
    session.auto_since = None
    session.last_auto_exit = LastAutoExit(at=now_iso, reason_code=code, text=text)

    # Формирование баннера
    session.banner = Banner(
        id=f"banner_{session.tick}",
        kind="AUTO_EXIT",
        title=f"АВТОМАТ ОТКЛЮЧЁН в {now_hm}",
        text=text,
        at=now_iso,
        ack_required=True,
    )

    # Запись в ленту
    session.feed.appendleft(
        FeedItem(
            id=f"{session.last_cycle_id or 'auto'}:exit:{session.tick}",
            t=now_hm,
            kind="warn",
            text=f"Автовыход: {text}",
            source="auto",
        )
    )

    return trigger


def set_mode(session: ConsoleSession, mode: str) -> ModeInfo:
    """
    Переключает режим управления сессии (ADVISORY или AUTO).
    При невозможности включить AUTO выбрасывает AutoUnavailable.
    """
    now_iso = session.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_hm = session.now.strftime("%H:%M")

    if mode == "AUTO":
        available, reason = is_auto_available(session)
        if not available:
            raise AutoUnavailable(reason or "Автомат временно недоступен")

        session.mode = "AUTO"
        session.auto_since = now_iso
        session.feed.appendleft(
            FeedItem(
                id=f"mode:{session.tick}",
                t=now_hm,
                kind="ok",
                text="Автомат включён старшим оператором. Цель: «Сбалансировано».",
                source="operator",
            )
        )
    elif mode == "ADVISORY":
        session.mode = "ADVISORY"
        session.auto_since = None
        session.last_auto_exit = LastAutoExit(
            at=now_iso, reason_code="OPERATOR", text="Отключён старшим оператором"
        )
        session.feed.appendleft(
            FeedItem(
                id=f"mode:{session.tick}",
                t=now_hm,
                kind="info",
                text="Оператор переключил в режим СОВЕТ.",
                source="operator",
            )
        )
    else:
        raise ValueError(f"Неизвестный режим управления: {mode}")

    return build_mode_info(session)


def auto_step(session: ConsoleSession) -> List[FeedItem]:
    """
    Выполняет один шаг автоматического управления:
    - если был пропуск шага: сбрасывает флаг и пишет info;
    - иначе: берет рекомендацию, обрезает до коридора, проверяет ядром и применяет.
    """
    new_items: List[FeedItem] = []
    now_hm = session.now.strftime("%H:%M")

    if session.skipped_next:
        session.skipped_next = False
        item = FeedItem(
            id=f"auto:skip:{session.tick}",
            t=now_hm,
            kind="info",
            text="Шаг пропущен оператором",
            source="auto",
        )
        session.feed.appendleft(item)
        return [item]

    last_res = getattr(session, "last_graph_result", None)
    if not last_res:
        return []

    final_rec = last_res.get("final_recommendation")
    delta_u = dict(getattr(final_rec, "recommended_delta_u", {}) or {})
    if not delta_u:
        return []

    # Обрезаем дельты до коридора шага
    clipped_delta: Dict[str, float] = {}
    was_clipped = False
    clipped_details: List[str] = []

    for sp_name, requested_delta in delta_u.items():
        corr = corridor.corridor_for(sp_name)
        if corr.max_step_per_tick is None:
            # MV без коридора скорости автомат не двигает
            clipped_delta[sp_name] = 0.0
            if abs(requested_delta) > 1e-4:
                was_clipped = True
                clipped_details.append(f"{sp_name} оставлен без движения (нет шага в паспорте)")
        else:
            max_s = corr.max_step_per_tick
            if abs(requested_delta) > max_s + 1e-6:
                was_clipped = True
                val = max(-max_s, min(max_s, requested_delta))
                clipped_delta[sp_name] = round(val, 3)
                clipped_details.append(f"{sp_name}: {requested_delta:+.1f} → {val:+.1f}")
            else:
                clipped_delta[sp_name] = requested_delta

    # Формируем целевые уставки
    u_target = {
        sp: round(session.u_current.get(sp, 0.0) + clipped_delta.get(sp, 0.0), 3)
        for sp in session.u_current.keys()
    }

    # Проверка через preview
    prev_req = PreviewRequest(session_id=session.session_id, u_target=u_target)
    prev_res = forecast.preview(session, prev_req)

    if prev_res.can_commit:
        # Применяем шаг
        session.apply(u_target)

        # Запись применения
        applied_strs = [
            f"{sp.replace('_SP', '')} {session.u_current[sp] - clipped_delta[sp]:.1f} → {session.u_current[sp]:.1f}"
            for sp in clipped_delta
            if abs(clipped_delta[sp]) > 1e-4
        ]
        applied_text = ", ".join(applied_strs) or "уставки подтверждены"
        ok_item = FeedItem(
            id=f"auto:apply:{session.tick}",
            t=now_hm,
            kind="ok",
            text=f"Автомат: {applied_text}",
            source="auto",
        )
        session.feed.appendleft(ok_item)
        new_items.append(ok_item)

        if was_clipped:
            shield_item = FeedItem(
                id=f"auto:shield:{session.tick}",
                t=now_hm,
                kind="shield",
                text=f"Коридор ограничил шаг: {'; '.join(clipped_details)}",
                source="corridor",
            )
            session.feed.appendleft(shield_item)
            new_items.append(shield_item)
    else:
        # Ядро отклонило полный (урезанный по коридору) шаг. Раньше автомат сразу сдавался и
        # принудительно уходил в СОВЕТ с блокирующим баннером на каждый такой отказ — по решению
        # оператора (2026-09-20) автомат теперь сам подбирает уменьшенный, но безопасный шаг в
        # ТОМ ЖЕ направлении (бисекция масштаба clipped_delta от 0 до 1, каждый кандидат — через
        # тот же forecast.preview(), что и раньше) и уходит в СОВЕТ только если безопасного хода
        # вообще не существует — не проходит даже удержание текущего режима (масштаб 0). Это
        # по-прежнему последняя линия защиты, просто больше не первая.
        def _preview_scaled(scale: float) -> Tuple[Dict[str, float], Dict[str, float], PreviewResult]:
            scaled_delta = {sp: round(v * scale, 4) for sp, v in clipped_delta.items()}
            scaled_target = {
                sp: round(session.u_current.get(sp, 0.0) + scaled_delta.get(sp, 0.0), 3)
                for sp in session.u_current.keys()
            }
            req = PreviewRequest(session_id=session.session_id, u_target=scaled_target)
            return scaled_delta, scaled_target, forecast.preview(session, req)

        hold_delta, hold_target, hold_res = _preview_scaled(0.0)

        if not hold_res.can_commit:
            # Даже удержание текущего режима не проходит ядро — самостоятельно предпринять
            # нечего, это и есть настоящая последняя линия защиты.
            shield_item = FeedItem(
                id=f"auto:reject:{session.tick}",
                t=now_hm,
                kind="shield",
                text=f"Ядро отклонило шаг автомата: {prev_res.blocking_reason}",
                source="kernel",
            )
            session.feed.appendleft(shield_item)
            new_items.append(shield_item)

            now_iso = session.now.strftime("%Y-%m-%dT%H:%M:%SZ")
            session.mode = "ADVISORY"
            session.auto_since = None
            session.last_auto_exit = LastAutoExit(
                at=now_iso,
                reason_code="KERNEL_REJECT",
                text=f"Безопасного хода не найдено даже при удержании режима: {hold_res.blocking_reason}",
            )
            session.banner = Banner(
                id=f"banner_reject_{session.tick}",
                kind="AUTO_EXIT",
                title=f"АВТОМАТ ОТКЛЮЧЁН в {now_hm}",
                text=f"Безопасного хода не найдено даже при удержании режима: {hold_res.blocking_reason}",
                at=now_iso,
                ack_required=True,
            )
        else:
            # Бисекция: ищем наибольший масштаб в [0, 1], ещё проходящий ядро. hi=1.0 уже
            # отклонён (prev_res), lo=0.0 уже проверен и безопасен (hold_res).
            lo, lo_delta, lo_target = 0.0, hold_delta, hold_target
            hi = 1.0
            for _ in range(6):
                mid = (lo + hi) / 2
                mid_delta, mid_target, mid_res = _preview_scaled(mid)
                if mid_res.can_commit:
                    lo, lo_delta, lo_target = mid, mid_delta, mid_target
                else:
                    hi = mid

            if lo <= 1e-3:
                # Нашли только "не двигаться" — по сути то же, что и удержание.
                info_item = FeedItem(
                    id=f"auto:selfheal_hold:{session.tick}",
                    t=now_hm,
                    kind="info",
                    text=f"Автомат удержал текущий режим: предложенный шаг отклонён ядром ({prev_res.blocking_reason})",
                    source="auto",
                )
                session.feed.appendleft(info_item)
                new_items.append(info_item)
            else:
                session.apply(lo_target)

                applied_strs = [
                    f"{sp.replace('_SP', '')} {session.u_current[sp] - lo_delta[sp]:.1f} → {session.u_current[sp]:.1f}"
                    for sp in lo_delta
                    if abs(lo_delta[sp]) > 1e-4
                ]
                applied_text = ", ".join(applied_strs) or "уставки подтверждены"
                ok_item = FeedItem(
                    id=f"auto:apply:{session.tick}",
                    t=now_hm,
                    kind="ok",
                    text=f"Автомат: {applied_text}",
                    source="auto",
                )
                session.feed.appendleft(ok_item)
                new_items.append(ok_item)

                shield_item = FeedItem(
                    id=f"auto:selfheal:{session.tick}",
                    t=now_hm,
                    kind="shield",
                    text=(
                        f"Автомат сам сократил шаг до {lo * 100:.0f}% от рекомендованного, "
                        f"чтобы не нарушить ядро безопасности: {prev_res.blocking_reason}"
                    ),
                    source="kernel",
                )
                session.feed.appendleft(shield_item)
                new_items.append(shield_item)

    return new_items


def build_mode_info(session: ConsoleSession) -> ModeInfo:
    """Собирает DTO информации о режиме."""
    avail, reason = is_auto_available(session)
    return ModeInfo(
        current=session.mode,
        auto_available=avail,
        auto_unavailable_reason=reason,
        auto_since=session.auto_since,
        last_auto_exit=session.last_auto_exit,
    )


def build_auto_info(session: ConsoleSession) -> Optional[AutoInfo]:
    """Собирает AutoInfo для отображения в карточке режима АВТОМАТ."""
    if session.mode != "AUTO":
        return None

    last_res = getattr(session, "last_graph_result", None)
    delta_u = {}
    narrative = "Поддержание текущего режима."
    if last_res:
        final_rec = last_res.get("final_recommendation")
        if final_rec:
            delta_u = dict(getattr(final_rec, "recommended_delta_u", {}) or {})
            narrative = getattr(final_rec, "explanation", narrative)

    # Клипируем для отображения следующего шага
    changes: List[Change] = []
    meta = {
        "HT_FEED_SP": ("HT_F9", "Сырьё ГО", "т/ч", 1),
        "HT_TIN_SP": ("HT_T6", "Вход Р-202", "°C", 1),
        "HT_P_SP": ("HT_P13", "Давление", "МПа", 3),
        "HT_GOR_SP": ("HT_GOR", "Кратность ВСГ", "нм³/м³", 0),
        "AVT_T55_SP": ("AVT_T55", "Перевал П-3", "°C", 1),
    }

    for sp_name, (tag, title, unit, decimals) in meta.items():
        du = delta_u.get(sp_name, 0.0)
        corr = corridor.corridor_for(sp_name)
        if corr.max_step_per_tick is not None:
            max_s = corr.max_step_per_tick
            du = max(-max_s, min(max_s, du))
        else:
            du = 0.0

        if abs(du) > 1e-4:
            curr_v = session.u_current.get(sp_name, 0.0)
            target_v = curr_v + du
            changes.append(
                Change(
                    sp=sp_name,  # type: ignore
                    tag=tag,
                    title=title,
                    unit=unit,
                    current=round(curr_v, decimals),
                    target=round(target_v, decimals),
                    delta=round(du, decimals),
                    decimals=decimals,
                )
            )

    next_step_in_s = getattr(session, "next_tick_in_s", None) or 150.0
    at_str = (session.now + session.tick_delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    next_step = AutoNextStep(
        at=at_str,
        in_s=next_step_in_s,
        changes=changes,
        narrative=narrative,
    )

    plan = [
        AutoPlanStep(at=at_str, changes=changes),
    ]

    all_corridors = {sp: corridor.corridor_for(sp) for sp in meta.keys()}

    return AutoInfo(
        next_step=next_step,
        plan=plan,
        corridor=all_corridors,
        skipped_next=session.skipped_next,
    )
