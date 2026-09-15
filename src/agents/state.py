"""Контракты данных и глобальное состояние мультиагентного графа LangGraph.

Обеспечивает типизацию (Pydantic v2) и защиту от гонок данных (InvalidUpdateError)
при параллельном выполнении агентов через типизированные редьюсеры.
"""

from __future__ import annotations

import operator
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class RawTelemetry(BaseModel):
    """Сырые КИПиА данные с датчиков технологического комплекса."""
    
    model_config = ConfigDict(extra="allow")

    timestamp: str = Field(description="Метка времени измерения (ISO 8601 или промышленный архив)")
    P52: float = Field(description="Перепад давления вакуумной колонны К-10, кгс/см2")
    D10: float = Field(description="Плотность сырья, кг/м3")
    F15: float = Field(default=0.0, description="Расход водородного квенча, нм3/ч")
    T55: float = Field(default=0.0, description="Температура перевала печи П-3 (COT), °C")
    F5: float = Field(default=0.0, description="Расход пара в куб колонны К-1, т/ч")
    F26: float = Field(default=0.0, description="Расход пара в стриппинг К-6 / дизельное сырье, т/ч")
    lims_age_hours: float = Field(default=0.0, description="Возраст последнего лабораторного анализа LIMS в часах")


class DataQuality(BaseModel):
    """Статус валидации КИПиА и качества входных данных (Data Guard)."""
    
    model_config = ConfigDict(extra="allow")

    is_valid: bool = True
    clamped_tags: List[str] = Field(default_factory=list, description="Теги, залипшие на аппаратных значениях клампинга (307.0/313.0)")
    invalid_range_tags: List[str] = Field(default_factory=list, description="Теги с нечисловыми (NaN, Inf) или внедиапазонными значениями")
    lims_age_hours: float = 0.0
    status_code: str = "NORMAL"  # "NORMAL" или "SAFE_HOLD_REFUSAL"
    refusal_reason: Optional[str] = None


class ControlCandidate(BaseModel):
    """Предложение по вариации уставок технологического режима."""
    
    candidate_id: str
    delta_u: Dict[str, float] = Field(default_factory=dict, description="Вектор изменения уставок")
    expected_margin: float = Field(default=0.0, description="Ожидаемый экономический эффект, руб/ч")


class FinalRecommendation(BaseModel):
    """Итоговая рекомендация системы оператору / на нижний уровень APC."""
    
    status: str = Field(description="Статус: NORMAL, SAFE_HOLD, BLEND_OPTIMIZED и т.д.")
    explanation: str = Field(description="Пояснение причин решения (XAI)")
    recommended_delta_u: Dict[str, float] = Field(default_factory=dict, description="Вектор допустимых коррекций уставок")


def merge_risk_penalties(left: Dict[str, float], right: Dict[str, float]) -> Dict[str, float]:
    """Редьюсер объединения словарей барьерных штрафов риска."""
    merged = left.copy() if left else {}
    if right:
        merged.update(right)
    return merged


def merge_audit_dict(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Редьюсер детерминированного слияния параллельных отчетов аудита."""
    merged = left.copy() if left else {}
    if right:
        for k, v in right.items():
            if k in merged and isinstance(merged[k], list) and isinstance(v, list):
                merged[k] = merged[k] + v
            else:
                merged[k] = v
    return merged


class MasGraphState(TypedDict, total=False):
    """
    Глобальное состояние графа вычислений LangGraph.
    
    Использование Annotated[..., operator.add] и кастомных редьюсеров
    предотвращает возникновение InvalidUpdateError при параллельном
    выполнении аудиторов надежности и качества (Fan-Out / Fan-In).
    """
    raw_telemetry: RawTelemetry
    data_quality: DataQuality
    
    # Список кандидатов, сгенерированных оптимизатором
    candidates: List[ControlCandidate]
    
    # Список забракованных кандидатов (аккумулируется всеми аудиторами через add)
    vetoed_candidates: Annotated[List[str], operator.add]
    
    # Барьерные штрафы (ID кандидата -> Штраф руб/ч)
    risk_penalties: Annotated[Dict[str, float], merge_risk_penalties]
    
    # Итоговый вердикт системы
    final_recommendation: Optional[FinalRecommendation]
