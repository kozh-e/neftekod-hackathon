"""Фикстуры pytest для тестов пульта оператора."""

import pytest
from fastapi.testclient import TestClient

import main
from src.console.demo import load_scenario
from src.console.runtime import ConsoleSession, REGISTRY


@pytest.fixture(scope="session")
def client():
    """Тестовый клиент FastAPI."""
    return TestClient(main.app)


@pytest.fixture(scope="module")
def s2_session() -> ConsoleSession:
    """Сессия сценария S2 с выключенным метрономом."""
    session = load_scenario("test_s2", "S2", warmup_ticks=48)
    session.seconds_per_tick = 0.0
    return session


@pytest.fixture(scope="module")
def s1_auto_session() -> ConsoleSession:
    """Сессия сценария S1 в режиме AUTO с выключенным метрономом."""
    session = load_scenario("test_s1_auto", "S1", warmup_ticks=48)
    session.seconds_per_tick = 0.0
    from src.console.auto import set_mode
    set_mode(session, "AUTO")
    return session
