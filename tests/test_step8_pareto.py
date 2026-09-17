"""Тесты Парето-анализа допустимых кандидатов (test_step8_pareto.py): Критерий 4 и п. 6.5 ТЗ.

Проверяет:
1. Ядро: отношение доминирования, недоминируемая сортировка против полного перебора,
   аналитический фронт ZDT1 (Zitzler E., Deb K., Thiele L. // Evol. Comput. 2000. V. 8, № 2), дубликаты;
2. Safety Ladder: ветированный кандидат не входит во фронт и не доминирует допустимые;
3. Метрики: чистая маржа за вычетом барьера ПАЗ, запас серы 2σ как у QualityAgent (tz:598), переочистка,
   WABT, неполные данные; ближайшие альтернативы на фронте и цена перехода;
4. Граф LangGraph на роллауте двойника: рекомендация арбитража лежит на фронте, сера согласована
   с QualityAgent, раздел XAI, журнал решений, REST API;
5. Визуализация Plotly и производительность.
"""

from __future__ import annotations

import json
import math
import time
import uuid

import numpy as np
import pytest
from scipy.stats import norm

from src.agents.decision_log import append_decision
from src.agents.graph import build_mvp_graph
from src.agents.limits import GIVEAWAY_Z, QUALITY_Z, SIGMA_S0_PPM
from src.agents.pareto import (
    ALL_METRICS,
    BED_TEMPERATURE,
    DEFAULT_OBJECTIVES,
    NET_MARGIN,
    ObjectiveSpec,
    ParetoAnalysis,
    SULFUR_GIVEAWAY,
    SULFUR_UCB,
    analyze_pareto,
    dominates,
    format_pareto_summary,
    node_pareto,
    non_dominated_sort,
    pareto_front_indices,
    sulfur_measurement_age,
    to_minimization,
    trade_off_alternatives,
)
from src.agents.state import ControlCandidate, SafetyAuditReport


# =============================================================================
# Вспомогательные функции
# =============================================================================

def brute_force_front(vectors) -> set:
    """Эталон: точка на фронте, если ни одна другая ее не доминирует (полный перебор)."""
    return {i for i, v in enumerate(vectors) if not any(dominates(u, v) for j, u in enumerate(vectors) if j != i)}


def make_cand(cid: str, margin: float, sulfur: float, wabt: float, dp: float = 177.0, is_hold: bool = False) -> ControlCandidate:
    return ControlCandidate(
        candidate_id=cid,
        delta_u={} if is_hold else {"HT_TIN_SP": 1.0},
        is_hold=is_hold,
        expected_margin=margin,
        steady_state={"HT_S_PRODUCT": sulfur, "HT_BED_MEAN": wabt, "HT_DP_KPA": dp},
    )


@pytest.fixture
def graph():
    return build_mvp_graph()


def invoke(graph, tags):
    return graph.invoke({"tags": tags, "session_id": f"test_pareto_{uuid.uuid4().hex}"})


# =============================================================================
# 1. Ядро алгоритма
# =============================================================================

def test_dominance_relation_basics():
    """Доминирование: строго лучше хотя бы по одной цели и не хуже по остальным; равенство не доминирует."""
    assert dominates((1.0, 2.0), (1.0, 3.0))
    assert not dominates((1.0, 3.0), (1.0, 2.0))
    assert not dominates((1.0, 2.0), (1.0, 2.0))
    assert not dominates((0.0, 5.0), (1.0, 2.0)) and not dominates((1.0, 2.0), (0.0, 5.0))
    # Зона неразличимости: разница 0.05 при tol=0.1 не делает точку лучше
    assert not dominates((1.0, 1.95), (1.0, 2.0), tol=0.1)
    # Цель max приводится к минимизации сменой знака
    assert to_minimization((5000.0, 9.0), ("max", "min")) == (-5000.0, 9.0)


