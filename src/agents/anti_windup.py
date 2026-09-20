"""Защита от интегрального насыщения исполнительных механизмов (Anti-Windup).

Реализует скоростную форму управления (Velocity Form) с обратным расчетом
рассогласования (Back-Calculation Anti-Windup) согласно System_Design.md (Шаг 1.2).
"""

from __future__ import annotations

from typing import Tuple


def apply_anti_windup(
    current_u: float,
    delta_u_calc: float,
    max_rate: float,
    min_u: float,
    max_u: float
) -> Tuple[float, float]:
    """
    Ограничивает скорость изменения (rate clipping) и абсолютные границы клапана.
    
    Параметры:
        current_u: Текущее положение исполнительного органа (уставка на шаге k-1).
        delta_u_calc: Запрошенное оптимизатором изменение уставки.
        max_rate: Максимально допустимая скорость перемещения привода за такт.
        min_u: Минимально допустимая граница уставки (нижний упор клапана).
        max_u: Максимально допустимая граница уставки (верхний упор клапана).
        
    Возвращает:
        (effective_delta_u, windup_error):
        - effective_delta_u: Фактически реализуемое приводом изменение уставки.
        - windup_error: Ошибка насыщения (разность между запрошенной и реализованной позицией),
          передаваемая обратно для обнуления ложного интегрального накопления.
    """
    # 1. Ограничение скорости перекладки привода (Rate clipping)
    delta_u_clipped = max(-abs(max_rate), min(delta_u_calc, abs(max_rate)))
    
    # 2. Ожидаемая позиция клапана после скоростного ограничения
    candidate_u = current_u + delta_u_clipped
    
    # 3. Учет физических ограничений хода привода (Actuator saturation)
    actuator_u = max(min_u, min(candidate_u, max_u))
    
    # 4. Эффективно реализуемое изменение уставки
    effective_delta_u = actuator_u - current_u
    
    # 5. Ошибка Windup (нереализованная часть запроса)
    windup_error = candidate_u - actuator_u
    
    return effective_delta_u, windup_error
