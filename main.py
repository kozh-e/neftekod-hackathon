"""FastAPI сервис для мультиагентной системы управления технологическим комплексом.

Предоставляет REST API (T6.4):
- POST /api/v1/optimize: Запуск цикла оптимизации через LangGraph с фиксацией в decision_log
- GET /api/v1/health: Проверка работоспособности сервиса
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.decision_log import append_decision
from src.agents.graph import build_mvp_graph
from src.agents.state import RawTelemetry

app = FastAPI(
    title="Neftecode Closed-Loop MES/APC Multi-Agent System",
    description="Автономная мультиагентная система управления производством дизельного топлива Евро-5",
    version="2.0.0",
)

# Компилируем граф один раз при запуске сервиса
graph = build_mvp_graph()


class TelemetryPayload(BaseModel):
    """Схема входного запроса телеметрии технологического комплекса."""

    tags: Dict[str, Any] = Field(
        ...,
        description="Словарь показаний КИПиА и анализов LIMS",
        json_schema_extra={
            "example": {
                "timestamp": "2026-09-15T15:30:00",
                "HT_F9": 219.6,
                "HT_T6": 363.3,
                "HT_P13": 3.922,
                "HT_GOR": 360.0,
                "HT_Q21": 8.43,
                "lims_age_hours": 2.5,
            }
        },
    )
    session_id: Optional[str] = Field(default=None, description="Идентификатор сессии двойника")
    economics: Optional[Dict[str, float]] = Field(
        default=None,
        description="Опциональные параметры цен и тарифов для расчета маржи",
    )


class OptimizationResponse(BaseModel):
    """Схема ответа мультиагентной системы."""

    status: str
    explanation: str
    recommended_delta_u: Dict[str, float]
    markdown_report: Optional[str] = None
    blending_recipe: Optional[Dict[str, Any]] = None
    data_quality_status: Optional[str] = None
    confidence: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
    alternatives: List[Dict[str, Any]] = Field(default_factory=list)
    predictions: Optional[Dict[str, Any]] = None
    pareto: Optional[Dict[str, Any]] = Field(default=None, description="Парето-фронт допустимых кандидатов (src.agents.pareto)")
    economics: Optional[Dict[str, Any]] = Field(default=None, description="Расчетные crack-spreads и валовая маржа")



@app.get("/api/v1/health")
async def health_check() -> Dict[str, str]:
    """Проверка доступности сервиса."""
    return {
        "status": "healthy",
        "service": "neftecode-mas-api",
        "version": "2.0.0",
    }


@app.post("/api/v1/optimize", response_model=OptimizationResponse)
async def run_optimization_cycle(payload: TelemetryPayload):
    """
    Принимает текущий срез телеметрии КИПиА, запускает мультиагентный граф LangGraph
    и возвращает согласованную арбитражем рекомендацию с физическим XAI-обоснованием.
    """
    raw_tags = payload.tags
    if not isinstance(raw_tags, dict):
        raise HTTPException(status_code=400, detail="Поле 'tags' должно быть словарем тегов КИПиА.")

    tags_copy = raw_tags.copy()
    if "timestamp" not in tags_copy:
        tags_copy["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if "P52" not in tags_copy:
        tags_copy["P52"] = float(tags_copy.get("AVT_P52", 0.045))
    if "D10" not in tags_copy:
        tags_copy["D10"] = float(tags_copy.get("AVT_D10", 840.0))

    try:
        telemetry = RawTelemetry(**tags_copy)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Ошибка валидации телеметрии: {exc}")

    state_input: Dict[str, Any] = {
        "raw_telemetry": telemetry,
        "tags": tags_copy,
        "session_id": payload.session_id,
        "economics": payload.economics,
    }

    try:
        result = graph.invoke(state_input)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка графа вычислений: {exc}")

    # Запись в журнал решений
    append_decision(result, {"tags": raw_tags, "session_id": payload.session_id, "economics": payload.economics})

    final_rec = result.get("final_recommendation")
    data_quality = result.get("data_quality")
    blending_recipe = result.get("blending_recipe")

    recipe_dict = None
    if blending_recipe is not None:
        if hasattr(blending_recipe, "model_dump"):
            recipe_dict = blending_recipe.model_dump()
        elif isinstance(blending_recipe, dict):
            recipe_dict = blending_recipe

    status = final_rec.status if final_rec else "UNKNOWN"
    explanation = final_rec.explanation if final_rec else "Рекомендация не сформирована."
    delta_u = final_rec.recommended_delta_u if final_rec else {}
    report = getattr(final_rec, "markdown_report", None) if final_rec else None

    # Формирование блока прогнозов
    hold_pred = result.get("hold_prediction")
    sel_cand = result.get("selected_candidate")
    cand_pred = sel_cand.trajectory if sel_cand else None
    predictions = {
        "hold": hold_pred,
        "selected": cand_pred,
    }

    # Экономическая сводка спредов и валовой маржи
    econ_summary = None
    try:
        from src.twin.params import load_params
        from src.agents.economics import MarginModel

        econ_p = load_params()
        if payload.economics:
            for k, v in payload.economics.items():
                if hasattr(econ_p.economics, k):
                    setattr(econ_p.economics, k, float(v))
        mm = MarginModel(econ_p.economics, econ_p.reactor)
        f9_val = float(tags_copy.get("HT_F9", tags_copy.get("F26", econ_p.reactor.feed_ref)))
        econ_summary = {
            "crack_spreads": mm.crack_spreads,
            "hourly_gross_margin_rub_h": mm.calc_hourly_gross_margin(f9_val),
            "expected_candidate_margin_rub_h": sel_cand.expected_margin if sel_cand else 0.0,
        }
    except Exception:
        pass

    return OptimizationResponse(
        status=status,
        explanation=explanation,
        recommended_delta_u=delta_u,
        markdown_report=report,
        blending_recipe=recipe_dict,
        data_quality_status=data_quality.status_code if data_quality else None,
        confidence=result.get("confidence"),
        warnings=result.get("twin_warnings", []),
        alternatives=result.get("alternatives", []),
        predictions=predictions,
        pareto=result["pareto"].model_dump(mode="json") if result.get("pareto") is not None else None,
        economics=econ_summary,
    )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