@pytest.mark.parametrize("seed, n, m, integer", [(0, 60, 2, False), (1, 60, 3, False), (2, 40, 4, False), (3, 80, 3, True)])
def test_non_dominated_sort_matches_brute_force(seed, n, m, integer):
    """Каждый слой сортировки Deb (2002) равен фронту полного перебора на оставшихся точках (включая ничьи)."""
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 5, size=(n, m)) if integer else rng.random((n, m))
    vectors = [tuple(float(x) for x in row) for row in data]

    fronts = non_dominated_sort(vectors)
    assert sorted(i for layer in fronts for i in layer) == list(range(n))

    remaining = list(range(n))
    for layer in fronts:
        sub = [vectors[i] for i in remaining]
        expected = {remaining[k] for k in brute_force_front(sub)}
        assert set(layer) == expected
        remaining = [i for i in remaining if i not in expected]
    assert remaining == []


def test_zdt1_analytic_front_recovered():
    """ZDT1: f2 = g·(1 − √(f1/g)); оптимальные точки (g = 1) образуют фронт, точки с g > 1 доминируются."""
    f1_grid = np.linspace(0.0, 1.0, 21)
    optimal = [(float(f1), float(1.0 - math.sqrt(f1))) for f1 in f1_grid]
    dominated = []
    for g in (1.3, 2.0, 4.5):
        dominated += [(float(f1), float(g * (1.0 - math.sqrt(f1 / g)))) for f1 in f1_grid]

    vectors = dominated[:30] + optimal + dominated[30:]
    front = pareto_front_indices(vectors)
    assert sorted(vectors[i] for i in front) == sorted(optimal)
    assert len(non_dominated_sort(vectors)) == 4  # g = 1 < 1.3 < 2.0 < 4.5


def test_duplicates_share_front_and_empty_input():
    """Совпадающие векторы взаимно не доминируются; пустое множество дает пустой фронт."""
    assert pareto_front_indices([(1.0, 1.0), (1.0, 1.0), (2.0, 2.0)]) == [0, 1]
    assert non_dominated_sort([]) == []


def test_trade_off_alternatives_cheapest_improvement_per_axis():
    """
    Альтернативы берутся только с фронта: по каждой оси — точка, улучшающая ось с наименьшей потерей маржи (без весов).
    Более чистый по сере, но в остальном не лучший режим доминируется (переочистка не цель) и не предлагается.
    """
    cands = [
        make_cand("cand_sel", 5_000.0, 8.2, 366.0),
        make_cand("cand_safe", 4_000.0, 7.9, 365.8),
        make_cand("cand_safe_dominated", 1_000.0, 7.6, 365.8),
        make_cand("cand_cool", 3_000.0, 8.3, 364.0),
        make_cand("cand_cool_cheap", 2_000.0, 8.3, 364.0),
        make_cand("cand_unsafe", 9_000.0, 7.0, 360.0),
    ]
    res = analyze_pareto(cands, vetoed_ids=["cand_unsafe"])
    assert res.point("cand_safe_dominated").status == "dominated"
    alts = {a.kind: a for a in trade_off_alternatives(res, "cand_sel")}

    safer = alts["safer_sulfur"]
    assert safer.candidate_id == "cand_safe"  # ветированный и доминируемый не предлагаются
    assert safer.delta_margin_rub_h == pytest.approx(-1_000.0)
    assert safer.delta_sulfur_ppm == pytest.approx(-0.3)
    assert safer.risk_to_pct < safer.risk_from_pct

    gentler = alts["gentler_catalyst"]
    assert gentler.candidate_id == "cand_safe"  # среди режимов с меньшим WABT потеря маржи минимальна
    assert gentler.delta_wabt_c == pytest.approx(-0.2)
    assert trade_off_alternatives(res, "cand_unsafe") == []  # для ветированного альтернатив не строим


# =============================================================================
# 2–3. Доменный слой: Safety Ladder и метрики
# =============================================================================

