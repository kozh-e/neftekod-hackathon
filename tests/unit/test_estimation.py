"""Unit tests for State Estimator and log-domain Kalman filtering (P1.5)."""

from __future__ import annotations

import datetime
import math
import pytest

from src.agents.contracts import SignalSource, SignalQuality, PlantEstimate
from src.agents.estimation import (
    StateEstimator,
    RingBuffer,
    HistoryBuffer,
    KalmanState,
    LimsSample,
)
from src.twin.tags import NOMINAL_OPERATING_POINT


def test_history_buffer_48h_limit():
    """Кольцевой буфер должен хранить историю за 48 часов и удалять старые записи."""
    buffer = RingBuffer(max_hours=48.0)
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)

    t_old = now - datetime.timedelta(hours=50)
    t_mid = now - datetime.timedelta(hours=20)
    t_new = now

    buffer.append(t_old, {"pak": 9.5, "model_raw": 9.0})
    buffer.append(t_mid, {"pak": 9.0, "model_raw": 8.8})
    buffer.append(t_new, {"pak": 8.5, "model_raw": 8.5})

    # Старая запись вытеснена из-за порога 48 часов
    assert buffer.pak_at(t_old) is None
    assert buffer.pak_at(t_mid) == 9.0
    assert buffer.pak_at(t_new) == 8.5


def test_kalman_state_log_domain_update():
    """Фильтр Калмана должен обновлять смещение и снижать дисперсию при замере."""
    now = datetime.datetime.now(datetime.timezone.utc)
    k_state = KalmanState(b=0.0, p=0.01, last_time=now)
    now = datetime.datetime.now(datetime.timezone.utc)

    # При прогнозе дисперсия растет
    later = now + datetime.timedelta(hours=5)
    k_state.predict(later, q_drift=0.0005)
    assert k_state.P > 0.01

    # При обновлении измерениями дисперсия снижается
    p_before = k_state.P
    k_state.update(innovation=0.05, r_noise=0.0025, t_now=later)
    assert k_state.P < p_before
    assert k_state.b != 0.0


def test_state_estimator_update():
    """Полный цикл оценки состояния установки через StateEstimator.update."""
    estimator = StateEstimator()
    t_now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    tags = dict(NOMINAL_OPERATING_POINT)
    tags["HT_Q21"] = 9.4

    twin_raw = {
        "HT_S_PRODUCT": 8.6,
        "HT_FLASH": 68.0,
        "HT_T95_PRODUCT": 347.0,
        "HT_D15_PRODUCT": 836.0,
        "HT_DP_KPA": 177.0,
    }
    u_twin = {
        "HT_FEED_SP": 219.6,
        "HT_TIN_SP": 363.3,
        "HT_P_SP": 3.922,
        "HT_GOR_SP": 360.0,
        "AVT_T55_SP": 381.7,
    }

    # Подаем пробу ЛИМС на момент отбора 2 часа назад
    t_sampled = t_now - datetime.timedelta(hours=2)
    estimator.buffer.append(t_sampled, {"pak": 9.2, "model_raw": 8.6})

    lims_sample = LimsSample(
        prop="S",
        value=8.8,
        sampled_at=t_sampled,
        available_at=t_now,
    )

    est = estimator.update(
        tags=tags,
        t_now=t_now,
        twin_raw_output=twin_raw,
        u_twin_current=u_twin,
        new_lims=[lims_sample],
        pak_quality=SignalQuality.GOOD,
    )

    assert isinstance(est, PlantEstimate)
    assert "GODT.S" in est.quality
    s_est = est.quality["GODT.S"]
    assert s_est.anchor == "PAK+bias"
    assert s_est.value > 0.0
    assert s_est.sigma_meas > 0.0
    assert s_est.sigma_calib > 0.0
    assert "AVT_T55" in est.measured_constraints
