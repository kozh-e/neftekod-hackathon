"""Хранилище сессионных экземпляров StateEstimator между тактами управления (P1.5).

Аналог src/twin/session.py::TwinSessionStore, но для калибровки качества: StateEstimator
хранит скользящие 48ч буферы и Калман-состояние смещений ПАК/модели, которые должны
переживать между вызовами graph.invoke() в рамках одной сессии, иначе калибровка
"забывается" и пересчитывается с нуля на каждом такте (см. память backend-audit-2026-09-20).

Твин этому модулю не принадлежит - вызывающий код (graph.py::node_estimate_core) строит
его один раз за цикл и передает сырой (twin_raw_output) и текущий (u_twin_current) вывод
сюда, чтобы не пересобирать твин отдельно ради калибровки (см. ESTIMATOR_GOVERNED_BIAS_KEYS
ниже - вызывающий код обязан обнулить эти ключи в twin.biases ПЕРЕД вызовом steady_state(),
иначе калибровка задвоится с наивной поправкой chain.py::assimilate()).
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Mapping, Optional

from src.agents.contracts import PlantEstimate
from src.agents.estimation import LimsSample, StateEstimator

# Свойства качества, калибровку которых берет на себя StateEstimator (Калман-фильтр с
# ростом неопределенности по возрасту калибровки). Для остальных ключей BIAS_SOURCES
# твина (HT_S_FEED, HT_T_OUT, HT_DP_KPA) StateEstimator калибровки не считает, поэтому
# наивная поправка chain.py::assimilate() остается для них единственным источником -
# ее не трогаем, обнуляем только пересекающиеся ключи, чтобы не корректировать дважды.
ESTIMATOR_GOVERNED_BIAS_KEYS: tuple[str, ...] = (
    "HT_S_PRODUCT", "HT_FLASH", "HT_D15_PRODUCT", "HT_T95_PRODUCT",
)

# Тег ЛИМС -> свойство качества (см. src/agents/contracts.py::QualityProp)
LIMS_TAG_MAP: Dict[str, str] = {
    "LIMS_HT_S": "S",
    "LIMS_HT_FLASH": "FLASH",
    "LIMS_HT_D15": "D15",
    "LIMS_HT_T95": "T95",
    "LIMS_HT_CFPP": "CFPP",
    "LIMS_HT_CN": "CN",
}

# Проба ЛИМС в тегах не имеет отдельного available_at - в этот же такт, когда она
# впервые видна в tags, для дедупликации считаем ее "той же", если момент отбора
# (вычисленный по lims_age_hours) сместился меньше чем на эту дельту.
SAME_SAMPLE_TOLERANCE_S: float = 60.0


class StateEstimatorSessionStore:
    """Сессионное хранилище StateEstimator с дедупликацией проб ЛИМС по моменту отбора."""

    def __init__(self) -> None:
        self._estimators: Dict[str, StateEstimator] = {}
        self._last_sample_at: Dict[str, Dict[str, datetime.datetime]] = {}

    def _build_lims_samples(
        self,
        session_id: Optional[str],
        tags: Mapping[str, float],
        t_now: datetime.datetime,
    ) -> List[LimsSample]:
        lims_age_h = tags.get("lims_age_hours")
        if lims_age_h is None or not isinstance(lims_age_h, (int, float)):
            lims_age_h = 0.0
        sampled_at = t_now - datetime.timedelta(hours=max(0.0, float(lims_age_h)))
        seen = self._last_sample_at.setdefault(session_id, {}) if session_id else {}

        samples: List[LimsSample] = []
        for tag, prop in LIMS_TAG_MAP.items():
            val = tags.get(tag)
            if val is None or not isinstance(val, (int, float)):
                continue
            fval = float(val)
            if fval != fval or fval in (float("inf"), float("-inf")):
                continue
            last_seen = seen.get(prop)
            if last_seen is not None and abs((last_seen - sampled_at).total_seconds()) < SAME_SAMPLE_TOLERANCE_S:
                continue
            samples.append(LimsSample(prop=prop, value=fval, sampled_at=sampled_at, available_at=t_now))
            if session_id:
                seen[prop] = sampled_at
        return samples

    def get(
        self,
        session_id: Optional[str],
        tags: Mapping[str, float],
        twin_raw_output: Mapping[str, float],
        u_twin_current: Mapping[str, float],
        t_now: Optional[datetime.datetime] = None,
    ) -> PlantEstimate:
        """Возвращает откалиброванный PlantEstimate за текущий такт.

        twin_raw_output/u_twin_current - вывод твина, который вызывающий код уже построил
        для этого такта (см. docstring модуля про обнуление ESTIMATOR_GOVERNED_BIAS_KEYS).
        """
        now = t_now or datetime.datetime.now(datetime.timezone.utc)

        if session_id is None:
            estimator = StateEstimator()
        elif session_id not in self._estimators:
            estimator = StateEstimator()
            self._estimators[session_id] = estimator
        else:
            estimator = self._estimators[session_id]

        new_lims = self._build_lims_samples(session_id, tags, now)

        return estimator.update(
            tags=tags,
            t_now=now,
            twin_raw_output=twin_raw_output,
            u_twin_current=u_twin_current,
            new_lims=new_lims,
        )

    def reset(self, session_id: str) -> None:
        """Сброс сессии (например, при разрыве > MAX_GAP_HOURS в TWIN_STORE)."""
        self._estimators.pop(session_id, None)
        self._last_sample_at.pop(session_id, None)


# Модульный синглтон сессионного хранилища
ESTIMATOR_STORE = StateEstimatorSessionStore()