def test_vetoed_candidate_never_enters_or_dominates_front():
    """Safety Ladder: кандидат с лучшими метриками, но с вето ПАЗ, не входит во фронт и не доминирует допустимые."""
    cands = [
        make_cand("cand_unsafe", margin=90_000.0, sulfur=5.0, wabt=360.0),
        make_cand("cand_a", margin=3_000.0, sulfur=8.0, wabt=366.0),
        make_cand("cand_b", margin=1_000.0, sulfur=7.0, wabt=364.0),
    ]
    reports = [SafetyAuditReport(candidate_id="cand_unsafe", is_vetoed=True, agent="reliability", violation_reason="ESD_VETO: T55=388.0°C")]
    res = analyze_pareto(cands, vetoed_ids=["cand_unsafe"], audit_reports=reports)

    unsafe = res.point("cand_unsafe")
    assert unsafe.status == "vetoed" and unsafe.rank is None
    assert unsafe.veto_reasons == ["ESD_VETO: T55=388.0°C"]
    assert set(res.front_ids) == {"cand_a", "cand_b"}
    assert all(res.point(c).dominated_by == [] for c in res.front_ids)
    assert res.n_admissible == 2


def test_dominated_candidate_ranked_with_dominators():
    """Доминируемый кандидат получает статус dominated, слой 2 и список доминирующих."""
    cands = [
        make_cand("cand_hold", margin=0.0, sulfur=9.0, wabt=363.3, is_hold=True),
        make_cand("cand_good", margin=2_000.0, sulfur=8.5, wabt=363.3),
        make_cand("cand_bad", margin=1_500.0, sulfur=8.9, wabt=364.0),
    ]
    res = analyze_pareto(cands)
    bad = res.point("cand_bad")
    assert res.front_ids == ["cand_good"]
    assert bad.status == "dominated" and bad.rank == 2 and bad.dominated_by == ["cand_good"]
    assert res.point("cand_hold").dominated_by == ["cand_good"]
    assert res.ideal == pytest.approx({"net_margin": 2000.0, "sulfur_giveaway": 0.0, "bed_temperature": 363.3}, abs=1e-3)
    assert res.nadir == res.ideal  # фронт из одной точки


def test_net_margin_subtracts_reliability_barrier_penalty():
    """Чистая маржа = маржа − барьер ПАЗ: дорогой по риску вариант доминируется безопасным при равных прочих."""
    cands = [make_cand("cand_risky", 5_000.0, 8.0, 364.0), make_cand("cand_calm", 4_000.0, 8.0, 364.0)]
    res = analyze_pareto(cands, risk_penalties={"cand_risky": 3_000.0})
    assert res.point("cand_risky").metrics["net_margin"] == pytest.approx(2_000.0)
    assert res.front_ids == ["cand_calm"]
    assert res.point("cand_risky").dominated_by == ["cand_calm"]


@pytest.mark.parametrize("age_h", [0.0, 6.0, 26.0])
def test_sulfur_ucb_giveaway_and_offspec_probability(age_h):
    """Сера: Ŝ + 2σ0·√(1 + age/12) (tz:598, ADR-12); переочистка ниже 10 − GIVEAWAY_Z·σ; P(S > 10) — хвост N(Ŝ, σ)."""
    sigma = SIGMA_S0_PPM * math.sqrt(1.0 + age_h / 12.0)
    res = analyze_pareto([make_cand("cand_x", 0.0, 9.2, 364.0), make_cand("cand_clean", -500.0, 5.0, 364.0)], sulfur_age_hours=age_h)
    point, clean = res.point("cand_x"), res.point("cand_clean")

    assert QUALITY_Z == 2.0
    assert res.sulfur_offset_ppm == pytest.approx(2.0 * sigma, abs=1e-4)
    assert point.metrics["sulfur_ucb"] == pytest.approx(9.2 + 2.0 * sigma, abs=1e-4)
    assert point.p_offspec == pytest.approx(norm.sf(10.0, loc=9.2, scale=sigma), abs=1e-6)
    assert res.giveaway_boundary_ppm == pytest.approx(10.0 - GIVEAWAY_Z * sigma, abs=1e-4)
    assert point.metrics["sulfur_giveaway"] == pytest.approx(max(0.0, res.giveaway_boundary_ppm - 9.2), abs=1e-4)
    assert clean.metrics["sulfur_giveaway"] == pytest.approx(res.giveaway_boundary_ppm - 5.0, abs=1e-4)


