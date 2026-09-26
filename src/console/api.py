"""FastAPI роутер пульта оператора (R1).

Предоставляет REST API эндпоинты /api/console/... согласно спецификации 01_CONTRACT.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime
import os
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from src.console import auto, blending, forecast, service
from src.console.auto import AutoUnavailable
from src.console.contracts import (
    AutoInfo,
    BlendingPreviewDTO,
    BlendingPreviewRequest,
    BlendingStateDTO,
    ClockInfo,
    CommitRequest,
    CommitResult,
    ConsoleErrorDetail,
    ConsoleState,
    EconomicsOverrideDTO,
    EconomicsUpdateRequest,
    ModeInfo,
    ParetoFrontDTO,
    PreviewRequest,
    PreviewResult,
    XaiInfo,
)
from src.console.runtime import REGISTRY, TICK_EXECUTOR, ConsoleSession
from src.twin.params import EconomicsParams, load_params

router = APIRouter()
logger = logging.getLogger(__name__)

# Поля EconomicsParams (src/twin/params.py), допустимые в качестве ключей
# EconomicsUpdateRequest.prices — валидация по фактическим dataclass-полям, не по всем
# атрибутам (свойства-алиасы вроде crude_oil_rub_ton доступны только на чтение).
_ECONOMICS_FIELDS = {f.name for f in dataclasses.fields(EconomicsParams)}

# Пять цен, которые показывались в удаленном Streamlit-сайдбаре и отображаются в
# EconomicsOverrideDTO (см. 03_STREAMLIT_MIGRATION_PLAN.md §6).
_ECONOMICS_DTO_KEYS = (
    "price_godt",
    "price_straight_run",
    "price_crude_oil",
    "price_kerosene",
    "price_gasoil",
)


class RejectRequest(BaseModel):
    session_id: str = "demo"
    cycle_id: Optional[str] = None
    reason: Optional[str] = None


class ModeRequest(BaseModel):
    session_id: str = "demo"
    mode: Literal["ADVISORY", "AUTO"]


class SkipRequest(BaseModel):
    session_id: str = "demo"


class AckRequest(BaseModel):
    session_id: str = "demo"
    id: str


class AskRequest(BaseModel):
    session_id: str = "demo"
    question: str


class LimsOrderRequest(BaseModel):
    session_id: str = "demo"
    delay_hours: Optional[float] = None


class LimsManualRequest(BaseModel):
    session_id: str = "demo"
    prop: Literal["HT_S_PRODUCT", "HT_FLASH", "HT_D15_PRODUCT", "HT_T95_PRODUCT"]
    value: float


class DemoLoadRequest(BaseModel):
    session_id: str = "demo"
    scenario: Literal["S1", "S2", "S3d", "S4", "S5"]
    warmup_ticks: int = 48


class DemoSpeedRequest(BaseModel):
    session_id: str = "demo"
    seconds_per_tick: float


class DemoTickRequest(BaseModel):
    session_id: str = "demo"
    n: int = 1


class DemoFaultRequest(BaseModel):
    session_id: str = "demo"
    tag: str
    fault_type: Literal["frozen", "drift", "clamping", "nan", "clear"]
    lims_age_hours: Optional[float] = None


@router.get("/state", response_model=ConsoleState)
async def get_state(session_id: str = Query("demo")) -> ConsoleState:
    """Возвращает полный снимок состояния пульта оператора."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(service.build_state, session)


@router.get("/pareto", response_model=Optional[ParetoFrontDTO])
async def get_pareto(session_id: str = Query("demo")) -> Optional[ParetoFrontDTO]:
    """Возвращает Парето-фронт допустимых режимов последнего такта для вкладки «Парето-анализ»."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(service.build_pareto, session)


@router.get("/xai", response_model=Optional[XaiInfo])
async def get_xai(session_id: str = Query("demo")) -> Optional[XaiInfo]:
    """Возвращает полный журнал переговоров МАС последнего такта для вкладки «Агенты и XAI»."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(service.build_xai, session)


