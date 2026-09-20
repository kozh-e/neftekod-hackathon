"""FastAPI сервис для мультиагентной системы управления технологическим комплексом (API v3).

Предоставляет REST API:
- POST /api/v1/optimize: Запуск цикла оптимизации через детерминированный граф v3
- GET /api/v1/decisions/{cycle_id}: Получение полной трассы решения DecisionTrace
- GET /api/v1/policy: Получение активной версии технологической политики
- GET /api/v1/health: Проверка доступности сервиса
- Сторожевой таймер жесткого бюджета (10 с -> REFUSAL_TIMEOUT).
"""

from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.decision_store import DEFAULT_STORE
from src.agents.graph import build_core_graph
from src.agents.policy import DEFAULT_POLICY_STORE
from src.agents.state import RawTelemetry
from src.xai.card import TZ_REFUSAL_TIMEOUT

app = FastAPI(
    title="Neftecode Closed-Loop MES/APC Multi-Agent System",
    description="Автономная мультиагентная система управления производством дизельного топлива Евро-5",
    version="3.0.0",
)

from fastapi.staticfiles import StaticFiles
from src.console.api import router as console_router
app.include_router(console_router, prefix="/api/console")
app.mount("/console", StaticFiles(directory="static/console", html=True), name="console")

EXECUTOR = ThreadPoolExecutor(max_workers=4)
# Единый синглтон политики (src/agents/policy.py::DEFAULT_POLICY_STORE) — используется и
# живым контуром /api/v1/optimize, и супервизорским approve_change_request(), чтобы
# утверждённые изменения политики реально долетали до контура решений.
POLICY_STORE = DEFAULT_POLICY_STORE

# Скомпилированные графы
core_graph = build_core_graph()


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
    """Схема ответа мультиагентной системы v3 (аддитивно совместима с v2)."""
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
    pareto: Optional[Dict[str, Any]] = Field(default=None, description="Парето-фронт допустимых кандидатов")
    economics: Optional[Dict[str, Any]] = Field(default=None, description="Расчетные crack-spreads и валовая маржа")

    # Аддитивные поля v3
    card: Optional[Dict[str, Any]] = Field(default=None, description="7-блочная XAI-карточка решения ТЗ §5")
    kernel: Optional[Dict[str, Any]] = Field(default=None, description="Вердикт независимого ядра безопасности")
    recovery: Optional[Dict[str, Any]] = Field(default=None, description="Многошаговый план восстановления")
    prices: Optional[List[Dict[str, Any]]] = Field(default=None, description="Цены свойств гидрогенизата в блендинге")
    trace_id: Optional[str] = Field(default=None, description="Идентификатор трассы цикла cycle_id")


@app.get("/api/v1/health")
async def health_check() -> Dict[str, str]:
    """Проверка доступности сервиса."""
    return {
        "status": "healthy",
        "service": "neftecode-mas-api",
        "version": "3.0.0",
    }


@app.get("/api/v1/policy")
async def get_active_policy() -> Dict[str, Any]:
    """Возвращает текущую активную технологическую политику ядра."""
    return POLICY_STORE.active_policy.model_dump()