def test_sulfur_measurement_age_follows_quality_agent():
    """Достоверный HT_Q21 → возраст 0; NaN, клампинг 307/313 или отсутствие тега → возраст ЛИМС."""
    assert sulfur_measurement_age({"HT_Q21": 8.4}, 5.0) == 0.0
    assert sulfur_measurement_age({"HT_Q21": float("nan")}, 5.0) == 5.0
    assert sulfur_measurement_age({"HT_Q21": 307.0}, 5.0) == 5.0
    assert sulfur_measurement_age({"HT_Q21": 313.0}, 5.0) == 5.0
    assert sulfur_measurement_age(None, 5.0) == 5.0


def test_node_pareto_reads_lims_age_from_raw_telemetry():
    """Узел графа: при недоступном HT_Q21 буфер серы расширяется по возрасту ЛИМС из raw_telemetry."""
    from src.agents.state import RawTelemetry

    state = {
        "tags": {"HT_Q21": float("nan")},
        "raw_telemetry": RawTelemetry(timestamp="2026-09-16T10:00:00", P52=0.045, D10=840.0, lims_age_hours=12.0),
        "candidates": [make_cand("cand_x", 0.0, 8.0, 364.0)],
    }
    analysis = node_pareto(state)["pareto"]
    assert isinstance(analysis, ParetoAnalysis)
    assert analysis.sulfur_age_hours == 12.0
    assert analysis.sulfur_offset_ppm == pytest.approx(QUALITY_Z * SIGMA_S0_PPM * math.sqrt(2.0), abs=1e-4)
    assert analysis.sulfur_sigma_ppm == pytest.approx(SIGMA_S0_PPM * math.sqrt(2.0), abs=1e-4)


def test_wabt_fallback_and_incomplete_legacy_candidate():
    """WABT = (T_in + T_out)/2 без HT_BED_MEAN; скалярный кандидат без WABT — incomplete вне ранжирования."""
    cand_io = ControlCandidate(
        candidate_id="cand_io", expected_margin=100.0,
        steady_state={"HT_S_PRODUCT": 8.0, "HT_T_IN": 363.0, "HT_T_OUT": 367.0, "HT_DP_KPA": 177.0},
    )
    legacy = ControlCandidate(candidate_id="cand_legacy", expected_margin=500.0, expected_sulfur=8.0, expected_w10=1.8)
    res = analyze_pareto([cand_io, legacy])

    assert res.point("cand_io").metrics["bed_temperature"] == pytest.approx(365.0)
    leg = res.point("cand_legacy")
    assert leg.status == "incomplete" and leg.rank is None
    assert leg.metrics["reactor_dp"] == pytest.approx(1.8 * 98.0665, abs=1e-3)
    assert res.front_ids == ["cand_io"]


def test_all_candidates_vetoed_gives_empty_front():
    """Все варианты ветированы → фронт пуст, компромисса нет, XAI сообщает о пустом фронте."""
    cands = [make_cand("cand_1", 1.0, 11.0, 364.0), make_cand("cand_2", 2.0, 12.0, 365.0)]
    res = analyze_pareto(cands, vetoed_ids=["cand_1", "cand_2"])
    assert res.front_ids == [] and res.ideal == {}
    assert "Парето-фронт пуст" in format_pareto_summary(res)


def test_objective_set_is_configurable_and_validated():
    """ΔP подключается четвертой целью; неизвестная или пустая цель — ошибка конфигурации."""
    cands = [make_cand("cand_a", 1_000.0, 8.0, 364.0, dp=180.0), make_cand("cand_b", 1_000.0, 8.0, 364.0, dp=170.0)]
    assert set(analyze_pareto(cands).front_ids) == {"cand_a", "cand_b"}  # ΔP не цель по умолчанию
    assert analyze_pareto(cands, objectives=ALL_METRICS).front_ids == ["cand_b"]
    assert [o.key for o in DEFAULT_OBJECTIVES] == [NET_MARGIN.key, SULFUR_GIVEAWAY.key, BED_TEMPERATURE.key]
    assert SULFUR_UCB in ALL_METRICS  # контролируемая метрика, не цель

    with pytest.raises(ValueError):
        analyze_pareto(cands, objectives=(ObjectiveSpec(key="unknown", label="?", unit="", sense="min"),))
    with pytest.raises(ValueError):
        analyze_pareto(cands, objectives=())


# =============================================================================
# 4. Граф LangGraph на роллауте цифрового двойника
# =============================================================================

