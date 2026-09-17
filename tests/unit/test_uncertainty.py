"""Unit tests for uncertainty quantification (UQ) and chance constraints (P1.7)."""

from __future__ import annotations

import math
import numpy as np
import pytest

from src.agents.contracts import ConstraintSpec, Tier, Provenance, ProvenanceKind
from src.agents.registry import REGISTRY
from src.agents.policy import PolicyConfig
from src.agents.uncertainty import (
    z_from_alpha,
    chance_effective,
    SensitivityModel,
    SIGMA_THETA,
    PARAM_NAMES,
)


def test_z_from_alpha():
    """Проверка квантилей нормального распределения для уровней значимости alpha."""
    assert abs(z_from_alpha(0.05) - 1.6449) < 1e-3
    assert abs(z_from_alpha(0.0228) - 1.999) < 1e-2
    assert abs(z_from_alpha(0.01) - 2.3263) < 1e-3

    # Граничные значения
    assert z_from_alpha(0.5) == 0.0
    assert z_from_alpha(1e-7) > 4.5


def test_chance_effective_hold_log_domain():
    """В режиме hold (Delta u = 0) эффективное ограничение рассчитывается без модельного градиента."""
    spec = REGISTRY["GODT.S_MAX"]
    mean_now = 8.5
    eff_val, sigma, z = chance_effective(
        spec=spec,
        mean_now=mean_now,
        pred_u=8.5,
        pred_u0=8.5,
        delta_u={},
    )
    # Для max ограничения эффективное значение должно быть строго больше среднего
    assert eff_val > mean_now
    assert sigma > 0.0
    assert z > 0.0


def test_sensitivity_model_gradients():
    """Проверка расчета градиентов отклика модели на приращения уставок."""
    sens = SensitivityModel()
    delta_u = {"HT_TIN_SP": 2.0, "HT_FEED_SP": -5.0}

    g_log = sens.grad_log_ratio("GODT.S", delta_u)
    assert isinstance(g_log, np.ndarray)
    assert len(g_log) == len(PARAM_NAMES)
    # Градиент по E_h_R и k_h должен быть ненулевым
    assert g_log[0] != 0.0 or g_log[1] != 0.0

    # Hold дает нулевой градиент
    g_zero = sens.grad_log_ratio("GODT.S", {})
    assert np.allclose(g_zero, 0.0)


def test_uncertainty_growth_with_step():
    """Неопределенность отклика модели должна возрастать с ростом величины шага Delta u."""
    sens = SensitivityModel()
    spec = REGISTRY["GODT.S_MAX"]

    _, sigma_small, _ = chance_effective(
        spec=spec,
        mean_now=8.0,
        pred_u=8.0,
        pred_u0=8.0,
        delta_u={"HT_FEED_SP": 1.0},
        sens_model=sens,
    )

    _, sigma_large, _ = chance_effective(
        spec=spec,
        mean_now=8.0,
        pred_u=8.0,
        pred_u0=8.0,
        delta_u={"HT_FEED_SP": 10.0},
        sens_model=sens,
    )

    assert sigma_large > sigma_small