@app.get("/api/v1/decisions/{cycle_id}")
async def get_decision_by_id(cycle_id: str) -> Dict[str, Any]:
    """Возвращает полную трассу решения DecisionTrace из SQLite."""
    trace = DEFAULT_STORE.get(cycle_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Трасса решения с cycle_id '{cycle_id}' не найдена.")
    return trace.model_dump()


@app.post("/api/v1/optimize", response_model=OptimizationResponse)
async def run_optimization_cycle(payload: TelemetryPayload):
    """
    Принимает текущий срез телеметрии КИПиА, запускает мультиагентный граф
    со сторожевым таймером (10 с) и возвращает согласованную арбитражем рекомендацию.
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

    policy = POLICY_STORE.active_policy

    state_input: Dict[str, Any] = {
        "raw_telemetry": telemetry,
        "tags": tags_copy,
        "raw_tags": tags_copy,
        "session_id": payload.session_id,
        "economics": payload.economics,
        "policy": policy,
    }

    # Выполнение графа с контролем жесткого бюджета времени.
    # Ожидание идёт через asyncio.wrap_future, а не future.result(), чтобы не
    # блокировать event loop и не сериализовать параллельные запросы /optimize.
    future = EXECUTOR.submit(core_graph.invoke, state_input)
    try:
        result = await asyncio.wait_for(asyncio.wrap_future(future), timeout=policy.hard_budget_s)
    except TimeoutError:
        # Регламентный таймаут жесткого бюджета
        return OptimizationResponse(
            status="REFUSAL_TIMEOUT",
            explanation=TZ_REFUSAL_TIMEOUT,
            recommended_delta_u={},
            markdown_report=f"> [!CAUTION]\n> {TZ_REFUSAL_TIMEOUT}",
        )
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

    # Поля v3
    card_obj = result.get("card")
    card_dict = card_obj.__dict__ if hasattr(card_obj, "__dict__") else None
    kernel_obj = result.get("kernel")
    kernel_dict = kernel_obj.model_dump() if hasattr(kernel_obj, "model_dump") else None
    decision_obj = result.get("decision")
    recov_dict = decision_obj.recovery.model_dump() if (decision_obj and decision_obj.recovery) else None
    cycle_id = result.get("cycle", {}).get("cycle_id")

    # Цены блендинга
    prices_list = None
    sel_sig = getattr(decision_obj, "selected", None) if decision_obj else None
    if sel_sig and "blending" in result and sel_sig in result["blending"]:
        b_cert = result["blending"][sel_sig]
        prices_list = [p.model_dump() for p in getattr(b_cert, "prices", ())]

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
        card=card_dict,
        kernel=kernel_dict,
        recovery=recov_dict,
        prices=prices_list,
        trace_id=cycle_id,
    )


# =============================================================================
# REST API Эндпоинты LLM-супервизора (Этап P4)
# =============================================================================

class OperatorQuestionRequest(BaseModel):
    question: str = Field(..., description="Вопрос оператора технологической установки")


class DecisionActionRequest(BaseModel):
    user: str = Field(default="Инженер-технолог", description="ФИО или роль лица, принимающего решение")


@app.get("/api/v1/supervisor/findings")
async def get_supervisor_findings(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Возвращает список диагностических находок и предупреждений супервизора."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    return [f.model_dump() for f in DEFAULT_SUPERVISOR_STORE.list_findings(status=status)]


@app.get("/api/v1/supervisor/briefings")
async def get_supervisor_briefings(limit: int = 10) -> List[Dict[str, Any]]:
    """Возвращает список сводок технологических смен."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    return [b.model_dump() for b in DEFAULT_SUPERVISOR_STORE.list_briefings(limit=limit)]


@app.get("/api/v1/supervisor/change-requests")
async def get_supervisor_change_requests(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Возвращает список запросов на изменение технологической политики."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    return [r.model_dump() for r in DEFAULT_SUPERVISOR_STORE.list_change_requests(status=status)]


@app.post("/api/v1/supervisor/change-requests/{request_id}/approve")
async def approve_change_request(request_id: str, payload: DecisionActionRequest) -> Dict[str, Any]:
    """Утверждает запрос на изменение и активирует новую версию политики."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    success = DEFAULT_SUPERVISOR_STORE.approve_change_request(request_id, approved_by=payload.user)
    if not success:
        raise HTTPException(status_code=400, detail=f"Не удалось утвердить запрос {request_id}")
    return {"status": "APPROVED", "request_id": request_id, "decided_by": payload.user}


@app.post("/api/v1/supervisor/change-requests/{request_id}/reject")
async def reject_change_request(request_id: str, payload: DecisionActionRequest) -> Dict[str, Any]:
    """Отклоняет запрос на изменение политики."""
    from src.supervisor.store import DEFAULT_SUPERVISOR_STORE
    success = DEFAULT_SUPERVISOR_STORE.reject_change_request(request_id, rejected_by=payload.user)
    if not success:
        raise HTTPException(status_code=400, detail=f"Не удалось отклонить запрос {request_id}")
    return {"status": "REJECTED", "request_id": request_id, "decided_by": payload.user}


@app.post("/api/v1/supervisor/ask")
async def ask_supervisor(payload: OperatorQuestionRequest) -> Dict[str, Any]:
    """Консультация оператора по трассам решений и ограничениям установки."""
    from src.supervisor.cassettes import CassetteNotFoundError, LLMDisabledError
    from src.supervisor.service import DEFAULT_SUPERVISOR_SERVICE
    try:
        ans = await asyncio.wrap_future(EXECUTOR.submit(DEFAULT_SUPERVISOR_SERVICE.answer_operator, payload.question))
    except (CassetteNotFoundError, LLMDisabledError) as exc:
        # cassette_name больше не подменяет собой поиск по отпечатку запроса (см. аудит:
        # раньше на любой вопрос молча отдавался законсервированный demo-ответ). В
        # REPLAY_STRICT-режиме без кассеты под конкретный вопрос — явный отказ, а не
        # чужой правдоподобный ответ.
        raise HTTPException(status_code=503, detail=f"Супервизор недоступен для этого вопроса: {exc}")
    if not ans:
        raise HTTPException(status_code=500, detail="Супервизор не смог сформировать ответ")
    return ans.model_dump()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
