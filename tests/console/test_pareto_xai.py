"""Тесты функций сборки Парето-фронта и XAI-журнала (Критерий 4, п. 6.5 ТЗ)."""

import pytest
from src.console.contracts import ParetoFrontDTO, XaiInfo
from src.console.runtime import ConsoleSession
from src.console.service import build_pareto, build_xai


def test_build_pareto_before_first_tick():
    session = ConsoleSession("test_empty_pareto")
    assert session.last_graph_result is None
    res = build_pareto(session)
    assert res is None


def test_build_xai_before_first_tick():
    session = ConsoleSession("test_empty_xai")
    assert session.last_graph_result is None
    res = build_xai(session)
    assert res is None


def test_build_pareto_with_active_session(s2_session: ConsoleSession):
    assert s2_session.last_graph_result is not None
    dto = build_pareto(s2_session)
    assert dto is not None
    assert isinstance(dto, ParetoFrontDTO)
    assert len(dto.objectives) >= 2
    assert len(dto.points) >= 1
    # хотя бы одна точка имеет статус pareto
    pareto_points = [p for p in dto.points if p.status == "pareto"]
    assert len(pareto_points) >= 1
    # сериализация без ошибок
    dumped = dto.model_dump_json()
    assert isinstance(dumped, str)
    reloaded = ParetoFrontDTO.model_validate_json(dumped)
    assert len(reloaded.points) == len(dto.points)


def test_build_xai_with_active_session(s2_session: ConsoleSession):
    assert s2_session.last_graph_result is not None
    dto = build_xai(s2_session)
    assert dto is not None
    assert isinstance(dto, XaiInfo)
    assert len(dto.events) >= 1
    dumped = dto.model_dump_json()
    assert isinstance(dumped, str)
    reloaded = XaiInfo.model_validate_json(dumped)
    assert len(reloaded.events) == len(dto.events)
