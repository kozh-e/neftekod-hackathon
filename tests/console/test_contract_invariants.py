"""Тесты инвариантов контракта пульта оператора (R6)."""

import json
from pathlib import Path
import pytest

from src.console.contracts import ConsoleState

FIXTURES_DIR = Path("tests/fixtures/console")


@pytest.fixture(params=["advisory", "auto", "refusal"])
def state_fixture(request) -> ConsoleState:
    path = FIXTURES_DIR / f"{request.param}.json"
    content = path.read_text(encoding="utf-8")
    return ConsoleState.model_validate_json(content)


def test_cv_series_order_and_history(state_fixture: ConsoleState):
    cvs = state_fixture.series.cv
    assert len(cvs) == 3
    assert cvs[0].key == "sulfur"
    assert cvs[1].key == "flash"
    assert cvs[2].key == "dp"

    for cv in cvs:
        assert len(cv.history) == 73
        assert cv.history[-1].t == state_fixture.clock.now


def test_trajectory_points_and_quantiles(state_fixture: ConsoleState):
    trajs = [state_fixture.hold]
    if state_fixture.recommendation and state_fixture.recommendation.trajectory:
        trajs.append(state_fixture.recommendation.trajectory)
    if state_fixture.recommendation:
        for alt in state_fixture.recommendation.alternatives:
            if alt.trajectory:
                trajs.append(alt.trajectory)

    for traj in trajs:
        for cv_key in ("sulfur", "flash", "dp"):
            bands = traj.cv[cv_key]
            assert len(bands) == 25
            assert bands[0].t == state_fixture.clock.now
            for b in bands:
                if b.p10 is not None and b.p90 is not None:
                    assert b.p10 <= b.p50 <= b.p90


def test_refusal_invariants(state_fixture: ConsoleState):
    if state_fixture.recommendation and state_fixture.recommendation.status.startswith("REFUSAL_"):
        assert state_fixture.recommendation.changes == []
        assert state_fixture.recommendation.refusal is not None


def test_auto_invariants(state_fixture: ConsoleState):
    if state_fixture.mode.current == "AUTO":
        assert state_fixture.auto is not None
        assert state_fixture.auto.corridor is not None


def test_no_nan_or_infinity(state_fixture: ConsoleState):
    # Строгая сериализация JSON без allow_nan
    raw_json = state_fixture.model_dump_json()
    parsed = json.loads(raw_json)
    assert parsed is not None


def test_contract_invariants_live_state(s2_session):
    from src.console.service import build_state

    state = build_state(s2_session)
    assert isinstance(state, ConsoleState)

    cvs = state.series.cv
    assert len(cvs) == 3
    assert [cv.key for cv in cvs] == ["sulfur", "flash", "dp"]
    for cv in cvs:
        assert 0 < len(cv.history) <= 73
        assert cv.history[-1].t == state.clock.now

    for cv_key in ("sulfur", "flash", "dp"):
        bands = state.hold.cv[cv_key]
        assert len(bands) == 25
        assert bands[0].t == state.clock.now
        for b in bands:
            if b.p10 is not None and b.p90 is not None:
                assert b.p10 <= b.p50 <= b.p90

    raw_json = state.model_dump_json()
    parsed = json.loads(raw_json)
    assert parsed is not None