@pytest.mark.parametrize("fixture_name", ["nominal_tags", "quality_risk_tags", "rich_front_tags"])
def test_graph_pareto_invariants_on_twin_rollout(graph, request, fixture_name):
    """
    Инварианты на реальных кандидатах двойника:
    - фронт ⊆ допустимых, ветированных во фронте нет, каждый доминируемый действительно доминируется;
    - рекомендация арбитража лежит на фронте: единственный максимум Net Utility по допустимым ходам
      Парето-оптимален, а hold не может его доминировать (в SUCCESS Net Utility ≥ deadband > 0,
      в SUCCESS_CORRECTIVE hold ветирован);
    - буфер серы совпадает с запасом QualityAgent: sulfur_ucb = 10 − margin(HT_S_PRODUCT).
    """
    res = invoke(graph, request.getfixturevalue(fixture_name))
    analysis = res["pareto"]
    assert isinstance(analysis, ParetoAnalysis)

    cand_ids = [c.candidate_id for c in res["candidates"]]
    vetoed = set(res.get("vetoed_candidates", []))
    assert [p.candidate_id for p in analysis.points] == cand_ids
    assert set(analysis.front_ids) <= set(cand_ids) - vetoed
    assert all(analysis.point(v).status == "vetoed" for v in vetoed)

    keys = [o.key for o in analysis.objectives]
    senses = [o.sense for o in analysis.objectives]
    vec = {p.candidate_id: to_minimization([p.metrics[k] for k in keys], senses) for p in analysis.points if p.status in ("pareto", "dominated")}
    for p in analysis.points:
        if p.status == "dominated":
            assert p.dominated_by and all(dominates(vec[d], vec[p.candidate_id]) for d in p.dominated_by)
        if p.status == "pareto":
            assert not any(dominates(v, vec[p.candidate_id]) for v in vec.values())

    selected = res.get("selected_candidate")
    if selected is not None:
        assert analysis.is_on_front(selected.candidate_id)

    for rep in res["audit_reports"]:
        if rep.agent == "quality" and "HT_S_PRODUCT" in rep.limit_margins and rep.candidate_id not in vetoed:
            assert analysis.point(rep.candidate_id).metrics["sulfur_ucb"] == pytest.approx(10.0 - rep.limit_margins["HT_S_PRODUCT"], abs=1e-3)


@pytest.fixture
def rich_front_tags(nominal_tags):
    """Запас по сере (HT_Q21 = 7.0 ppm): допустимы почти все ходы, фронт содержит компромиссы."""
    tags = dict(nominal_tags)
    tags["HT_Q21"] = 7.0
    tags["LIMS_HT_S"] = 7.0
    return tags


def test_graph_rich_front_has_tradeoffs(graph, rich_front_tags):
    """При запасе по качеству фронт содержит компромиссы: самый доходный режим не лучший по другой цели."""
    analysis = invoke(graph, rich_front_tags)["pareto"]
    assert analysis.n_admissible >= 3
    front = [analysis.point(c) for c in analysis.front_ids]
    assert len(front) >= 2
    best_margin = max(front, key=lambda p: p.metrics["net_margin"])
    other_keys = [o.key for o in analysis.objectives if o.key != "net_margin"]
    assert any(
        min(p.metrics[k] for p in front) < best_margin.metrics[k] for k in other_keys
    )  # экономика конфликтует с переочисткой или износом катализатора


def test_graph_xai_report_and_decision_log_contain_pareto(graph, quality_risk_tags, tmp_path):
    """Раздел XAI сохраняется после перегенерации отчета блендингом; журнал решений содержит фронт."""
    res = invoke(graph, quality_risk_tags)
    rec = res["final_recommendation"]
    assert rec.status.startswith("SUCCESS")
    assert "Парето-анализ" in rec.markdown_report
    assert f"Рекомендация `{res['selected_candidate'].candidate_id}` — **Парето-оптимальное (компромиссное) решение в допустимой зоне**" in rec.markdown_report

    log_path = append_decision(res, {"tags": quality_risk_tags}, path=tmp_path / "decisions.jsonl")
    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["pareto"]["front_ids"] == res["pareto"].front_ids
    assert len(record["pareto"]["points"]) == len(res["candidates"])


