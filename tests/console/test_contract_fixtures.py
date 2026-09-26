"""Тесты валидности фикстур пульта по контракту (R6)."""

from pathlib import Path
import pytest

from src.console.contracts import ConsoleState, ParetoFrontDTO, PreviewResult, XaiInfo

FIXTURES_DIR = Path("tests/fixtures/console")
STATIC_FIXTURES_DIR = Path("static/console/fixtures")


@pytest.mark.parametrize("name", ["advisory", "auto", "refusal"])
def test_console_state_fixtures_valid(name: str):
    file_path = FIXTURES_DIR / f"{name}.json"
    assert file_path.exists(), f"Фикстура {file_path} отсутствует"
    content = file_path.read_text(encoding="utf-8")
    state = ConsoleState.model_validate_json(content)
    assert state.schema_version == "1.0"
    assert state.session_id == "demo"


@pytest.mark.parametrize("name", ["preview_ok", "preview_blocked"])
def test_preview_fixtures_valid(name: str):
    file_path = FIXTURES_DIR / f"{name}.json"
    assert file_path.exists(), f"Фикстура {file_path} отсутствует"
    content = file_path.read_text(encoding="utf-8")
    res = PreviewResult.model_validate_json(content)
    assert isinstance(res.can_commit, bool)


def test_pareto_fixture_valid():
    file_path = FIXTURES_DIR / "pareto.json"
    assert file_path.exists(), f"Фикстура {file_path} отсутствует"
    content = file_path.read_text(encoding="utf-8")
    dto = ParetoFrontDTO.model_validate_json(content)
    assert len(dto.objectives) >= 2
    assert len(dto.points) >= 1


def test_xai_fixture_valid():
    file_path = FIXTURES_DIR / "xai.json"
    assert file_path.exists(), f"Фикстура {file_path} отсутствует"
    content = file_path.read_text(encoding="utf-8")
    dto = XaiInfo.model_validate_json(content)
    assert len(dto.events) >= 1


def test_constants_fixture_valid():
    import json
    file_path = FIXTURES_DIR / "constants.json"
    assert file_path.exists(), f"Фикстура {file_path} отсутствует"
    content = file_path.read_text(encoding="utf-8")
    data = json.loads(content)
    assert isinstance(data, list)
    assert len(data) >= 30
    for c in data:
        assert "category" in c
        assert "key" in c
        assert "label" in c
        assert "value" in c
        assert "unit" in c
        assert "provenance" in c
        assert "source_ref" in c


@pytest.mark.parametrize(
    "name",
    [
        "advisory.json",
        "auto.json",
        "refusal.json",
        "preview_ok.json",
        "preview_blocked.json",
        "pareto.json",
        "xai.json",
        "constants.json",
    ],
)
def test_static_fixtures_are_identical_copies(name: str):
    src = FIXTURES_DIR / name
    dst = STATIC_FIXTURES_DIR / name
    assert src.exists()
    assert dst.exists()
    assert src.read_bytes() == dst.read_bytes(), f"{dst} отличается от {src}"
