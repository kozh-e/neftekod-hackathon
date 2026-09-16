"""Генератор оптимизационных кандидатов управления (Control Candidates Generator).

Формирует детерминированное множество технологических ходов по официальным управляемым параметрам (ADR-4):
- cand_hold (удержание текущего режима) всегда первый;
- Однопараметрические шаги (+/- step) с клиппингом по допустимым диапазонам [lo, hi] и max_move;
- Согласованные ходы (Coupled Moves);
- Дедупликация и ограничение до 25 кандидатов.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class MVSpec:
    name: str
    tag: Optional[str]
    unit: str
    step: float
    max_move: float
    lo: float
    hi: float
    enabled: bool = True
    source: str = "PDF «У»; диапазон q05–q95 рабочих периодов — ASSUMPTION, не промышленный предел"


DEFAULT_MVS: Tuple[MVSpec, ...] = (
    MVSpec("HT_FEED_SP", "HT_F9", "т/ч", step=5.0, max_move=10.0, lo=158.7, hi=252.8),
    MVSpec("HT_TIN_SP", "HT_T6", "°C", step=2.0, max_move=3.0, lo=346.5, hi=380.5),
    MVSpec("HT_P_SP", "HT_P13", "МПа", step=0.03, max_move=0.05, lo=3.755, hi=4.009),
    MVSpec("HT_GOR_SP", "HT_GOR", "нм3/м3", step=15.0, max_move=30.0, lo=313.0, hi=490.0),
    MVSpec("AVT_TFURN_DEV", None, "°C", step=2.0, max_move=3.0, lo=-5.0, hi=5.0, enabled=False),
)

COUPLED_MOVES: Tuple[Dict[str, float], ...] = (
    {"HT_FEED_SP": +1.0, "HT_TIN_SP": +1.0},   # больше сырья + компенсация жёсткостью
    {"HT_FEED_SP": -1.0, "HT_TIN_SP": -1.0},   # разгрузка + экономия топлива
    {"HT_TIN_SP": -1.0, "HT_P_SP": +1.0},      # температуру заменяем давлением
)


def move_scales(mvs: Sequence[MVSpec] = DEFAULT_MVS) -> Dict[str, float]:
    """Возвращает масштабы шагов для каждого параметра: {name: step}."""
    return {mv.name: mv.step for mv in mvs if mv.enabled}


def generate_candidates(
    u_current: Mapping[str, float],
    mvs: Sequence[MVSpec] = DEFAULT_MVS,
    max_candidates: int = 25,
) -> List[Tuple[str, Dict[str, float]]]:
    """
    Генерирует детерминированный список кандидатов:
    1) ("cand_hold", {}) первым;
    2) +/- step для каждого включенного параметра;
    3) COUPLED_MOVES;
    4) Дедупликация и обрезка до max_candidates.
    """
    candidates_raw: List[Dict[str, float]] = [{}]  # cand_hold
    mv_dict = {mv.name: mv for mv in mvs}

    # 1. Однопараметрические шаги
    for mv in mvs:
        if not mv.enabled:
            continue
        u0 = float(u_current.get(mv.name, (mv.lo + mv.hi) / 2.0))

        for sign in (+1.0, -1.0):
            du_req = sign * mv.step
            u_target = u0 + du_req
            u_clipped = min(mv.hi, max(mv.lo, u_target))
            du_eff = u_clipped - u0

            # Ограничение максимальной скорости хода
            if abs(du_eff) > mv.max_move:
                du_eff = math.copysign(mv.max_move, du_eff)

            if abs(du_eff) > 1e-4:
                candidates_raw.append({mv.name: round(du_eff, 4)})

    # 2. Связанные ходы
    for cm in COUPLED_MOVES:
        cand_dict: Dict[str, float] = {}
        for mv_name, mult in cm.items():
            mv = mv_dict.get(mv_name)
            if mv is None or not mv.enabled:
                continue
            u0 = float(u_current.get(mv.name, (mv.lo + mv.hi) / 2.0))
            du_req = mult * mv.step
            u_target = u0 + du_req
            u_clipped = min(mv.hi, max(mv.lo, u_target))
            du_eff = u_clipped - u0
            if abs(du_eff) > mv.max_move:
                du_eff = math.copysign(mv.max_move, du_eff)
            if abs(du_eff) > 1e-4:
                cand_dict[mv.name] = round(du_eff, 4)

        if cand_dict:
            candidates_raw.append(cand_dict)

    # 3. Дедупликация с сохранением порядка
    unique_candidates: List[Dict[str, float]] = []
    seen_signatures = set()

    for cand_dict in candidates_raw:
        sig = tuple(sorted(cand_dict.items()))
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            unique_candidates.append(cand_dict)

    # 4. Формирование читаемых детерминированных ID
    result: List[Tuple[str, Dict[str, float]]] = []
    for idx, c_dict in enumerate(unique_candidates):
        if not c_dict:
            cid = "cand_hold"
        else:
            suffix_parts = [f"{k}{v:+g}" for k, v in sorted(c_dict.items())]
            cid = f"cand_{idx:02d}_" + "_".join(suffix_parts)
        result.append((cid, c_dict))
        if len(result) >= max_candidates:
            break

    return result