def test_graph_degraded_data_skips_pareto(graph, degraded_tags):
    """Деградация КИП/LIMS → Safe Hold до оптимизации: Парето-анализ не выполняется."""
    res = invoke(graph, degraded_tags)
    assert res["final_recommendation"].status == "SAFE_HOLD"
    assert res.get("pareto") is None


def test_api_returns_pareto(quality_risk_tags):
    """REST API /api/v1/optimize возвращает сериализованный Парето-анализ."""
    from fastapi.testclient import TestClient
    from main import app

    data = TestClient(app).post("/api/v1/optimize", json={"tags": quality_risk_tags}).json()
    assert data["pareto"] is not None
    assert isinstance(data["pareto"]["front_ids"], list) and data["pareto"]["front_ids"]
    assert {o["key"] for o in data["pareto"]["objectives"]} == {"net_margin", "sulfur_giveaway", "bed_temperature"}


# =============================================================================
# 5. Визуализация и производительность
# =============================================================================

@pytest.fixture
def synthetic_analysis() -> ParetoAnalysis:
    cands = [
        make_cand("cand_hold", 0.0, 8.6, 363.3, is_hold=True),
        make_cand("cand_fast", 70_000.0, 9.4, 363.2, dp=184.0),
        make_cand("cand_hot", -2_900.0, 7.9, 365.3),
        make_cand("cand_bad", -3_000.0, 8.7, 365.4),
        make_cand("cand_unsafe", 90_000.0, 10.9, 366.0),
    ]
    return analyze_pareto(cands, vetoed_ids=["cand_unsafe"])


def test_plotly_figures_render_statuses_and_selection(synthetic_analysis):
    """3D, 2D и параллельные координаты: слои статусов, точка арбитража, норматив по сере, все метрики."""
    pytest.importorskip("plotly")
    from src.agents.pareto import build_parallel_coordinates_figure, build_pareto_2d_figure, build_pareto_3d_figure

    fig3d = build_pareto_3d_figure(synthetic_analysis, selected_id="cand_fast")
    names3d = {t.name for t in fig3d.data}
    assert {"Парето-фронт", "Доминируемые (допустимые)", "Отклонены вето ПАЗ/ГОСТ", "Текущий режим (hold)", "Рекомендация арбитража"} <= names3d
    sel_trace = next(t for t in fig3d.data if t.name == "Рекомендация арбитража")
    assert list(sel_trace.z) == [70_000.0]

    fig2d = build_pareto_2d_figure(synthetic_analysis, selected_id="cand_fast", x="sulfur_ucb", y="net_margin")
    assert any(t.name == "Граница проекции (2 метрики)" for t in fig2d.data)
    assert any(getattr(s, "x0", None) == 10.0 for s in fig2d.layout.shapes)

    figpc = build_parallel_coordinates_figure(synthetic_analysis, selected_id="cand_fast")
    assert len(figpc.data[0].dimensions) == len(ALL_METRICS)
    assert 3 in list(figpc.data[0].line.color)

    # Пустой фронт отрисовывается без исключений
    empty = analyze_pareto([make_cand("cand_x", 1.0, 11.0, 364.0)], vetoed_ids=["cand_x"])
    build_pareto_3d_figure(empty)
    build_pareto_2d_figure(empty)
    build_parallel_coordinates_figure(empty)


def test_analyze_pareto_latency_p95():
    """Производительность: p95 analyze_pareto на 25 кандидатах < 5 мс (бюджет графа — 150 мс)."""
    rng = np.random.default_rng(7)
    cands = [
        make_cand(f"cand_{i:02d}", float(rng.normal(0, 3000)), float(rng.uniform(6, 10)), float(rng.uniform(360, 367)), float(rng.uniform(165, 190)))
        for i in range(25)
    ]
    analyze_pareto(cands)
    latencies = []
    for _ in range(200):
        t0 = time.perf_counter()
        analyze_pareto(cands, vetoed_ids=["cand_03", "cand_07"])
        latencies.append(time.perf_counter() - t0)
    assert np.percentile(latencies, 95) < 0.005
