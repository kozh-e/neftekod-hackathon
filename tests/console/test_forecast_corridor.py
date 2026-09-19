"""Тесты прогноза, коридоров и preview (R2 / R6)."""

import time
import pytest

from src.console.contracts import PreviewRequest
from src.console.corridor import check_step, corridor_for
from src.console.forecast import build_trajectory, preview
from src.console.runtime import ConsoleSession


def test_corridor_specs():
    corr_f9 = corridor_for("HT_FEED_SP")
    assert corr_f9.max_step_per_tick == 10.0
    assert corr_f9.lo is not None and corr_f9.hi is not None

    corr_t6 = corridor_for("HT_TIN_SP")
    assert corr_t6.max_step_per_tick == 3.0

    corr_p13 = corridor_for("HT_P_SP")
    assert corr_p13.max_step_per_tick is None

    corr_gor = corridor_for("HT_GOR_SP")
    assert corr_gor.max_step_per_tick is None

    corr_t55 = corridor_for("AVT_T55_SP")
    assert corr_t55.max_step_per_tick == 3.0


def test_check_step_violations():
    u_curr = {"HT_TIN_SP": 363.3, "HT_P_SP": 3.922}

    # Превышение шага T6
    v1 = check_step(u_curr, {"HT_TIN_SP": 367.5})
    assert any(v["sp"] == "HT_TIN_SP" and v["max_step"] == 3.0 for v in v1)

    # Изменение P13 без коридора в паспорте
    v2 = check_step(u_curr, {"HT_P_SP": 3.950})
    assert any(v["sp"] == "HT_P_SP" and v["max_step"] is None for v in v2)


def test_preview_no_side_effects(s2_session: ConsoleSession):
    u_init = dict(s2_session.u_current)
    truth_init = dict(s2_session.plant.truth())

    req = PreviewRequest(session_id=s2_session.session_id, u_target=u_init)
    for _ in range(10):
        res = preview(s2_session, req)
        assert res is not None

    assert s2_session.u_current == u_init
    assert s2_session.plant.truth() == truth_init


@pytest.mark.perf
def test_preview_latency(s2_session: ConsoleSession):
    u_test = dict(s2_session.u_current)
    u_test["HT_TIN_SP"] += 1.0
    req = PreviewRequest(session_id=s2_session.session_id, u_target=u_test)

    latencies = []
    for _ in range(20):
        t0 = time.perf_counter()
        res = preview(s2_session, req)
        latencies.append((time.perf_counter() - t0) * 1000.0)

    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies))]
    assert p95 < 300.0, f"p95 latency {p95:.1f} ms >= 300 ms"


def test_s2_story(s2_session: ConsoleSession):
    hold = s2_session.hold_trajectory or build_trajectory(s2_session, s2_session.u_current, "hold")
    rec_u = dict(s2_session.u_current)
    rec_u["HT_TIN_SP"] += 1.7
    rec_u["HT_GOR_SP"] += 20.0
    rec_traj = build_trajectory(s2_session, rec_u, "recommendation")

    # В сценарии S2 сера растет на hold
    s_hold_end = hold.cv["sulfur"][-1].p50
    s_rec_end = rec_traj.cv["sulfur"][-1].p50
    assert s_rec_end < s_hold_end, f"Рекомендация ({s_rec_end}) должна быть эффективнее hold ({s_hold_end})"


def test_note_hold_consistency(s2_session: ConsoleSession):
    from src.console.service import build_state

    state = build_state(s2_session)
    for cv in state.series.cv:
        bands = state.hold.cv.get(cv.key, [])
        lim = cv.limit.value
        breaches = [
            b for b in bands
            if (b.p50 > lim if cv.limit.sense == "max" else b.p50 < lim)
        ]
        if breaches:
            assert cv.note is not None, f"CV {cv.key} breaches limit {lim} in hold, but note is None"
            assert "без изменений пробьёт" in cv.note
        else:
            assert cv.note is None, f"CV {cv.key} does not breach limit {lim}, but note is {cv.note}"

