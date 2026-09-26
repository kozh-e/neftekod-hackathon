"""Тесты API эндпоинтов /api/console/... (R1 / R6)."""

from fastapi.testclient import TestClient
import pytest

from src.console.contracts import ParetoFrontDTO, XaiInfo


def test_get_state_returns_valid_console_state(client: TestClient):
    # Загрузим сценарий S2
    client.post("/api/console/demo/load", json={"session_id": "test_api", "scenario": "S2", "warmup_ticks": 10})
    resp = client.get("/api/console/state?session_id=test_api")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "1.0"
    assert data["session_id"] == "test_api"
    assert len(data["series"]["cv"]) == 3
    assert len(data["series"]["mv"]) == 5


def test_commit_corridor_violation_returns_409(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_corr", "scenario": "S2", "warmup_ticks": 5})
    # T6 max step is 3.0. Попробуем шагнуть на +5.0
    state_res = client.get("/api/console/state?session_id=test_corr").json()
    u_current = {mv["sp"]: mv["sp_history"][-1]["v"] for mv in state_res["series"]["mv"]}
    u_bad = dict(u_current)
    u_bad["HT_TIN_SP"] += 5.0

    commit_res = client.post(
        "/api/console/commit",
        json={
            "session_id": "test_corr",
            "cycle_id": state_res["recommendation"]["cycle_id"],
            "u_target": u_bad,
            "source": "operator_edit",
        },
    )
    assert commit_res.status_code == 409
    err = commit_res.json()["detail"]
    assert err["code"] == "CORRIDOR"


def test_commit_corridor_violation_p13_returns_409(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_corr_p13", "scenario": "S2", "warmup_ticks": 5})
    state_res = client.get("/api/console/state?session_id=test_corr_p13").json()
    u_current = {mv["sp"]: mv["sp_history"][-1]["v"] for mv in state_res["series"]["mv"]}
    u_bad = dict(u_current)
    # RATE.HT_P_SP.MAX = 0.05 МПа/такт (ASSUMPTION, см. registry.py) — шаг должен строго
    # превышать лимит, а не совпадать с ним.
    u_bad["HT_P_SP"] += 0.10

    commit_res = client.post(
        "/api/console/commit",
        json={
            "session_id": "test_corr_p13",
            "cycle_id": state_res["recommendation"]["cycle_id"],
            "u_target": u_bad,
            "source": "operator_edit",
        },
    )
    assert commit_res.status_code == 409
    err = commit_res.json()["detail"]
    assert err["code"] == "CORRIDOR"


def test_commit_kernel_violation_returns_409(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_kern", "scenario": "S2", "warmup_ticks": 5})
    state_res = client.get("/api/console/state?session_id=test_kern").json()
    u_current = {mv["sp"]: mv["sp_history"][-1]["v"] for mv in state_res["series"]["mv"]}
    u_bad = dict(u_current)
    # T55 step within corridor (max 2.0, hi 385.0) but eff_val (384.8 + 3.0 = 387.8) > T1 limit (386.4)
    u_bad["AVT_T55_SP"] = min(384.8, u_current.get("AVT_T55_SP", 381.7) + 1.8)

    commit_res = client.post(
        "/api/console/commit",
        json={
            "session_id": "test_kern",
            "cycle_id": state_res["recommendation"]["cycle_id"],
            "u_target": u_bad,
            "source": "operator_edit",
        },
    )
    assert commit_res.status_code == 409
    err = commit_res.json()["detail"]
    assert err["code"] == "KERNEL"


def test_commit_stale_recommendation_returns_409(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_stale", "scenario": "S2", "warmup_ticks": 5})
    state_res = client.get("/api/console/state?session_id=test_stale").json()
    old_cycle = state_res["recommendation"]["cycle_id"]

    # Делаем тик, чтобы cycle_id сменился
    client.post("/api/console/demo/tick", json={"session_id": "test_stale", "n": 1})

    u_current = {mv["sp"]: mv["sp_history"][-1]["v"] for mv in state_res["series"]["mv"]}
    commit_res = client.post(
        "/api/console/commit",
        json={
            "session_id": "test_stale",
            "cycle_id": old_cycle,
            "u_target": u_current,
            "source": "recommendation",
        },
    )
    assert commit_res.status_code == 409
    err = commit_res.json()["detail"]
    assert err["code"] == "STALE_RECOMMENDATION"


def test_mode_switch_and_auto_unavailable(client: TestClient):
    # S3d загружаем с отказом данных
    client.post("/api/console/demo/load", json={"session_id": "test_mode", "scenario": "S3d", "warmup_ticks": 5})

    # Попытка включить авто в REFUSAL должна вернуть 409
    mode_res = client.post("/api/console/mode", json={"session_id": "test_mode", "mode": "AUTO"})
    assert mode_res.status_code == 409
    err = mode_res.json()["detail"]
    assert err["code"] == "AUTO_UNAVAILABLE"


def test_get_pareto_endpoint(client: TestClient):
    # До первого такта (новая сессия)
    resp_empty = client.get("/api/console/pareto?session_id=new_pareto_test")
    assert resp_empty.status_code == 200
    assert resp_empty.json() is None

    # После загрузки сценария S2
    client.post("/api/console/demo/load", json={"session_id": "test_pareto_ep", "scenario": "S2", "warmup_ticks": 5})
    resp = client.get("/api/console/pareto?session_id=test_pareto_ep")
    assert resp.status_code == 200
    data = resp.json()
    assert data is not None
    dto = ParetoFrontDTO.model_validate(data)
    assert len(dto.points) >= 1


def test_get_xai_endpoint(client: TestClient):
    # До первого такта
    resp_empty = client.get("/api/console/xai?session_id=new_xai_test")
    assert resp_empty.status_code == 200
    assert resp_empty.json() is None

    # После загрузки сценария S2
    client.post("/api/console/demo/load", json={"session_id": "test_xai_ep", "scenario": "S2", "warmup_ticks": 5})
    resp = client.get("/api/console/xai?session_id=test_xai_ep")
    assert resp.status_code == 200
    data = resp.json()
    assert data is not None
    dto = XaiInfo.model_validate(data)
    assert len(dto.events) >= 1


def test_get_constants_endpoint(client: TestClient):
    resp = client.get("/api/console/constants?session_id=test_const")
    assert resp.status_code == 200
    constants = resp.json()
    assert isinstance(constants, list)
    assert len(constants) >= 30
    for c in constants:
        assert "category" in c
        assert "key" in c
        assert "label" in c
        assert "value" in c
        assert "unit" in c
        assert "provenance" in c
        assert "source_ref" in c
        assert "description" in c
        assert c["provenance"] in ("NORM", "POLICY", "ASSUMPTION", "REGISTRY")


def test_narrative_formatting_no_raw_dict(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_narr", "scenario": "S2", "warmup_ticks": 5})
    resp = client.get("/api/console/state?session_id=test_narr")
    assert resp.status_code == 200
    data = resp.json()
    rec = data.get("recommendation")
    assert rec is not None
    narr = rec.get("narrative")
    assert narr
    assert "{'hold_violated'" not in narr
    assert "{'alerts'" not in narr
    assert not narr.startswith("{")
    assert not narr.endswith("}")


def test_feed_max_5_items(client: TestClient):
    client.post("/api/console/demo/load", json={"session_id": "test_feed_lim", "scenario": "S2", "warmup_ticks": 10})
    resp = client.get("/api/console/state?session_id=test_feed_lim")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data.get("feed", [])) <= 5


def test_scenario_s4_load_and_ticks_do_not_crash(client: TestClient):
    """Регрессия (аудит 2026-09-22): загрузка S4 с полным прогревом (48 тактов) раньше
    падала с 500 на 49-м такте — negotiation-кандидат двигает печь (AVT_T55_SP),
    ансамбль ADR-19 (evaluate_furnace_move) его отклоняет, и agents/quality.py собирал
    ConstraintEvaluation(status=VIOLATED, ...) для spec_key="FURNACE.ENSEMBLE" БЕЗ поля
    slack (Optional[float], дефолт None). xai/card.py::build_decision_card форматировал
    его как f"{ev.slack:.3f}" для любой VIOLATED/ACTIVE evaluation -> TypeError -> весь
    graph.invoke() падал.
    Это был не просто одноразовый сбой одного запроса: если такое падение происходило
    внутри ФОНОВОГО такта метронома (SessionRegistry._metronome_loop), последствия были
    намного хуже — session.last_tick_time проставляется в САМОМ КОНЦЕ tick_single(), уже
    после точки падения, так что метка не продвигалась; на следующем проходе метронома
    (через 0.2с) условие "такт пора выполнять" снова истинно, такт падал заново — и так
    бесконечно, без единой паузы, каждая попытка реально сжигая ~1-1.5с CPU на
    data_guard/estimate/negotiation до точки падения. Итог — фоновый поток непрерывно, а
    не раз в seconds_per_tick, боролся за GIL с любым конкурентным запросом (в т.ч.
    /api/console/preview), что и проявлялось как «Пересчитать прогноз висит вечность».
    Здесь проверяем оба уровня защиты: (1) сама загрузка S4 (единственный сценарий,
    детерминированно воспроизводивший баг на 49-м такте) не должна падать; (2) сессия
    должна нормально тикать и дальше вручную через /demo/tick, и /preview на ней должен
    отвечать быстро и корректно — то есть тот самый затяжной такт больше не "залипает".
    """
    resp = client.post(
        "/api/console/demo/load",
        json={"session_id": "test_s4_regression", "scenario": "S4", "warmup_ticks": 48},
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["clock"]["tick"] == 49

    # Несколько дополнительных ручных тактов подряд — воспроизводит то, что раньше было
    # бесконечным повтором одного и того же падающего такта в фоновом потоке метронома.
    resp = client.post("/api/console/demo/tick", json={"session_id": "test_s4_regression", "n": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json()["clock"]["tick"] == 52

    u_current = {
        mv["sp"]: mv["sp_history"][-1]["v"]
        for mv in resp.json()["series"]["mv"]
        if mv.get("sp_history")
    }
    preview_resp = client.post(
        "/api/console/preview",
        json={"session_id": "test_s4_regression", "u_target": u_current, "cycle_id": None},
    )
    assert preview_resp.status_code == 200, preview_resp.text

