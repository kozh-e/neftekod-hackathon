"""Агенты аудита технологической безопасности и качества (Reliability & Quality Agents).

Реализуют Стадию 1 двухстадийного гибридного арбитража согласно System_Design.md (Шаг 4.1):
1. ReliabilityAgent: Агент противоаварийной защиты (ПАЗ/ESD).
   Проверяет соблюдение обязательного 5% буфера безопасности оборудования
   (T55 <= 386.40°C, W10 <= 4.635 кгс/см², P52 <= 0.077 кгс/см², F31 >= 362.5 м³/ч)
   и начисляет логарифмические штрафы риска в предохранительной зоне печи.
2. QualityAgent: Агент соблюдения стандарта ГОСТ 32511-2013 (Евро-5).
   Проверяет непревышение порога серы (<= 9.50 ppm с учетом 5% буфера)
   и допустимый диапазон плотности дизельного гидрогенизата.
"""

from __future__ import annotations

import math
from typing import Dict, List, Any, Tuple
from src.agents.state import MasGraphState, ControlCandidate


class ReliabilityAgent:
    """Агент Надежности: аудит технологических ограничений оборудования и барьеры ПАЗ."""
    
    # 5% защитные барьеры отсечения Hard-Veto (Таблица 4.1 System_Design.md)
    MAX_COT_T55: float = 386.40     # °C (норматив 387.0 °C, размах 12.0 °C, запас 0.60 °C)
    MAX_W10: float = 4.635          # кгс/см² (предел 4.80, размах 3.3, запас 0.165)
    MAX_P52: float = 0.077          # кгс/см² (предел 0.080, размах 0.06, запас 0.003)
    MIN_F31: float = 362.50         # м³/ч (минимум 350.0, размах 250.0, запас 12.5)
    
    # Параметры логарифмического барьера риска печи П-3
    COT_WARNING_ZONE: float = 380.0 # Нижняя граница буферной зоны печи, °C
    COT_SPAN: float = 12.0          # Размах технологической шкалы печи, °C
    MU_PENALTY: float = 1000.0      # Весовой множитель лог-барьера, руб/ч

    @classmethod
    def audit_candidate(cls, cand: ControlCandidate) -> Tuple[bool, Optional[str], float]:
        """
        Проверка одного кандидата на соответствие барьерам ПАЗ.
        Возвращает: (is_vetoed, veto_reason, risk_penalty)
        """
        # 1. Проверка 5% барьеров Hard-Veto
        if cand.expected_t55 is not None and cand.expected_t55 > cls.MAX_COT_T55:
            return True, f"ESD_VETO: T55={cand.expected_t55:.2f}°C превышает 5% защитный порог ПАЗ ({cls.MAX_COT_T55:.2f}°C)", 0.0
            
        if cand.expected_w10 is not None and cand.expected_w10 > cls.MAX_W10:
            return True, f"ESD_VETO: W10={cand.expected_w10:.3f} кгс/см² превышает предел ({cls.MAX_W10:.3f} кгс/см²)", 0.0
            
        if cand.expected_p52 is not None and cand.expected_p52 > cls.MAX_P52:
            return True, f"ESD_VETO: P52={cand.expected_p52:.3f} кгс/см² превышает порог захлебывания ({cls.MAX_P52:.3f} кгс/см²)", 0.0
            
        if cand.expected_f31 is not None and cand.expected_f31 < cls.MIN_F31:
            return True, f"ESD_VETO: Расход сырья F31={cand.expected_f31:.1f} м³/ч ниже безопасного ({cls.MIN_F31:.1f} м³/ч)", 0.0

        # 2. Расчет гладкого логарифмического барьера риска (при отсутствии вето)
        penalty = 0.0
        if cand.expected_t55 is not None and cand.expected_t55 > cls.COT_WARNING_ZONE:
            # Зазор безопасности до жесткого отсечения
            safety_margin = max(cls.MAX_COT_T55 - cand.expected_t55, 1e-4)
            # Формула B(X) = -mu * ln((X_safe - X) / span)
            normalized_margin = safety_margin / cls.COT_SPAN
            penalty = -cls.MU_PENALTY * math.log(normalized_margin)
            penalty = max(0.0, penalty)

        return False, None, penalty


class QualityAgent:
    """Агент Качества: аудит соответствия ГОСТ 32511-2013 (Евро-5)."""

    MAX_SULFUR_PPM: float = 9.50    # мг/кг (норматив 10.0 ppm, 5% буфер = 0.50 ppm)
    MIN_DENSITY: float = 821.25     # кг/м³ (норматив 820.0, 5% буфер = 1.25)
    MAX_DENSITY: float = 843.75     # кг/м³ (норматив 845.0, 5% буфер = 1.25)

    @classmethod
    def audit_candidate(cls, cand: ControlCandidate) -> Tuple[bool, Optional[str]]:
        """
        Проверка одного кандидата на соответствие качеству ГОСТ.
        Возвращает: (is_vetoed, veto_reason)
        """
        if cand.expected_sulfur is not None and cand.expected_sulfur > cls.MAX_SULFUR_PPM:
            return True, f"GOST_VETO: Сера гидрогенизата {cand.expected_sulfur:.2f} ppm > 5% предела ГОСТ ({cls.MAX_SULFUR_PPM:.2f} ppm)"

        if cand.expected_density is not None:
            if cand.expected_density < cls.MIN_DENSITY or cand.expected_density > cls.MAX_DENSITY:
                return True, f"GOST_VETO: Плотность {cand.expected_density:.1f} кг/м³ вне допуска ГОСТ [{cls.MIN_DENSITY:.2f}, {cls.MAX_DENSITY:.2f}]"

        return False, None


# =========================================================================
# Узлы LangGraph (Параллельный Fan-Out)
# =========================================================================

def node_reliability_agent(state: MasGraphState) -> Dict[str, Any]:
    """Узел LangGraph: аудит безопасности оборудования Агентом Надежности."""
    candidates = state.get("candidates", [])
    vetoed: List[str] = []
    penalties: Dict[str, float] = {}

    for cand in candidates:
        is_vetoed, reason, penalty = ReliabilityAgent.audit_candidate(cand)
        if is_vetoed:
            vetoed.append(cand.candidate_id)
        elif penalty > 0.0:
            penalties[cand.candidate_id] = round(penalty, 2)

    return {
        "vetoed_candidates": vetoed,
        "risk_penalties": penalties,
    }


def node_quality_agent(state: MasGraphState) -> Dict[str, Any]:
    """Узел LangGraph: аудит соответствия ГОСТ Агентом Качества."""
    candidates = state.get("candidates", [])
    vetoed: List[str] = []

    for cand in candidates:
        is_vetoed, reason = QualityAgent.audit_candidate(cand)
        if is_vetoed:
            vetoed.append(cand.candidate_id)

    return {
        "vetoed_candidates": vetoed,
    }
