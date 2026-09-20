"""Песочница блендинга пульта оператора (роль B2, ADR-6 / 03_STREAMLIT_MIGRATION_PLAN.md §5).

Два режима работы:
- `blending_state` — НИЧЕГО не пересчитывает, только сериализует то, что граф МАС уже посчитал
  на последнем такте, тот же паттерн, что `build_pareto`/`build_xai` в `src/console/service.py`.
  ВНИМАНИЕ: реальный граф `core_v3` (используется `ConsoleSession` по умолчанию) не пишет
  ключ `"blending_recipe"`, ожидаемый по 03_STREAMLIT_MIGRATION_PLAN.md §0/§5 — см. подробности
  и обоснование в docstring `blending_state` и `agents/console_tz/QUESTIONS.md` [B2].
- `blending_preview` — считает гипотетический рецепт «что если» на копии резервуаров/цен
  напрямую через `build_blend_problem` + `solve_blend` + `certify_blend` из
  `src/agents/blending.py`, ничего не применяя к боевой сессии (`session.plant`/`session.graph`
  и `session.last_graph_result` не трогаются).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from src.agents.blending import (
    BlendingResult,
    build_blend_problem,
    certify_blend,
    get_default_tanks,
    solve_blend,
)
from src.agents.limits import (
    CETANE_PRODUCT_MIN,
    CFPP_BY_GRADE,
    FLASH_PRODUCT_MIN,
    SULFUR_PRODUCT_MAX,
    T95_PRODUCT_MAX,
)
from src.agents.tanks import ComponentTank
from src.console.contracts import (
    BlendCertDTO,
    BlendComponentDTO,
    BlendingPreviewDTO,
    BlendingStateDTO,
    BlendRecipeDTO,
    BlendSpecMetricDTO,
)

if TYPE_CHECKING:
    from src.console.runtime import ConsoleSession


# Человекочитаемые подписи резервуаров (agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md §5).
_TANK_LABELS: Dict[str, str] = {
    "GODT": "ГО ДТ",
    "Kerosene": "Керосин ТС-1",
    "Gasoil": "Газойль",
}

# Показатели сертификата: (key, label, unit, поле BlendingResult, лимит, знак, источник).
# Лимиты — реальные нормативы ГОСТ 32511-2013 (NORM из src/agents/limits.py), а НЕ внутренние
# буферы LP-задачи (BLEND_*), которыми реально ограничен решатель в node_blending_agent/
# build_blend_problem. Решение принято ролью B0 (agents/console_tz/STATUS.md, волна 6,
# раздел «Допущения»), см. также agents/console_tz/QUESTIONS.md — не дублируется здесь.
_SPEC_METRICS: Tuple[Tuple[str, str, str, str, Optional[float], Optional[str], Optional[str]], ...] = (
    ("sulfur", "Сера", "мг/кг", "expected_sulfur", SULFUR_PRODUCT_MAX.hi, "max", "limits.py:SULFUR_PRODUCT_MAX"),
    ("t95", "T95", "°C", "expected_t95", T95_PRODUCT_MAX.hi, "max", "limits.py:T95_PRODUCT_MAX"),
    ("cetane", "Цетановое число", "", "expected_cetane", CETANE_PRODUCT_MIN.lo, "min", "limits.py:CETANE_PRODUCT_MIN"),
    ("cfpp", "ПТФ (CFPP)", "°C", "expected_cfpp", CFPP_BY_GRADE.get("E"), "max", "limits.py:CFPP_BY_GRADE['E']"),
    ("flash", "Вспышка", "°C", "expected_flash", FLASH_PRODUCT_MIN.lo, "min", "limits.py:FLASH_PRODUCT_MIN"),
)


def _current_tanks(session: "ConsoleSession") -> Dict[str, ComponentTank]:
    """Возвращает резервуары последнего такта графа либо дефолт `get_default_tanks()`.

    `state["tanks"]` — ключ `MasGraphState` (src/agents/state.py:93), который заполняет
    `node_blending_agent` при первом обращении (`state.get("tanks") or get_default_tanks()`),
    но НЕ возвращает обратно в `result_update` — поэтому граф ни разу не пишет "tanks" в
    `last_graph_result`, и на практике `session.last_graph_result.get("tanks")` всегда `None`
    (см. agents/console_tz/QUESTIONS.md — зафиксировано новым вопросом от B2).
    """
    last_res = getattr(session, "last_graph_result", None) or {}
    tanks = last_res.get("tanks")
    if not tanks:
        tanks = get_default_tanks()
    return tanks


def _components_dto(tanks: Mapping[str, ComponentTank]) -> List[BlendComponentDTO]:
    """Сериализует резервуары в DTO (зеркало ComponentTank, без пересчета)."""
    return [
        BlendComponentDTO(
            id=tank_id,
            label=_TANK_LABELS.get(tank_id, tank_id),
            stock_t=float(getattr(tank, "stock_t", 0.0)),
            props=dict(getattr(tank, "props", {}) or {}),
        )
        for tank_id, tank in tanks.items()
    ]


def _recipe_dto(recipe: BlendingResult) -> BlendRecipeDTO:
    """Полное зеркало BlendingResult -> BlendRecipeDTO (совпадающий набор полей, см. STATUS.md B0)."""
    return BlendRecipeDTO(**recipe.model_dump())


def _recipe_dto_from_certificate(cert: Any) -> BlendRecipeDTO:
    """Адаптер для реально работающего графа core_v3 (см. docstring blending_state).

    `BlendingCertificate` (src/agents/contracts.py) — не `BlendingResult`: не содержит
    per-показательных полей (expected_sulfur/expected_flash/expected_cfpp/expected_t95/
    expected_cetane/additive_doses_kg_t), потому что certify_blend() всегда вызывается с
    `product_evaluations=()` (src/agents/blending.py, node_blending в negotiation.py их не
    заполняет). Поля, которых нет в BlendingCertificate, остаются на дефолтах BlendRecipeDTO
    (0.0/None/[]) — не подставляются наугад. v_diesel/v_kerosene восстановлены из реального
    `shares`, т.к. это фактические данные, а не выдумка.
    """
    shares = dict(getattr(cert, "shares", None) or {})
    status = getattr(cert, "status", "INFEASIBLE_ELASTIC")
    return BlendRecipeDTO(
        success=status == "FEASIBLE",
        status=status,
        cost_per_ton=float(getattr(cert, "cost_rub_per_t", 0.0) or 0.0),
        shares=shares,
        v_diesel=float(shares.get("GODT", 0.0)),
        v_kerosene=float(shares.get("Kerosene", 0.0)),
        binding_constraints=list(getattr(cert, "binding", None) or ()),
        elastic_violations=dict(getattr(cert, "elastic_violation", None) or {}),
    )


def _cert_dto(
    recipe: BlendingResult | BlendRecipeDTO,
    *,
    cost_per_ton: Optional[float] = None,
    status: Optional[str] = None,
    has_metric_detail: bool = True,
) -> BlendCertDTO:
    """Строит сертификат сравнением показателей рецепта с нормативами ГОСТ, без LP-пересчета.

    `has_metric_detail=False` — рецепт получен через `_recipe_dto_from_certificate` (фолбэк
    core_v3, см. blending_state): expected_sulfur/expected_flash/expected_cfpp там — ЛОЖНЫЕ
    дефолты BlendRecipeDTO (0.0), а не реально посчитанные значения. Показывать 0.0 как
    «сера смеси = 0 мг/кг» было бы выдумыванием числа (README.md §5, п.1) — вместо этого
    `value=None` для всех 5 показателей в этом случае (лимит/знак/источник остаются: это
    статические константы реестра, а не результат расчета).
    """
    metrics = [
        BlendSpecMetricDTO(
            key=key,
            label=label,
            unit=unit,
            value=getattr(recipe, field_name, None) if has_metric_detail else None,
            limit=limit,
            sense=sense,
            source=source,
        )
        for key, label, unit, field_name, limit, sense, source in _SPEC_METRICS
    ]
    return BlendCertDTO(
        status=status or recipe.status,
        cost_per_ton=cost_per_ton if cost_per_ton is not None else recipe.cost_per_ton,
        metrics=metrics,
        error_message=recipe.error_message,
    )


def blending_state(session: "ConsoleSession") -> Optional[BlendingStateDTO]:
    """Сериализует уже посчитанный графом рецепт блендинга последнего такта.

    Ничего не пересчитывает (тот же паттерн, что build_pareto/build_xai в service.py).
    Возвращает None, если такт еще не считался или узел блендинга не отдал рецепт
    (например, отказ данных до первого успешного цикла графа, ветка safe_hold).

    ВАЖНО (см. agents/console_tz/QUESTIONS.md [B2]): 03_STREAMLIT_MIGRATION_PLAN.md §0/§5
    предполагает, что граф пишет ключ "blending_recipe" (BlendingResult из
    node_blending_agent). Это верно только для legacy-графа. Реальный
    ConsoleSession.graph = get_graph() использует DEFAULT_GRAPH_MODE = "core_v3"
    (build_core_graph), где рецепт выбранного кандидата пишет node_blend_recipe в ключи
    "recipe"/"blending_certificate" как BlendingCertificate (agents/contracts.py) — другой,
    более бедный контракт (нет per-показательных expected_*). Поэтому здесь сначала
    проверяется "blending_recipe" (полная фидельность, на случай legacy/будущих режимов),
    затем — фолбэк на "recipe"/"blending_certificate" (частичная фидельность, реальные данные
    core_v3: статус/себестоимость/доли/активные ограничения, метрики сертификата без values).
    """
    last_res = getattr(session, "last_graph_result", None)
    if not last_res:
        return None

    raw_recipe = last_res.get("blending_recipe")
    has_metric_detail = raw_recipe is not None
    if raw_recipe is not None:
        recipe_dto = _recipe_dto(raw_recipe)
    else:
        certificate = last_res.get("recipe") or last_res.get("blending_certificate")
        if certificate is None:
            return None
        recipe_dto = _recipe_dto_from_certificate(certificate)

    tanks = _current_tanks(session)
    return BlendingStateDTO(
        recipe=recipe_dto,
        components=_components_dto(tanks),
        cert=_cert_dto(recipe_dto, has_metric_detail=has_metric_detail),
    )


def _apply_tank_overrides(tanks: Dict[str, ComponentTank], overrides: Optional[Dict[str, Any]]) -> None:
    """Применяет операторские правки поверх КОПИИ резервуаров (мутирует только переданный словарь).

    ASSUMPTION (формат тела запроса не описан в 01_CONTRACT.md, зафиксировано в docstring
    BlendingPreviewRequest, src/console/contracts.py): значение по ключу-танку — либо
    {"stock_t": ..., "props": {"S_ppm": ...}}, либо плоский словарь свойств {"S_ppm": ...}
    (с необязательным "stock_t" внутри). Неизвестный id танка или нечисловое значение — пропускается
    молча (песочница не должна падать на некорректном вводе UI).
    """
    if not overrides:
        return
    for tank_id, patch in overrides.items():
        tank = tanks.get(tank_id)
        if tank is None or not isinstance(patch, dict):
            continue
        if "stock_t" in patch:
            try:
                tank.stock_t = float(patch["stock_t"])
            except (TypeError, ValueError):
                pass
        nested_props = patch.get("props")
        props_patch = nested_props if isinstance(nested_props, dict) else patch
        for prop_key, prop_val in props_patch.items():
            if prop_key in ("stock_t", "props"):
                continue
            try:
                tank.props[prop_key] = float(prop_val)
            except (TypeError, ValueError):
                continue


def blending_preview(
    session: "ConsoleSession",
    tank_overrides: Optional[Dict[str, Any]],
    price_overrides: Optional[Dict[str, float]],
) -> BlendingPreviewDTO:
    """Пересчитывает рецепт блендинга «что если» на копии резервуаров/цен, ничего не применяя.

    Вызывает build_blend_problem + solve_blend + certify_blend из src/agents/blending.py
    напрямую на copy.deepcopy резервуаров последнего такта (или дефолте) — не мутирует
    session.last_graph_result и не трогает session.plant/session.graph. При status != "FEASIBLE"
    (либо success=False, например BLOCKED Dilution Loophole) исключение не бросается — DTO
    возвращается с feasible=False и error_message из BlendingResult.
    """
    tanks_copy: Dict[str, ComponentTank] = copy.deepcopy(_current_tanks(session))
    _apply_tank_overrides(tanks_copy, tank_overrides)

    prices: Dict[str, float] = dict(price_overrides or {})
    godt_tank = tanks_copy.get("GODT")
    godt_dict: Dict[str, float] = dict(godt_tank.props) if godt_tank is not None else {}

    problem = build_blend_problem(godt_dict, tanks_copy, prices)
    res = solve_blend(problem)

    cert_cost: Optional[float] = res.cost_per_ton
    cert_status: str = res.status
    try:
        certificate = certify_blend(
            cand="preview",
            godt_forecast=godt_dict,
            tanks=tanks_copy,
            prices=prices,
        )
        cert_cost = certificate.cost_rub_per_t
        cert_status = certificate.status
    except Exception:
        # certify_blend — вторичный (сверочный) вызов поверх того же solve_blend; при его сбое
        # не блокируем предпросмотр, используем результат основного res.
        pass

    feasible = bool(res.is_feasible)
    return BlendingPreviewDTO(
        recipe=_recipe_dto(res),
        components=_components_dto(tanks_copy),
        cert=_cert_dto(res, cost_per_ton=cert_cost, status=cert_status),
        feasible=feasible,
        error_message=None if feasible else res.error_message,
    )
