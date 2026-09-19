"""Тесты раздачи статики пульта оператора FastAPI."""

import json
from fastapi.testclient import TestClient


def test_console_html_served(client: TestClient):
    """GET /console/ отдает index.html со статус-кодом 200."""
    response = client.get("/console/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "ПУЛЬТ ОПЕРАТОРА" in response.text or "console" in response.text.lower()
    assert "status-bar" in response.text
    assert "left-column" in response.text


def test_console_static_assets_served(client: TestClient):
    """GET статических ресурсов (CSS, JS, vendor) отдает 200."""
    assets = [
        "/console/styles.css",
        "/console/app.js",
        "/console/charts.js",
        "/console/api.js",
        "/console/vendor/echarts.min.js",
    ]
    for asset in assets:
        resp = client.get(asset)
        assert resp.status_code == 200, f"Asset {asset} failed with {resp.status_code}"
        assert len(resp.content) > 0, f"Asset {asset} is empty"


def test_console_fixtures_served(client: TestClient):
    """GET фикстур через /console/fixtures/ отдает валидный JSON."""
    fixtures = [
        "/console/fixtures/advisory.json",
        "/console/fixtures/auto.json",
        "/console/fixtures/refusal.json",
        "/console/fixtures/preview_ok.json",
        "/console/fixtures/preview_blocked.json",
        "/console/fixtures/pareto.json",
        "/console/fixtures/xai.json",
    ]
    for fix in fixtures:
        resp = client.get(fix)
        assert resp.status_code == 200, f"Fixture {fix} returned {resp.status_code}"
        data = resp.json()
        assert isinstance(data, dict)