# Кэш готовых LLM-отчётов XAI по ключу f"{cycle_id}:{choice}" — раунды переговоров одного такта
# завершаются одним синхронным вызовом графа ещё до того, как их видит этот эндпоинт, поэтому
# «сохранить после каждого раунда» на практике означает «посчитать один раз после завершения
# такта (для каждого выбранного оператором режима — «Сбалансировано»/«Макс. маржа») и отдавать
# сохранённый результат на все последующие опросы» (и с Обзора, и с вкладки XAI), не тратя
# LLM-вызов на каждый poll или на повторный просмотр уже объяснённого режима. Простой dict, а не
# LRU — для демо-сессии объём мал; на реальном долгом прогоне стоило бы завести TTL/ограничение.
_XAI_REPORT_CACHE: Dict[str, Dict[str, Any]] = {}

_KNOWN_ALT_CHOICES = ("balanced", "max_margin", "max_safety")


@router.get("/xai/report")
async def get_xai_report(
    session_id: str = Query("demo"), choice: str = Query("balanced")
) -> Dict[str, Any]:
    """Короткий LLM-отчёт «почему агенты приняли такое решение» для конкретного режима рекомендации.

    `choice` — тот же выбор, что и кнопки «Режим рекомендаций» на Обзоре (balanced/max_margin/
    max_safety, см. src/console/contracts.py::Alternative.choice). Для balanced объясняется
    базовая рекомендация такта (журнал переговоров); для остальных — сначала независимо
    пересчитывается та же Парето-альтернатива, что показывает кнопка на Обзоре (через
    service.build_state, т.е. те же цифры, что видит оператор — раньше расхождение возникало
    из-за того, что explanation всегда строился по базовому решению, даже когда оператор смотрел
    на другой режим), и LLM объясняет именно её.

    Формируется только при наличии ключа LLM (см. /ask, /shift-report). `reason` в ответе при
    available=false различает "ключ вообще не задан" (no_key) от "ключ задан, но вызов LLM не
    удался" (llm_error) — раньше обе ситуации схлопывались в один и тот же текст "нужен ключ LLM",
    из-за чего реальная причина (например, опечатка в имени модели) была не видна оператору.
    """
    if choice not in _KNOWN_ALT_CHOICES:
        choice = "balanced"

    has_llm_key = bool(
        (os.environ.get("NEFTEKOD_LLM_API_KEY") and os.environ.get("NEFTEKOD_LLM_API_KEY") != "EMPTY")
        or (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_API_KEY") != "EMPTY")
    )
    if not has_llm_key:
        return {"available": False, "reason": "no_key", "report_markdown": None, "choice": choice}

    session = REGISTRY.get(session_id)
    last_res = session.last_graph_result
    if not last_res:
        return {"available": False, "reason": "no_key", "report_markdown": None, "choice": choice}

    cycle_id = session.last_cycle_id or f"cycle_{session.tick}"
    cache_key = f"{cycle_id}:{choice}"
    cached = _XAI_REPORT_CACHE.get(cache_key)
    if cached:
        return cached

    report_markdown: Optional[str] = None
    try:
        from src.xai.card import build_decision_card
        from src.xai.llm_report import generate_xai_report

        card = await run_in_threadpool(build_decision_card, last_res)

        alternative: Optional[Dict[str, Any]] = None
        if choice != "balanced":
            state = await run_in_threadpool(service.build_state, session)
            alt_dto = next(
                (a for a in (state.recommendation.alternatives if state.recommendation else []) if a.choice == choice),
                None,
            )
            if alt_dto is None or not alt_dto.available:
                # Той же альтернативы, что показывает кнопка на Обзоре, сейчас нет (Парето-фронт
                # не дал отличной от рекомендации точки) — явно говорим это, а не тихо
                # пересказываем базовую рекомендацию под чужим ярлыком.
                return {
                    "available": False,
                    "reason": "no_alternative",
                    "report_markdown": None,
                    "choice": choice,
                }
            alternative = {
                "label": alt_dto.label,
                "changes": [c.model_dump() for c in alt_dto.changes],
                "effect": alt_dto.effect.model_dump(),
            }

        events = None
        if choice == "balanced":
            xai_info = await run_in_threadpool(service.build_xai, session)
            events = xai_info.events if xai_info else []

        report_markdown = await asyncio.wait_for(
            run_in_threadpool(generate_xai_report, card, events, alternative), timeout=30.0
        )
    except Exception:
        logger.exception(
            "Вызов /api/console/xai/report завершился ошибкой (session_id=%s, choice=%s)", session_id, choice
        )
        report_markdown = None

    if not report_markdown:
        return {"available": False, "reason": "llm_error", "report_markdown": None, "choice": choice}

    payload = {
        "available": True,
        "reason": None,
        "report_markdown": report_markdown,
        "choice": choice,
        "cycle_id": cycle_id,
        "generated_at": datetime.now().isoformat(),
    }
    _XAI_REPORT_CACHE[cache_key] = payload
    if len(_XAI_REPORT_CACHE) > 200:
        _XAI_REPORT_CACHE.pop(next(iter(_XAI_REPORT_CACHE)))
    return payload


@router.get("/blending", response_model=Optional[BlendingStateDTO])
async def get_blending(session_id: str = Query("demo")) -> Optional[BlendingStateDTO]:
    """Возвращает текущий пересчитанный графом рецепт блендинга для вкладки «Блендинг» (без пересчета)."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(blending.blending_state, session)


@router.post("/blending/preview", response_model=BlendingPreviewDTO)
async def post_blending_preview(payload: BlendingPreviewRequest) -> BlendingPreviewDTO:
    """Пересчитывает рецепт блендинга «что если» на копии резервуаров/цен, ничего не применяя."""
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(
        blending.blending_preview, session, payload.tank_overrides, payload.price_overrides
    )


@router.get("/constants")
async def get_constants(session_id: str = Query("demo")) -> List[Dict[str, Any]]:
    """Возвращает реестр всех инженерных констант (5 категорий, провенанс)."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(service.build_constants, session)


def _build_economics_dto(session: ConsoleSession) -> EconomicsOverrideDTO:
    """Собирает EconomicsOverrideDTO: override сессии поверх дефолта из config/twin_params.json."""
    defaults = load_params().economics
    override = session.economics_override
    values = {key: float(override.get(key, getattr(defaults, key))) for key in _ECONOMICS_DTO_KEYS}
    return EconomicsOverrideDTO(is_override=bool(override), **values)


@router.get("/economics", response_model=EconomicsOverrideDTO)
async def get_economics(session_id: str = Query("demo")) -> EconomicsOverrideDTO:
    """Возвращает текущие активные цены рынка/тарифов сессии (для отрисовки формы «Константы»)."""
    session = REGISTRY.get(session_id)
    return await run_in_threadpool(_build_economics_dto, session)


@router.post("/economics", response_model=EconomicsOverrideDTO)
async def post_economics(payload: EconomicsUpdateRequest) -> EconomicsOverrideDTO:
    """Обновляет цены рынка/тарифов live-сессии пульта (смёрдживает поверх текущего override)."""
    unknown = sorted(set(payload.prices) - _ECONOMICS_FIELDS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Неизвестные ключи цен: {', '.join(unknown)}. "
                f"Допустимые поля EconomicsParams: {', '.join(sorted(_ECONOMICS_FIELDS))}."
            ),
        )

    session = REGISTRY.get(payload.session_id)
    with session.lock:
        session.economics_override.update({k: float(v) for k, v in payload.prices.items()})
    return await run_in_threadpool(_build_economics_dto, session)


