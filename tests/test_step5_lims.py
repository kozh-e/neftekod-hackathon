"""Тесты Шага 5 MVP: Контракты Pydantic v2 и компенсация запаздывания LIMS.

Проверяет:
1. Валидацию контрактов BaseAgentProtocol и SafetyAuditReport (Pydantic v2).
2. Расчет ретроспективной ошибки (инновации) ВАК и LIMS.
3. Экспоненциальное затухание смещения (Bias Decay, T_half = 12 ч).
4. Безударный перенос (Bumpless Transfer, tau = 30 мин).
5. Расчет верхней доверительной границы UCB (95% = +1.96 * sigma_t).
6. Сброс и защиту от нефизичных значений.
"""

import math
import pytest
from pydantic import ValidationError

from src.agents.state import BaseAgentProtocol, SafetyAuditReport, ControlCandidate
from src.agents.lims import LimsBiasCompensator


def test_pydantic_contracts_validation():
    """Тест 1: Проверка валидации Pydantic v2 контрактов (ge, le, frozen, extra='forbid')."""
    # 1. BaseAgentProtocol запрещает лишние поля (extra='forbid')
    with pytest.raises(ValidationError):
        BaseAgentProtocol(extra_field="disallowed")

    # 2. SafetyAuditReport требует risk_penalty_rub_h >= 0.0
    with pytest.raises(ValidationError):
        SafetyAuditReport(
            candidate_id="cand_1",
            is_vetoed=False,
            risk_penalty_rub_h=-50.0  # Недопустимо, ge=0.0
        )

    # 3. Корректный отчет аудита
    report = SafetyAuditReport(
        candidate_id="cand_1",
        is_vetoed=True,
        risk_penalty_rub_h=1500.0,
        violation_reason="Превышение COT печи П-3"
    )
    assert report.candidate_id == "cand_1"
    assert report.is_vetoed is True
    assert report.risk_penalty_rub_h == 1500.0
    assert report.violation_reason == "Превышение COT печи П-3"


def test_lims_retrospective_innovation_and_decay():
    """Тест 2: Ретроспективная инновация и экспоненциальное затухание по возрасту пробы."""
    compensator = LimsBiasCompensator(dt_minutes=10.0, tau_filter_minutes=30.0, half_life_hours=12.0)

    # Проба отобрана 4 часа (240 минут) назад
    # Лаборатория: 9.5 ppm, исторический ВАК: 8.0 ppm -> raw_error = +1.5 ppm
    current_age = 240.0
    compensator.process_new_lims_result(
        lims_value=9.5,
        historical_vak_value_at_sampling_time=8.0,
        current_age_minutes=current_age
    )

    expected_decay = math.exp(-compensator.decay_lambda * current_age)
    expected_target_bias = 1.5 * expected_decay

    # Целевое смещение должно быть уменьшено пропорционально возрасту пробы
    assert math.isclose(compensator.target_bias, expected_target_bias, rel_tol=1e-4)
    assert compensator.target_bias < 1.5
    assert compensator.target_bias > 0.0


def test_lims_bumpless_transfer_filter():
    """Тест 3: Безударный перенос сглаживает скачок коррекции (tau = 30 мин)."""
    compensator = LimsBiasCompensator(dt_minutes=10.0, tau_filter_minutes=30.0)

    # Ввод нового анализа с нулевым возрастом: ошибка +2.0 ppm
    compensator.process_new_lims_result(
        lims_value=10.0,
        historical_vak_value_at_sampling_time=8.0,
        current_age_minutes=0.0
    )

    current_vak = 8.0

    # Шаг 1 (dt = 10 мин): bias не должен мгновенно подскочить до 2.0
    corr_1, ucb_1 = compensator.get_corrected_vak(current_vak, minutes_since_last_lims=0.0)
    # alpha = exp(-10/30) = exp(-1/3) ~ 0.7165; filtered ~ (1 - 0.7165) * 2.0 ~ 0.567
    assert 8.3 < corr_1 < 8.7  # Плавное нарастание, никакого скачка на 2.0
    assert ucb_1 > corr_1

    # Шаг 2 (dt = 20 мин суммарно)
    corr_2, ucb_2 = compensator.get_corrected_vak(current_vak, minutes_since_last_lims=10.0)
    assert corr_2 > corr_1  # Фильтр продолжает плавно подтягиваться к target


def test_lims_ucb_uncertainty_growth():
    """Тест 4: Верхняя доверительная граница (UCB) растет при старении анализа LIMS."""
    compensator = LimsBiasCompensator(dt_minutes=10.0, base_sigma=0.3)

    current_vak = 8.5
    # Свежий анализ (0 мин)
    _, ucb_fresh = compensator.get_corrected_vak(current_vak, minutes_since_last_lims=0.0)
    # 24 часа без анализов (1440 мин)
    _, ucb_old = compensator.get_corrected_vak(current_vak, minutes_since_last_lims=1440.0)

    # При 24 часах uncertainty_multiplier = 1 + 1 = 2.0, sigma = 0.6 -> +1.96 * 0.6 = +1.176
    # При 0 часов uncertainty_multiplier = 1.0, sigma = 0.3 -> +1.96 * 0.3 = +0.588
    assert ucb_old > ucb_fresh
    delta_fresh = ucb_fresh - current_vak
    delta_old = ucb_old - current_vak
    assert delta_old > delta_fresh * 1.8  # Рост неопределенности почти в 2 раза


def test_lims_decay_to_zero_without_new_samples():
    """Тест 5: При отсутствии новых анализов смещение плавно затухает к 0."""
    compensator = LimsBiasCompensator(dt_minutes=10.0, half_life_hours=12.0)
    compensator.process_new_lims_result(
        lims_value=10.0,
        historical_vak_value_at_sampling_time=8.0,
        current_age_minutes=0.0
    )

    # Симулируем 72 часа (432 шага по 10 минут)
    for _ in range(432):
        compensator.get_corrected_vak(current_vak_value=8.0, minutes_since_last_lims=100.0)

    # Через 6 периодов полураспада смещение должно быть практически нулевым
    assert abs(compensator.current_filtered_bias) < 0.05
    assert abs(compensator.target_bias) < 0.05
