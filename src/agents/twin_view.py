"""Обертка цифрового двойника для быстрого прогнозирования и вычисления градиентов (TwinView).

Реализует спецификацию §5.1–§5.2 implementation_plan_v3.md (Задача P2.1):
1. Роллаут и установившийся режим цифрового двойника FullChainTwin с калибровочными поправками
   оценщика состояния StateEstimator (PlantEstimate);
2. Конечно-разностные градиенты запасов ограничений grad(s_i) по 5 управляющим воздействиям (MV)
   с мемоизацией (кэшированием) по сигнатуре кандидата;
3. Хранение полных динамических траекторий только для hold и лучших кандидатов;
4. Метод with_params для параметризации сценариев ансамбля печи П-3.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.agents.contracts import (
    Candidate,
    ConstraintSpec,
    PlantEstimate,
    Prediction,
    Tier,
)
from src.agents.policy import PolicyConfig
from src.agents.uncertainty import chance_effective
from src.twin.chain import FullChainTwin, MV_NAMES
from src.twin.params import TwinParams, load_params


# Шаги конечных разностей для 5 управляющих переменных (MV)
FD_STEPS: Dict[str, float] = {
    "HT_FEED_SP": 0.5,    # т/ч
    "HT_TIN_SP": 0.2,     # °C
    "HT_P_SP": 0.005,     # МПа
    "HT_GOR_SP": 2.0,     # нм3/м3
    "AVT_T55_SP": 0.2,    # °C
}


class TwinView:
    """
    Высокопроизводительная обертка над FullChainTwin с поправками PlantEstimate.
    """

    def __init__(
        self,
        twin: FullChainTwin,
        estimate: Optional[PlantEstimate] = None,
        policy: Optional[PolicyConfig] = None,
    ) -> None:
        self.twin = twin
        self.estimate = estimate
        self.policy = policy or PolicyConfig()
        self._steady_cache: Dict[str, Dict[str, float]] = {}
        self._gradient_cache: Dict[Tuple[str, str], Dict[str, float]] = {}

    @property
    def u_current(self) -> Dict[str, float]:
        """Базовые уставки текущего состояния."""
        return dict(self.twin.u_current)

    def _extract_target_u(self, cand_or_delta: Any) -> Dict[str, float]:
        """Формирует абсолютные уставки u = u0 + delta_u."""
        u = dict(self.twin.u_current)
        if hasattr(cand_or_delta, "delta_u"):
            du = cand_or_delta.delta_u
        elif isinstance(cand_or_delta, dict):
            du = cand_or_delta
        else:
            du = {}

        for k, delta in du.items():
            u[k] = u.get(k, 0.0) + float(delta)
        return u

    def _apply_corrections(self, raw_ss: Mapping[str, float]) -> Dict[str, float]:
        """Применяет калибровочные коэффициенты и поправки PlantEstimate."""
        corrected = dict(raw_ss)

        # Синхронизация алиасов выходов
        if "HT_T_OUT" in corrected and "HT_T11" not in corrected:
            corrected["HT_T11"] = corrected["HT_T_OUT"]
        elif "HT_T11" in corrected and "HT_T_OUT" not in corrected:
            corrected["HT_T_OUT"] = corrected["HT_T11"]

        if self.estimate is not None:
            # 1. Мультипликативный коэффициент засорения Р-202
            dp_factor = self.estimate.factors.get("HT_DP_KPA")
            if dp_factor is not None and "HT_DP_KPA" in corrected:
                corrected["HT_DP_KPA"] = corrected["HT_DP_KPA"] * dp_factor

            # 2. Множитель серы
            s_factor = self.estimate.factors.get("GODT.S")
            if s_factor is not None and "HT_S_PRODUCT" in corrected:
                corrected["HT_S_PRODUCT"] = corrected["HT_S_PRODUCT"] * s_factor
                corrected["GODT.S"] = corrected["HT_S_PRODUCT"]

            # 3. Аддитивные поправки для остальных показателей качества
            for prop in ("FLASH", "D15", "T95", "CFPP", "CN"):
                key = f"GODT.{prop}"
                q_est = self.estimate.quality.get(key)
                twin_tag = f"HT_{prop}" if prop == "FLASH" else f"HT_{prop}_PRODUCT"
                if q_est is not None and twin_tag in corrected:
                    # Поправка = измеренное/оцененное сейчас - сырой выход модели hold
                    bias = self.estimate.factors.get(key, 0.0)
                    corrected[twin_tag] = corrected[twin_tag] + bias
                    corrected[key] = corrected[twin_tag]

        if "HT_S_PRODUCT" in corrected and "GODT.S" not in corrected:
            corrected["GODT.S"] = corrected["HT_S_PRODUCT"]
        if "HT_FLASH" in corrected and "GODT.FLASH" not in corrected:
            corrected["GODT.FLASH"] = corrected["HT_FLASH"]

        return corrected

    def steady(self, cand_or_delta: Any) -> Dict[str, float]:
        """
        Рассчитывает установившийся режим с учетом поправок оценки состояния.
        """
        sig = getattr(cand_or_delta, "signature", None)
        if sig is not None and sig in self._steady_cache:
            return dict(self._steady_cache[sig])

        u_target = self._extract_target_u(cand_or_delta)
        raw_ss = self.twin.steady_state(u_target)
        corrected_ss = self._apply_corrections(raw_ss)

        if sig is not None:
            self._steady_cache[sig] = dict(corrected_ss)

        return corrected_ss

    def predict(
        self,
        cand: Candidate,
        horizon_steps: int = 12,
        store_traj: bool = False,
    ) -> Prediction:
        """
        Строит прогноз поведения кандидата на горизонте установления.
        Полная траектория сохраняется только если store_traj=True (для hold и топ-кандидатов).
        """
        u_target = self._extract_target_u(cand)
        ss = self.steady(cand)

        # Симуляция динамической траектории
        raw_traj = self.twin.predict(u_target, horizon=horizon_steps)

        # Коррекция траектории
        dp_factor = self.estimate.factors.get("HT_DP_KPA", 1.0) if self.estimate else 1.0
        s_factor = self.estimate.factors.get("GODT.S", 1.0) if self.estimate else 1.0

        corrected_traj: Dict[str, Tuple[float, ...]] = {}
        extrema: Dict[str, Tuple[float, float, int]] = {}

        for k, vals in raw_traj.items():
            if k == "HT_DP_KPA":
                vals_corr = [v * dp_factor for v in vals]
            elif k == "HT_S_PRODUCT":
                vals_corr = [v * s_factor for v in vals]
            else:
                vals_corr = list(vals)

            corrected_traj[k] = tuple(vals_corr)
            if vals_corr:
                min_v = min(vals_corr)
                max_v = max(vals_corr)
                # Индекс шага с худшим отклонением
                worst_idx = vals_corr.index(max_v)
                extrema[k] = (min_v, max_v, worst_idx)
            else:
                ss_v = ss.get(k, 0.0)
                extrema[k] = (ss_v, ss_v, 0)

        # Добавляем алиасы в extrema
        if "HT_S_PRODUCT" in extrema and "GODT.S" not in extrema:
            extrema["GODT.S"] = extrema["HT_S_PRODUCT"]
        if "HT_FLASH" in extrema and "GODT.FLASH" not in extrema:
            extrema["GODT.FLASH"] = extrema["HT_FLASH"]
        if "HT_T_OUT" in extrema and "HT_T11" not in extrema:
            extrema["HT_T11"] = extrema["HT_T_OUT"]
        elif "HT_T11" in extrema and "HT_T_OUT" not in extrema:
            extrema["HT_T_OUT"] = extrema["HT_T11"]

        return Prediction(
            candidate=cand.signature,
            horizon_steps=horizon_steps,
            steady_state=ss,
            trajectory_extrema=extrema,
            trajectory=corrected_traj if store_traj else None,
        )

    def _calc_slack(self, u_dict: Dict[str, float], spec: ConstraintSpec) -> float:
        """Вспомогательный расчет запаса ограничения для уставки u."""
        raw_ss = self.twin.steady_state(u_dict)
        ss = self._apply_corrections(raw_ss)

        alias_map = {
            "GODT.S": "HT_S_PRODUCT",
            "GODT.FLASH": "HT_FLASH",
            "GODT.T95": "HT_T95_PRODUCT",
            "GODT.D15": "HT_D15_PRODUCT",
            "GODT.CFPP": "HT_CFPP_PRODUCT",
            "GODT.CN": "HT_CN_PRODUCT",
            "HT_T_OUT": "HT_T11",
            "HT_T11": "HT_T_OUT",
        }
        val = ss.get(spec.quantity)
        if val is None and spec.quantity in alias_map:
            val = ss.get(alias_map[spec.quantity])
        if val is None:
            val = u_dict.get(spec.quantity, 0.0)

        if spec.sense == "max":
            slack = (spec.limit - val) / max(spec.scale, 1e-4)
        else:
            slack = (val - spec.limit) / max(spec.scale, 1e-4)
        return float(slack)

    def slack_gradient(self, cand: Candidate, spec: ConstraintSpec) -> Dict[str, float]:
        """
        Вычисляет конечно-разностный градиент запаса ограничения d(slack)/d(Delta u_j)
        по 5 управляющим переменным (MV) с кэшированием по (signature, spec.key).
        """
        cache_key = (cand.signature, spec.key)
        if cache_key in self._gradient_cache:
            return dict(self._gradient_cache[cache_key])

        # Если ограничение структурно не зависит от управляемых переменных, градиент нулевой
        grad: Dict[str, float] = {}
        applicable_mvs = [mv for mv in MV_NAMES if mv in spec.depends_on]
        if not applicable_mvs:
            self._gradient_cache[cache_key] = grad
            return grad

        u_base = self._extract_target_u(cand)

        for mv in applicable_mvs:
            h = FD_STEPS.get(mv, 1.0)
            u_plus = dict(u_base)
            u_plus[mv] = u_base.get(mv, 0.0) + h

            u_minus = dict(u_base)
            u_minus[mv] = u_base.get(mv, 0.0) - h

            slack_plus = self._calc_slack(u_plus, spec)
            slack_minus = self._calc_slack(u_minus, spec)

            d_slack = (slack_plus - slack_minus) / (2.0 * h)
            grad[mv] = round(float(d_slack), 5)

        self._gradient_cache[cache_key] = grad
        return grad

    def with_params(self, theta_params: Mapping[str, float]) -> TwinView:
        """
        Создает копию TwinView с обновленными параметрами кинетики или откликов
        (для оценки сценариев ансамбля печи П-3).
        """
        new_params = copy.deepcopy(self.twin.params)
        for k, v in theta_params.items():
            if hasattr(new_params.feed, k):
                setattr(new_params.feed, k, v)
            elif hasattr(new_params.reactor, k):
                setattr(new_params.reactor, k, v)
            elif hasattr(new_params.stabilizer, k):
                setattr(new_params.stabilizer, k, v)
            elif hasattr(new_params.dynamics, k):
                setattr(new_params.dynamics, k, v)

        new_twin = FullChainTwin(new_params)
        # Копируем текущие состояния и уставки
        new_twin._u_current = dict(self.twin._u_current)
        new_twin.biases = dict(self.twin.biases)

        return TwinView(twin=new_twin, estimate=self.estimate, policy=self.policy)