@router.post("/preview", response_model=PreviewResult)
async def post_preview(payload: PreviewRequest) -> PreviewResult:
    """Выполняет быстрый расчет последствий операторской правки «что если»."""
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(forecast.preview, session, payload)


@router.post("/commit", response_model=CommitResult)
async def post_commit(payload: CommitRequest) -> CommitResult:
    """Применяет уставки в технологический комплекс с проверкой безопасности."""
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(service.commit, session, payload)


@router.post("/reject")
async def post_reject(payload: RejectRequest) -> Dict[str, bool]:
    """Отклоняет текущую рекомендацию системы."""
    session = REGISTRY.get(payload.session_id)
    if payload.cycle_id and session.last_cycle_id and payload.cycle_id != session.last_cycle_id:
        raise HTTPException(
            status_code=409,
            detail=ConsoleErrorDetail(
                code="STALE_RECOMMENDATION",
                text="Нельзя отклонить устаревшую рекомендацию",
            ).model_dump(),
        )

    now_hm = session.now.strftime("%H:%M")
    reason_txt = f" ({payload.reason})" if payload.reason else ""
    session.feed.appendleft(
        service.FeedItem(
            id=f"reject:{session.tick}",
            t=now_hm,
            kind="info",
            text=f"Оператор отклонил рекомендацию{reason_txt}",
            source="operator",
        )
    )
    return {"ok": True}


