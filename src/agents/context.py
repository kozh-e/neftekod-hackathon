"""Контекст исполнения агентов ядра управления (AgentContext).

Объединяет оценку состояния PlantEstimate, качество данных DataAssessment,
технологическую политику PolicyConfig, цифровую модель TwinView и параметры сессии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from src.agents.contracts import (
    DataAssessment,
    Measurement,
    PlantEstimate,
    QualityEstimate,
    Provenance,
    ProvenanceKind,
    SignalQuality,
    SignalSource,
    AutomationLevel,
)
from src.agents.policy import PolicyConfig
from src.agents.uncertainty import SensitivityModel
from src.twin.chain import FullChainTwin
from src.twin.params import TwinParams, load_params


@dataclass
class AgentContext:
    """Контекст для вызова методов certify агентов-владельцев ограничений."""
    estimate: PlantEstimate
    data: DataAssessment
    policy: PolicyConfig
    twin_view: Any = None
    sens_theta: SensitivityModel = field(default_factory=SensitivityModel)
    round: int = 0
    buffer_inventory_t: float = 0.0
    u: Dict[str, float] = field(default_factory=dict)
    session_id: str = "default_session"
    tags: Dict[str, float] = field(default_factory=dict)


def build_default_context(
    estimate: Optional[PlantEstimate] = None,
    data: Optional[DataAssessment] = None,
    policy: Optional[PolicyConfig] = None,
    twin_view: Any = None,
    tags: Optional[Mapping[str, float]] = None,
) -> AgentContext:
    """Создает валидный дефолтный контекст для юнит-тестов и автономных вызовов."""
    now = datetime.now()
    pol = policy or PolicyConfig()
    t_dict = dict(tags or {})

    if data is None:
        meas_dict: Dict[str, Measurement] = {}
        for k, v in t_dict.items():
            meas_dict[k] = Measurement(
                tag=k,
                value=float(v),
                unit="",
                source=SignalSource.DCS,
                sampled_at=now,
                quality=SignalQuality.GOOD,
            )
        data = DataAssessment(
            measurements=meas_dict,
            automation_level=AutomationLevel.FULL,
            reasons=(),
            blocked_mvs=frozenset(),
            unknown_specs=frozenset(),
        )

    if estimate is None:
        u_act = {
            "HT_FEED_SP": float(t_dict.get("HT_FEED_SP", t_dict.get("HT_F9", 219.6))),
            "HT_TIN_SP": float(t_dict.get("HT_TIN_SP", t_dict.get("HT_T6", 363.3))),
            "HT_P_SP": float(t_dict.get("HT_P_SP", t_dict.get("HT_P13", 3.922))),
            "HT_GOR_SP": float(t_dict.get("HT_GOR_SP", t_dict.get("HT_GOR", 360.0))),
            "AVT_T55_SP": float(t_dict.get("AVT_T55_SP", t_dict.get("AVT_T55", 381.7))),
        }
        measured = {
            "AVT_T55": float(t_dict.get("AVT_T55", 381.7)),
            "HT_DP_KPA": float(t_dict.get("HT_DP_KPA", 177.0)),
            "HT_T11": float(t_dict.get("HT_T11", 364.0)),
            "HT_GOR": float(t_dict.get("HT_GOR", 360.0)),
            "AVT_F31": float(t_dict.get("AVT_F31", 540.7)),
            "AVT_P52": float(t_dict.get("AVT_P52", 0.05)),
            "HT_FEED_TO_AVT": float(t_dict.get("HT_FEED_TO_AVT", 1.0)),
        }
        import math
        def _safe_float(k: str, fb_k: str, dflt: float) -> float:
            v = t_dict.get(k)
            if v is not None and isinstance(v, (int, float)) and not (math.isnan(v) or math.isinf(v)):
                return float(v)
            v_fb = t_dict.get(fb_k)
            if v_fb is not None and isinstance(v_fb, (int, float)) and not (math.isnan(v_fb) or math.isinf(v_fb)):
                return float(v_fb)
            return dflt

        s_val = _safe_float("HT_Q21", "LIMS_HT_S", 8.6)
        s_anchor = "PAK+bias" if (t_dict.get("HT_Q21") is not None and not math.isnan(t_dict.get("HT_Q21", 0.0))) else "MODEL+bias"
        s_sigma = 0.10 if s_anchor == "PAK+bias" else 0.25

        q_dict: Dict[str, QualityEstimate] = {
            "GODT.S": QualityEstimate(
                stream="GODT",
                prop="S",
                value=s_val,
                domain="log",
                sigma_meas=s_sigma,
                sigma_calib=0.05,
                calib_age_h=float(t_dict.get("lims_age_hours", 0.0)),
                anchor=s_anchor,
                provenance=Provenance(kind=ProvenanceKind.NORM, ref="ГОСТ 32511-2013", note="Сера Евро-5"),
            ),
            "GODT.FLASH": QualityEstimate(
                stream="GODT",
                prop="FLASH",
                value=_safe_float("HT_T18", "LIMS_HT_FLASH", 68.0),
                domain="linear",
                sigma_meas=4.78,
                sigma_calib=0.5,
                calib_age_h=float(t_dict.get("lims_age_hours", 0.0)),
                anchor="PAK+bias",
                provenance=Provenance(kind=ProvenanceKind.NORM, ref="ГОСТ 32511-2013", note="Вспышка Евро-5"),
            ),
        }
        estimate = PlantEstimate(
            t=now,
            u_actual=u_act,
            manual_changes=(),
            disturbances={
                "AVT_F30": float(t_dict.get("AVT_F30", 119.3)),
                "AVT_F32": float(t_dict.get("AVT_F32", 100.3)),
                "AVT_F31": float(t_dict.get("AVT_F31", 540.7)),
                "AVT_P52": float(t_dict.get("AVT_P52", 0.05)),
            },
            measured_constraints=measured,
            quality=q_dict,
            factors={"HT_DP_KPA": 1.0, "GODT.S": 1.0},
            twin_steps_advanced=1,
        )

    if twin_view is None:
        from src.agents.twin_view import TwinView
        twin = FullChainTwin(load_params())
        twin_view = TwinView(twin=twin, estimate=estimate, policy=pol)

    return AgentContext(
        estimate=estimate,
        data=data,
        policy=pol,
        twin_view=twin_view,
        u=dict(estimate.u_actual),
        tags=t_dict,
    )
