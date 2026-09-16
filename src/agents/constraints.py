"""Оценка технологических ограничений и статистических буферов (Constraints & Safety Boundaries).

Реализует ADR-4 и ADR-12:
- Оценка установившегося режима (steady-state);
- Проверка переходного процесса («не хуже hold»);
- Расчет динамического статистического смещения stat_offset с учетом возраста измерений.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Sequence


@dataclass(frozen=True)
class LimitAssessment:
    key: str
    vetoed: bool
    violates_ss: bool
    worsens_transient: bool
    margin: float
    worst_value: float
    reason: Optional[str] = None


def stat_offset(sigma0: float, age_h: float, z: float, t_sigma_h: float = 12.0) -> float:
    """
    Рассчитывает статистический запас надежности:
    offset = z * sigma0 * sqrt(1 + age_h / t_sigma_h)
    где age_h = 0 при валидном поточном анализаторе (онлайн-сигнал).
    """
    age_eff = max(0.0, float(age_h))
    t_sigma_eff = max(1e-4, float(t_sigma_h))
    return float(z * sigma0 * math.sqrt(1.0 + age_eff / t_sigma_eff))


def assess_limit(
    key: str,
    traj: Optional[Sequence[float]],
    ss_val: float,
    hold_traj: Optional[Sequence[float]],
    limit: float,
    sense: Literal["max", "min"],
    offset: float = 0.0,
    tol: float = 1e-6,
) -> LimitAssessment:
    """
    Оценивает нарушение технологического ограничения:
    - offset >= 0: статистический запас (добавляется для max, вычитается для min);
    - violates_ss: нарушение на установившемся режиме;
    - worsens_transient: наличие точки k, где кандидат нарушает предел И ухудшает траекторию относительно hold;
    - vetoed = violates_ss or worsens_transient.
    """
    offset_eff = max(0.0, float(offset))

    if sense == "max":
        # Эффективное установившееся значение
        ss_eff = ss_val + offset_eff
        g_ss = ss_eff - limit
        violates_ss = g_ss > tol

        if traj is not None and len(traj) > 0:
            worst_val = max(traj)
            worst_effective = worst_val + offset_eff

            worsens_transient = False
            h_traj = hold_traj if hold_traj is not None else []
            for y_k, h_k in zip(traj, h_traj):
                g_k = (y_k + offset_eff) - limit
                if g_k > tol and (y_k - h_k) > tol:
                    worsens_transient = True
                    break

            vetoed = violates_ss or worsens_transient
            margin = limit - ss_eff
        else:
            worsens_transient = False
            worst_val = ss_val
            worst_effective = ss_eff
            vetoed = violates_ss
            margin = limit - ss_eff

        reason = None
        if vetoed:
            if violates_ss:
                reason = f"{key} превышает предел {limit} на установившемся режиме ({ss_eff:.2f})"
            else:
                reason = f"{key} ухудшает переходный процесс сверх предела {limit} ({worst_effective:.2f})"

    else:  # sense == "min"
        ss_eff = ss_val - offset_eff
        g_ss = limit - ss_eff
        violates_ss = g_ss > tol

        if traj is not None and len(traj) > 0:
            worst_val = min(traj)
            worst_effective = worst_val - offset_eff

            worsens_transient = False
            h_traj = hold_traj if hold_traj is not None else []
            for y_k, h_k in zip(traj, h_traj):
                g_k = limit - (y_k - offset_eff)
                if g_k > tol and (h_k - y_k) > tol:
                    worsens_transient = True
                    break

            vetoed = violates_ss or worsens_transient
            margin = ss_eff - limit
        else:
            worsens_transient = False
            worst_val = ss_val
            worst_effective = ss_eff
            vetoed = violates_ss
            margin = ss_eff - limit

        reason = None
        if vetoed:
            if violates_ss:
                reason = f"{key} ниже предела {limit} на установившемся режиме ({ss_eff:.2f})"
            else:
                reason = f"{key} ухудшает переходный процесс ниже предела {limit} ({worst_effective:.2f})"

    return LimitAssessment(
        key=key,
        vetoed=vetoed,
        violates_ss=violates_ss,
        worsens_transient=worsens_transient,
        margin=round(margin, 4),
        worst_value=round(worst_val, 4),
        reason=reason,
    )
