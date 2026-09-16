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
    assert "сырья F15" in report
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


def test_fastapi_optimize_endpoint_success(quality_risk_tags):
    """Тест 4: Эндпоинт POST /api/v1/optimize успешно прогоняет LangGraph и возвращает результат."""
    payload = {
        "tags": quality_risk_tags
    }

    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"].startswith("SUCCESS")
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


def test_streamlit_app_renders():
    """Тест 6: Streamlit приложение компилируется и исполняется без ошибок и исключений."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    ui_path = Path(__file__).resolve().parent.parent / "src" / "ui" / "app.py"
    at = AppTest.from_file(str(ui_path))
    at.run()
    assert not at.exception, f"Streamlit app raised an exception: {at.exception}"


def test_fastapi_optimize_endpoint_with_custom_economics(quality_risk_tags):
    """Тест 7: Эндпоинт POST /api/v1/optimize принимает опциональный блок economics и выполняет расчет."""
    payload = {
        "tags": quality_risk_tags,
        "economics": {
            "price_godt": 72000.0,
            "price_straight_run": 54000.0,
            "fuel_rub_mwh": 2800.0,
            "min_margin_improvement": 500.0,
        },
    }

    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"].startswith("SUCCESS")
    assert len(data["recommended_delta_u"]) > 0
    assert "economics" in data and data["economics"] is not None
    assert "crack_spreads" in data["economics"]
    assert data["economics"]["hourly_gross_margin_rub_h"] > 0


def test_economics_aliases_and_properties():
    """Тест 8: Проверка вычисляемых спредов и алиасов в EconomicsParams."""
    from src.twin.params import EconomicsParams

    p = EconomicsParams(
        price_crude_oil=41500.0,
        price_straight_run=52000.0,
        price_godt=68000.0,
        y_liq=0.98,
    )
    assert p.margin_spread == 16000.0
    assert p.straight_to_godt_spread == 16000.0
    assert p.crude_to_straight_spread == 10500.0
    assert abs(p.crude_to_godt_spread - ((68000.0 * 0.98) - 41500.0)) < 1e-6
    assert p.product_diesel_rub_ton == 68000.0
    assert p.straight_run_diesel_rub_ton == 52000.0
    assert p.crude_oil_rub_ton == 41500.0


def test_margin_model_net_margin_and_opex():
    """Тест 9: Расчет OPEX и чистой операционной маржи (Net Margin) в MarginModel."""
    from src.agents.economics import MarginModel
    from src.twin.params import EconomicsParams, ReactorParams

    model = MarginModel(EconomicsParams(), ReactorParams())
    gross = model.calc_hourly_gross_margin(219.6)
    assert gross > 3_000_000.0

    opex = model.calc_hourly_operating_costs(219.6, 363.3, 3.922, 93309.0, 363.65)
    assert "furnace_mwh_h" in opex
    assert opex["total_opex_rub_h"] > 100_000.0

    net = model.calc_hourly_net_margin(219.6, 363.3, 3.922, 93309.0, 363.65)
    assert net == round(gross - opex["total_opex_rub_h"], 2)


def test_inverted_crack_spread_optimization(quality_risk_tags):
    """Тест 10: При инвертированном спреде (убыточная переработка) агент не увеличивает расход сырья."""
    from src.agents.graph import build_mvp_graph

    graph = build_mvp_graph()
    # Чистый режим (низкая сера 7.0 ppm)
    clean_tags = dict(quality_risk_tags)
    clean_tags["HT_Q21"] = 7.0
    clean_tags["LIMS_HT_S"] = 7.0

    result = graph.invoke({
        "tags": clean_tags,
        "economics": {
            "price_godt": 40000.0,
            "price_straight_run": 52000.0,
        },
    })
    rec = result.get("final_recommendation")
    assert rec is not None
    # При инвертированном спреде увеличение сырья (HT_FEED_SP +5) не должно рекомендоваться
    assert rec.recommended_delta_u.get("HT_FEED_SP", 0.0) <= 0.0

