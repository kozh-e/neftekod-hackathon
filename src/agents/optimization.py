"""Агент оптимизации технологического режима на основе роллаута цифрового двойника.

Выполняет упреждающее прогнозирование (rollout) траекторий технологического комплекса
на горизонте установления H для детерминированного набора кандидатов управления,
рассчитывает маржинальность относительно удержания текущего режима (hold) и готовит
кандидатов для параллельного аудита надежности и качества.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.candidates import DEFAULT_MVS, MVSpec, generate_candidates
from src.agents.economics import MarginModel
from src.agents.state import ControlCandidate, MasGraphState
from src.twin.chain import FullChainTwin
from src.twin.params import TwinParams, load_params
from src.twin.session import TWIN_STORE


class RolloutOptimizationAgent:
    """Оптимизационный агент, формирующий кандидатов через роллаут динамического двойника."""

    def __init__(
        self,
        params: Optional[TwinParams] = None,
        mvs: Sequence[MVSpec] = DEFAULT_MVS,
        max_candidates: int = 25,
        horizon_steps: Optional[int] = None,
    ) -> None:
        self.params = params or load_params()
        self.mvs = mvs
        self.max_candidates = max_candidates
        self.user_horizon_steps = horizon_steps
        self.margin_model = MarginModel(self.params.economics, self.params.reactor)

    def horizon(self) -> int:
        """
        Рассчитывает глубину горизонта упреждения H (число шагов dt):
        H = ceil((theta_feed + theta_S + 3 * max(tau_mix, tau_S, tau_flash)) / dt)
        с ограничением в диапазон [6, 48].
        """
        if self.user_horizon_steps is not None:
            return max(6, min(48, int(self.user_horizon_steps)))

        theta_feed = self.params.feed.theta_min
        theta_s = self.params.dynamics.theta_s
        tau_mix = self.params.feed.tau_mix_min
        tau_s = self.params.dynamics.tau_s
        tau_flash = self.params.dynamics.tau_flash

        tau_max = max(tau_mix, tau_s, tau_flash)
        total_time_min = theta_feed + theta_s + 3.0 * tau_max
        dt = max(self.params.dt_min, 1.0)
        h_calc = math.ceil(total_time_min / dt)

        return max(6, min(48, h_calc))

    def propose(
        self, twin: FullChainTwin
    ) -> Tuple[List[ControlCandidate], Dict[str, List[float]]]:
        """
        Генерирует кандидатов и симулирует их динамические траектории через FullChainTwin.
        Возвращает:
        (список ControlCandidate, прогноз траектории удержания hold_prediction).
        """
        u0 = twin.u_current
        h_steps = self.horizon()

        # 1. Симуляция базового удержания (hold)
        hold_traj = twin.predict(u0, h_steps)
        hold_ss = twin.steady_state(u0)

        # 2. Формирование кандидатов
        candidate_moves = generate_candidates(
            u0, mvs=self.mvs, max_candidates=self.max_candidates
        )

        candidates: List[ControlCandidate] = []

        for cid, du in candidate_moves:
            is_hold = (du == {})
            # Абсолютные уставки кандидата
            u_target = dict(u0)
            for k, delta in du.items():
                u_target[k] = u0.get(k, 0.0) + delta

            # Динамический прогноз и установившийся режим
            traj = twin.predict(u_target, h_steps)
            ss = twin.steady_state(u_target)

            # Расчет экономической маржи относительно hold
            breakdown = self.margin_model.evaluate(ss, hold_ss, u_target, u0)
            expected_margin = 0.0 if is_hold else breakdown.total

            # Худшие показатели на траектории ∪ установившемся режиме
            s_vals = traj.get("HT_S_PRODUCT", []) + ([ss["HT_S_PRODUCT"]] if "HT_S_PRODUCT" in ss else [])
            exp_sulfur = max(s_vals) if s_vals else ss.get("HT_S_PRODUCT", 0.0)

            flash_vals = traj.get("HT_FLASH", []) + ([ss["HT_FLASH"]] if "HT_FLASH" in ss else [])
            exp_flash = min(flash_vals) if flash_vals else ss.get("HT_FLASH", 0.0)

            t95_vals = traj.get("HT_T95_PRODUCT", []) + ([ss["HT_T95_PRODUCT"]] if "HT_T95_PRODUCT" in ss else [])
            exp_t95 = max(t95_vals) if t95_vals else ss.get("HT_T95_PRODUCT", 0.0)

            dp_vals = traj.get("HT_DP_KPA", []) + ([ss["HT_DP_KPA"]] if "HT_DP_KPA" in ss else [])
            exp_dp = max(dp_vals) if dp_vals else ss.get("HT_DP_KPA", 0.0)

            tout_vals = traj.get("HT_T_OUT", []) + ([ss["HT_T_OUT"]] if "HT_T_OUT" in ss else [])
            exp_tout = max(tout_vals) if tout_vals else ss.get("HT_T_OUT", 0.0)

            gor_vals = traj.get("HT_GOR", []) + ([ss["HT_GOR"]] if "HT_GOR" in ss else [])
            exp_gor = min(gor_vals) if gor_vals else ss.get("HT_GOR", 0.0)

            ratio_vals = traj.get("HT_FEED_TO_AVT", []) + ([ss["HT_FEED_TO_AVT"]] if "HT_FEED_TO_AVT" in ss else [])
            exp_feed_to_avt = max(ratio_vals) if ratio_vals else ss.get("HT_FEED_TO_AVT", 1.0)

            cand = ControlCandidate(
                candidate_id=cid,
                delta_u=du,
                is_hold=is_hold,
                horizon_steps=h_steps,
                trajectory=traj,
                steady_state=ss,
                expected_margin=round(expected_margin, 2),
                margin_breakdown=asdict(breakdown),
                expected_sulfur=round(exp_sulfur, 2),
                expected_flash=round(exp_flash, 2),
                expected_t95=round(exp_t95, 2),
                expected_dp_kpa=round(exp_dp, 2),
                expected_t_out=round(exp_tout, 2),
                expected_gor=round(exp_gor, 2),
                expected_feed_to_avt=round(exp_feed_to_avt, 3),
                expected_density=round(ss.get("HT_D15_PRODUCT", 836.0), 2),
                expected_cfpp=round(ss.get("HT_CFPP_PRODUCT", -6.0), 2),
                expected_cetane=round(ss.get("HT_CN_PRODUCT", 53.75), 2),
                expected_t55=round(ss.get("AVT_T55", 381.7), 2),
                expected_w10=round(exp_dp / 98.0665, 3),  # обратная совместимость со старыми тестами
            )
            candidates.append(cand)

        return candidates, hold_traj


DEFAULT_OPTIMIZER = RolloutOptimizationAgent()


def node_optimization(state: MasGraphState) -> Dict[str, Any]:
    """
    Узел графа LangGraph: оптимизатор на базе роллаута.
    """
    existing_candidates = state.get("candidates")
    if existing_candidates:
        return {"candidates": existing_candidates}

    tags = state.get("tags")
    if tags is None and "raw_telemetry" in state:
        tags = state["raw_telemetry"].model_dump()

    session_id = state.get("session_id")
    twin, warnings = TWIN_STORE.get(session_id, tags or {})

    base_params = state.get("twin_params") or load_params()
    # Клонируем параметры для изолированного применения переопределений
    import copy
    params = copy.deepcopy(base_params)

    econ_override = state.get("economics")
    if econ_override:
        if isinstance(econ_override, dict):
            # Карта алиасов для совместимости
            alias_map = {
                "crude_oil_rub_ton": "price_crude_oil",
                "straight_run_diesel_rub_ton": "price_straight_run",
                "product_diesel_rub_ton": "price_godt",
                "c_diesel": "price_godt",
                "c_kerosene": "price_kerosene",
                "c_gasoil": "price_gasoil",
                "min_margin": "min_margin_improvement",
            }
            norm_econ = {}
            for k, v in econ_override.items():
                target_k = alias_map.get(k, k)
                norm_econ[target_k] = float(v)

            if "margin_spread" in econ_override and "price_godt" not in norm_econ:
                p_sr = norm_econ.get("price_straight_run", params.economics.price_straight_run)
                norm_econ["price_godt"] = p_sr + float(econ_override["margin_spread"])

            if "power_rub_kwh" in norm_econ and "compressor_rub_per_nm3" not in norm_econ:
                norm_econ["compressor_rub_per_nm3"] = round(norm_econ["power_rub_kwh"] * 0.05, 4)

            for k, v in norm_econ.items():
                if hasattr(params.economics, k):
                    setattr(params.economics, k, v)
        elif isinstance(econ_override, EconomicsParams):
            params.economics = econ_override

    agent = RolloutOptimizationAgent(params=params)
    candidates, hold_prediction = agent.propose(twin)

    return {
        "candidates": candidates,
        "hold_prediction": hold_prediction,
        "twin_warnings": warnings,
    }