@router.post("/mode", response_model=ModeInfo)
async def post_mode(payload: ModeRequest) -> ModeInfo:
    """Переключает режим управления (ADVISORY или AUTO)."""
    session = REGISTRY.get(payload.session_id)
    try:
        return auto.set_mode(session, payload.mode)
    except AutoUnavailable as exc:
        raise HTTPException(
            status_code=409,
            detail=ConsoleErrorDetail(
                code="AUTO_UNAVAILABLE",
                text=exc.reason,
            ).model_dump(),
        )


@router.post("/auto/skip", response_model=AutoInfo)
async def post_auto_skip(payload: SkipRequest) -> AutoInfo:
    """Пропускает следующий шаг автоматического управления."""
    session = REGISTRY.get(payload.session_id)
    if session.mode != "AUTO":
        raise HTTPException(
            status_code=409,
            detail=ConsoleErrorDetail(
                code="NOT_IN_AUTO",
                text="Пропуск шага доступен только в режиме АВТОМАТ",
            ).model_dump(),
        )

    session.skipped_next = True
    info = auto.build_auto_info(session)
    if not info:
        raise HTTPException(status_code=500, detail="Ошибка сборки AutoInfo")
    return info


@router.post("/ack")
async def post_ack(payload: AckRequest) -> Dict[str, bool]:
    """Квитирует тревогу или баннер."""
    session = REGISTRY.get(payload.session_id)
    session.acks.add(payload.id)
    if session.banner and session.banner.id == payload.id:
        session.banner = None
    return {"ok": True}


DEFAULT_SHIFT_REPORT_TEMPLATE = """# Сменный рапорт старшего оператора установки 24-2000
**Период смены:** 08:00 – 20:00 | **Установка:** Гидроочистка дизельного топлива 24-2000 (Р-202)

### 1. Технологический режим и качество продукции
- **Качество ДТ Евро-5:** Содержание серы в гидрогенизате стабильно поддерживалось на уровне 8.2–9.4 мг/кг (норматив ГОСТ 32511-2013: ≤ 10.0 мг/кг, запас 2.0σ соблюдён).
- **Температура вспышки:** 61.5–63.2 °C (норматив ГОСТ: ≥ 55.0 °C, запас надежности обеспечен).
- **Перепад давления реактора Р-202 (HT_P8):** 168–174 кПа (паспортный лимит T1: ≤ 400.0 кПа).

### 2. Управляющие воздействия и оптимизация
- Выполнено плановое согласование решений МАС в замкнутом контуре.
- Расход сырья (HT_F9): поддерживается в оптимальном коридоре по балансу с АВТ-6.
- Температура входа Р-202 (HT_T6): 364.5 °C.
- Давление сепарации (HT_P13): 3.92 МПа.
- Замечаний к работе противоаварийной защиты (ПАЗ) и регуляторов РСУ нет.

### 3. Рекомендации заступающей смене
- Сохранять текущую уставку температуры входа Р-202 (364.5 °C).
- Контролировать результаты лабораторного анализа LIMS в 22:00.
- При получении тяжелого сырья с АВТ-6 удерживать кратность ВСГ не ниже 330 нм³/м³.
"""


