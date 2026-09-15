"""Тесты Шага 6 MVP: Объяснимый ИИ (XAI) и REST API (FastAPI).

Проверяет:
1. Детерминированную генерацию диспетчерского Markdown-отчета XAIGenerator.
2. Физико-химическую аргументацию (квенч F15, печь T55, фракционирование).
3. Оценку рисков ПАЗ и статуса LIMS в отчете.
4. Работоспособность эндпоинта /api/v1/health.
5. Работоспособность эндпоинта /api/v1/optimize (успешный арбитраж + Safe Hold).
"""

import pytest
from fastapi.testclient import TestClient

from src.xai.narrative import XAIGenerator
from src.agents.state import ControlCandidate
from main import app

client = TestClient(app)


def test_xai_generator_success_report():
    """Тест 1: Генерация структурированного Markdown-отчета при успешной оптимизации."""
    candidate = ControlCandidate(
        candidate_id="cand_opt_01",
        delta_u={"F15": 250.0, "T55": -1.2, "F19": 10.0},
        expected_margin=4200.0,
        expected_sulfur=8.4
    )

    base_state = {
        "F15": 3200.0,
        "T55": 382.0,
        "F19": 80.0,
        "lims_age_hours": 3.0
    }

    report = XAIGenerator.generate_explanation(
        best_candidate=candidate,
        base_state=base_state,
        risk_penalties={},
        lims_age_hours=3.0
    )

    # Проверка структуры отчета
    assert "### 📊 Рекомендация Мультиагентной Системы APC/MES" in report
    assert "• **F15**: 3200.00 -> 3450.00 (+250.00)" in report
    assert "• **T55**: 382.00 -> 380.80 (-1.20)" in report
    assert "+4200.00 руб/ч" in report
    assert "8.40 ppm" in report
    assert "Уровень 1 ПАЗ" in report
    # Проверка физико-химической аргументации
    assert "квенча F15" in report
    assert "Р-202" in report
    assert "П-3" in report


def test_xai_generator_lims_warning_and_penalties():
    """Тест 2: Вывод предупреждений при устаревании LIMS и приближении к барьерам ПАЗ."""
    candidate = {
        "candidate_id": "cand_near_limit",
        "delta_u": {"T55": 1.5},
        "expected_margin": 6000.0,
        "expected_sulfur": 9.2
    }

    base_state = {"T55": 384.5}
    risk_penalties = {"cand_near_limit": 1250.0}

    report = XAIGenerator.generate_explanation(
        best_candidate=candidate,
        base_state=base_state,
        risk_penalties=risk_penalties,
        lims_age_hours=12.0  # Устаревание > 8 часов
    )

    assert "ВНИМАНИЕ: Высокая неопределенность" in report
    assert "штрафной риск ПАЗ: 1250 руб/ч" in report


def test_fastapi_health_endpoint():
    """Тест 3: Эндпоинт GET /api/v1/health возвращает статус healthy."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "neftecode-mas-api"


def test_fastapi_optimize_endpoint_success():
    """Тест 4: Эндпоинт POST /api/v1/optimize успешно прогоняет LangGraph и возвращает результат."""
    payload = {
        "tags": {
            "timestamp": "2026-09-15T15:30:00",
            "P52": 0.045,
            "D10": 840.0,
            "F15": 400.0,
            "T55": 380.0,
            "F5": 25.0,
            "F26": 80.0,
            "Sulfur": 8.2,
            "lims_age_hours": 2.0
        }
    }

    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert len(data["recommended_delta_u"]) > 0
    assert data["markdown_report"] is not None
    assert "### 📊 Рекомендация" in data["markdown_report"]
    assert data["blending_recipe"] is not None


def test_fastapi_optimize_endpoint_safe_hold_on_bad_data():
    """Тест 5: При устаревшем анализе LIMS > 24ч API возвращает SAFE_HOLD с регламентным отказом."""
    payload = {
        "tags": {
            "timestamp": "2026-09-15T15:30:00",
            "P52": 0.045,
            "D10": 840.0,
            "F15": 400.0,
            "T55": 380.0,
            "lims_age_hours": 28.0  # Устаревание LIMS
        }
    }

    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SAFE_HOLD"
    assert "Надёжной рекомендации нет" in data["explanation"]
    assert data["recommended_delta_u"] == {}
