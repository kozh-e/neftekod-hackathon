"""Имитатор технологической установки для реплея сценариев и тестов замкнутого контура (PlantSimulator).

Установка моделируется отдельным экземпляром FullChainTwin с той же динамикой (FOPDT, запаздывания), что и
двойник МАС: измерения КИП запаздывают за уставками физически согласованно. Мгновенный пересчет измерений
в стенде (например, «сера −0.5 ppm на °C сразу после хода») противоречит динамике двойника и порождает ложный
дребезг уставок через коррекцию смещения (bias).
"""

from __future__ import annotations

import copy
from typing import Dict, Mapping, Optional

import numpy as np

from src.twin.chain import FullChainTwin
from src.twin.params import TwinParams, load_params

MV_TAGS: Dict[str, str] = {
    "HT_FEED_SP": "HT_F9",
    "HT_TIN_SP": "HT_T6",
    "HT_P_SP": "HT_P13",
    "HT_GOR_SP": "HT_GOR",
    "AVT_T55_SP": "AVT_T55",
}

OUTPUT_TAGS: Dict[str, tuple] = {
    "HT_S_PRODUCT": ("HT_Q21", "LIMS_HT_S"),
    "HT_T_OUT": ("HT_T11",),
    "HT_FLASH": ("HT_T18",),
}


class PlantSimulator:
    """Установка-имитатор: шаг dt с текущими уставками, выдача тегов КИП, применение одобренных ходов."""

    def __init__(
        self,
        tags: Mapping[str, float],
        params: Optional[TwinParams] = None,
        q21_noise_ppm: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.twin = FullChainTwin(copy.deepcopy(params or load_params()))
        self.twin.initialize(tags)
        self.tags: Dict[str, float] = dict(tags)
        self.q21_noise_ppm = q21_noise_ppm
        self.rng = np.random.default_rng(seed)

    def measure(self) -> Dict[str, float]:
        """Один такт установки и срез телеметрии (с шумом анализатора серы)."""
        out = self.twin.step(self.twin.u_current)
        for key, tag_names in OUTPUT_TAGS.items():
            for name in tag_names:
                self.tags[name] = float(out[key])
        for mv, tag in MV_TAGS.items():
            self.tags[tag] = float(self.twin.u_current[mv])
        snapshot = dict(self.tags)
        if self.q21_noise_ppm > 0.0:
            snapshot["HT_Q21"] += float(self.rng.normal(0.0, self.q21_noise_ppm))
        return snapshot

    def apply(self, delta_u: Mapping[str, float]) -> None:
        """Применение одобренного оператором вектора приращений уставок."""
        for mv, delta in delta_u.items():
            if mv in self.twin._u_current:
                self.twin._u_current[mv] += float(delta)
