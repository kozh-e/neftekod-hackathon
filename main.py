"""FastAPI сервис для мультиагентной системы управления технологическим комплексом (Шаг 6 MVP).

Предоставляет REST API:
- POST /api/v1/optimize: Запуск цикла оптимизации через LangGraph
- GET /api/v1/health: Проверка работоспособности сервиса
"""

from __future__ import annotations

import datetime
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.state import RawTelemetry
from src.agents.graph import build_mvp_graph

app = FastAPI(
    title="Neftecode Closed-Loop MES/APC Multi-Agent System",
    description="Автономная мультиагентная система управления производством дизельного топлива Евро-5",
    version="1.0.0"
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
                "P52": 0.045,
                "D10": 840.0,
                "F15": 400.0,
                "T55": 380.0,
                "F5": 25.0,
                "F26": 80.0,
                "Sulfur": 8.2,
                "lims_age_hours": 2.5
            }
        }
    )


class OptimizationResponse(BaseModel):
    """Схема ответа мультиагентной системы."""
    status: str
    explanation: str
    recommended_delta_u: Dict[str, float]
    markdown_report: Optional[str] = None
    blending_recipe: Optional[Dict[str, Any]] = None
    data_quality_status: Optional[str] = None


@app.get("/api/v1/health")
async def health_check() -> Dict[str, str]:
    """Проверка доступности сервиса."""
    return {
        "status": "healthy",
        "service": "neftecode-mas-api",
        "version": "1.0.0"
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

    # Обеспечиваем наличие базовых полей для RawTelemetry
    tags_copy = raw_tags.copy()
    if "timestamp" not in tags_copy:
        tags_copy["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if "P52" not in tags_copy:
        tags_copy["P52"] = 0.045
    if "D10" not in tags_copy:
        tags_copy["D10"] = 840.0

    try:
        telemetry = RawTelemetry(**tags_copy)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Ошибка валидации телеметрии: {exc}")

    try:
        result = graph.invoke({"raw_telemetry": telemetry})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка графа вычислений: {exc}")

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

    return OptimizationResponse(
        status=status,
        explanation=explanation,
        recommended_delta_u=delta_u,
        markdown_report=report,
        blending_recipe=recipe_dict,
        data_quality_status=data_quality.status_code if data_quality else None
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