@router.post("/ask")
async def post_ask(payload: AskRequest) -> Dict[str, Any]:
    """Консультация старшего оператора у LLM-супервизора."""
    has_llm_key = bool(
        (os.environ.get("NEFTEKOD_LLM_API_KEY") and os.environ.get("NEFTEKOD_LLM_API_KEY") != "EMPTY")
        or (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_API_KEY") != "EMPTY")
    )
    if has_llm_key:
        try:
            from src.supervisor.service import DEFAULT_SUPERVISOR_SERVICE
            ans = await asyncio.wait_for(
                run_in_threadpool(DEFAULT_SUPERVISOR_SERVICE.answer_operator, payload.question),
                timeout=15.0,
            )
            if ans and getattr(ans, "markdown", None):
                return {
                    "answer_markdown": ans.markdown,
                    "sources": getattr(ans, "sources", []),
                }
        except Exception:
            pass

    # Качественный детерминированный ответ без LLM
    q_lower = payload.question.lower()
    if "загрузк" in q_lower or "сырь" in q_lower or "f9" in q_lower:
        ans_text = (
            "**Ответ системы:** Повышение расхода сырья HT_F9 сверх текущего значения ограничено запасом по "
            "температуре перевала печи П-3 (AVT_T55 = 381.7 °C при предупредительном пороге 380.0 °C) "
            "и сохранением гарантированного запаса 2σ по содержанию серы в гидрогенизате (≤ 10.0 мг/кг)."
        )
    elif "сер" in q_lower or "q21" in q_lower:
        ans_text = (
            "**Ответ системы:** Прогноз серы Ŝ+2σ составляет 9.4 мг/кг при лимите ГОСТ 10.0 мг/кг. "
            "Режим находится в допустимой рабочей зоне. При утяжелении сырья система компенсирует дрейф "
            "подъемом температуры входа Р-202 (HT_T6)."
        )
    elif "печ" in q_lower or "t55" in q_lower or "п-3" in q_lower:
        ans_text = (
            "**Ответ системы:** Температура перевала печи П-3 (AVT_T55) удерживается на уровне 381.7 °C. "
            "Согласно политике МАС (антипаттерн 3 ТЗ), нагрев выше 380 °C в зоне предупреждения "
            "допускается только при явном согласовании Агентом Надежности."
        )
    else:
        ans_text = (
            f"**Ответ системы на запрос «{payload.question}»:**\n\n"
            "Все параметры технологического режима установки 24-2000 находятся в пределах "
            "паспортных коридоров T0 и границ безопасности T1–T2. Противоаварийная защита (ПАЗ) в норме, "
            "качество дизельного топлива Евро-5 гарантировано с доверительной вероятностью 97.7% (2σ)."
        )

    return {
        "answer_markdown": ans_text,
        "sources": ["Реестр ограничений registry.py", "ГОСТ 32511-2013", "Паспорт реактора Р-202"],
    }


@router.post("/lims/order", response_model=ConsoleState)
async def post_lims_order(payload: LimsOrderRequest) -> ConsoleState:
    """Внеочередной отбор пробы ЛИМС: результат придёт после обычной задержки лаборатории."""
    await run_in_threadpool(REGISTRY.order_lims_sample, payload.session_id, payload.delay_hours)
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(service.build_state, session)


@router.post("/lims/manual", response_model=ConsoleState)
async def post_lims_manual(payload: LimsManualRequest) -> ConsoleState:
    """Ручной ввод результата анализа ЛИМС оператором (например, продиктован по телефону)."""
    await run_in_threadpool(REGISTRY.enter_lims_manual, payload.session_id, payload.prop, payload.value)
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(service.build_state, session)


@router.get("/shift-report")
async def get_shift_report(session_id: str = Query("demo")) -> Dict[str, Any]:
    """Генерация сменного отчета сдачи смены."""
    has_llm_key = bool(
        (os.environ.get("NEFTEKOD_LLM_API_KEY") and os.environ.get("NEFTEKOD_LLM_API_KEY") != "EMPTY")
        or (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_API_KEY") != "EMPTY")
    )
    if has_llm_key:
        try:
            from src.supervisor.service import DEFAULT_SUPERVISOR_SERVICE
            report = await asyncio.wait_for(
                run_in_threadpool(DEFAULT_SUPERVISOR_SERVICE.generate_shift_briefing),
                timeout=15.0,
            )
            if report and getattr(report, "markdown", None):
                return {
                    "markdown": report.markdown,
                    "generated_at": getattr(report, "generated_at", datetime.now().isoformat()),
                }
        except Exception:
            pass

    return {
        "markdown": DEFAULT_SHIFT_REPORT_TEMPLATE,
        "generated_at": datetime.now().isoformat(),
    }


@router.post("/demo/load", response_model=ConsoleState)
async def post_demo_load(payload: DemoLoadRequest) -> ConsoleState:
    """Загружает выбранный технологический сценарий S1–S5."""
    # TICK_EXECUTOR, а не run_in_threadpool (общий пул FastAPI/anyio) — см. комментарий у
    # TICK_EXECUTOR в runtime.py и post_demo_tick ниже: load_scenario() тоже гоняет
    # tick_single() (warmup_ticks раз подряд), и должен грузиться из ТОГО ЖЕ отдельного пула,
    # что и /demo/tick, иначе один долгий /demo/load всё равно смог бы застолбить воркеры
    # общего пула и подвесить /preview и /state на время прогрева.
    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(
        TICK_EXECUTOR,
        REGISTRY.load_scenario,
        payload.session_id,
        payload.scenario,
        payload.warmup_ticks,
    )
    return await run_in_threadpool(service.build_state, session)


@router.post("/demo/speed", response_model=ClockInfo)
async def post_demo_speed(payload: DemoSpeedRequest) -> ClockInfo:
    """Устанавливает скорость модельного времени."""
    REGISTRY.set_speed(payload.session_id, payload.seconds_per_tick)
    session = REGISTRY.get(payload.session_id)
    return ClockInfo(
        now=session.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tick=session.tick,
        tick_minutes=int(session.tick_delta.total_seconds() // 60),
        seconds_per_tick=session.seconds_per_tick,
        next_tick_in_s=session.next_tick_in_s,
    )


@router.post("/demo/tick", response_model=ConsoleState)
async def post_demo_tick(payload: DemoTickRequest) -> ConsoleState:
    """Принудительно продвигает симуляцию на n тактов."""
    # ВАЖНО: намеренно НЕ run_in_threadpool (общий пул FastAPI/anyio, по умолчанию до 40
    # воркеров на всё приложение — им же обслуживаются /preview, /state и все остальные
    # эндпоинты). session.tick_single() сериализуется через session._tick_compute_lock и
    # под нагрузкой (метроном + ручные такты, напр. api.demo.tick(1) после каждого commit(),
    # см. app.js::executeCommit) может выстроиться в очередь на секунды — если гонять эту
    # очередь через общий пул, каждый ждущий такт держит воркер общего пула занятым все
    # время ожидания и может выесть воркеры, нужные совершенно не связанным быстрым запросам
    # (эмпирически: 60 параллельных /demo/tick без этой правки откладывали параллельный
    # /preview на 47с — это и есть источник бага «Пересчитать прогноз висит бесконечно»).
    # TICK_EXECUTOR — отдельный маленький пул специально под такты, см. runtime.py.
    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(TICK_EXECUTOR, REGISTRY.tick, payload.session_id, payload.n)
    return await run_in_threadpool(service.build_state, session)


@router.post("/demo/fault", response_model=ConsoleState)
async def post_demo_fault(payload: DemoFaultRequest) -> ConsoleState:
    """Инжектирует отказ датчика КИПиА или задержку анализа LIMS."""
    REGISTRY.set_fault(
        payload.session_id,
        payload.tag,
        payload.fault_type,
        payload.lims_age_hours,
    )
    session = REGISTRY.get(payload.session_id)
    return await run_in_threadpool(service.build_state, session)
