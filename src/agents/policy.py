"""Конфигурация технологической политики, пороги автоматизации и PolicyStore.

Соответствует спецификации §4.7 implementation_plan_v3.md.
Реализует белый список параметров (POLICY_WHITELIST), доступных для изменения
супервизором или технологом, и версионирование политик через JSON-хранилище.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple
from pydantic import Field

from src.agents.contracts import Frozen


class AutomationThresholds(Frozen):
    """Пороги лестницы деградации автоматизации по возрасту ЛИМС."""
    cautious_calib_age_h: float = 8.0          # POLICY: переход в CAUTIOUS (шаг 50%)
    corrective_only_calib_age_h: float = 16.0  # POLICY: переход в CORRECTIVE_ONLY
    refusal_lims_age_h_without_pak: float = 24.0 # NORM/POLICY: отказ ТЗ при отсутствии исправного ПАК


class PolicyConfig(Frozen):
    """Параметры технологической политики ядра управления."""
    version: str = "1.0.0"
    alpha_quality: float = 0.0228              # z ≈ 2.0 (квантиль 1-стороннего риска ГОСТ)
    alpha_equipment: float = 0.00135            # z ≈ 3.0 (квантиль 1-стороннего риска ПАЗ)
    deadband_rub_h: float = 1000.0             # зона нечувствительности по марже, руб/ч
    min_move_norm: float = 0.05                # минимальная норма хода для применения
    cautious_step_scale: float = 0.5           # масштаб шага в режиме CAUTIOUS
    max_rounds: int = 4                        # макс. число раундов переговоров
    soft_budget_s: float = 2.0                 # мягкий бюджет времени цикла, с
    hard_budget_s: float = 10.0                # жесткий бюджет времени цикла, с
    recovery_rho: float = 0.9                  # требуемое убывание нарушения за шаг восстановления
    recovery_max_steps: int = 6                # макс. шагов плана восстановления
    furnace_ensemble_size: int = 40            # размер ансамбля печи П-3 (ADR-19)
    furnace_benefit_confidence: float = 0.8    # порог уверенности экономической выгоды печи
    dilution_policy: Literal["GODT_ON_SPEC", "ALLOW_WITHIN_PRODUCT_BUDGET"] = "GODT_ON_SPEC"
    catalyst_wear_rub_h_per_c: float = 450.0   # штраф старения катализатора за нагрев, руб/(ч·°C)
    move_cost_rub_h_per_unit_norm: float = 0.0 # штраф за перемещение арматуры
    cfpp_margin_c: float = 1.0                 # запас по температуре фильтруемости, °C
    heating_warning_zone_c: float = 380.0      # порог зоны предупреждения печи П-3 (POLICY)
    pause_economics_on_uncontrollable_t1: bool = True
    buffer_bounds_t: tuple[float, float] = (-300.0, 300.0) # границы буфера сырья АВТ-ГО, т
    h_plan_h: float = 8.0                      # горизонт планирования сырьевого буфера, ч
    thresholds: AutomationThresholds = AutomationThresholds()


class WhitelistEntry(Frozen):
    """Элемент белого списка разрешенных к изменению параметров политики."""
    field: str
    lo: float
    hi: float
    direction: Literal["tighten_only", "both"]


POLICY_WHITELIST: tuple[WhitelistEntry, ...] = (
    WhitelistEntry(field="alpha_quality", lo=0.00135, hi=0.0228, direction="tighten_only"),
    WhitelistEntry(field="deadband_rub_h", lo=500.0, hi=5000.0, direction="both"),
    WhitelistEntry(field="cautious_step_scale", lo=0.25, hi=0.5, direction="tighten_only"),
    WhitelistEntry(field="catalyst_wear_rub_h_per_c", lo=225.0, hi=900.0, direction="both"),
    WhitelistEntry(field="cautious_calib_age_h", lo=4.0, hi=8.0, direction="tighten_only"),
    WhitelistEntry(field="furnace_benefit_confidence", lo=0.8, hi=0.95, direction="tighten_only"),
)

WHITELIST_BY_FIELD: dict[str, WhitelistEntry] = {entry.field: entry for entry in POLICY_WHITELIST}


class PolicyStore:
    """Хранилище версий технологической политики на диске с валидацией белого списка."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent.parent / "config" / "policy"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._active_policy: PolicyConfig = self._init_default_policy()

    def _init_default_policy(self) -> PolicyConfig:
        default_file = self.base_dir / "policy_v1.json"
        if default_file.exists():
            try:
                return self.load_from_file(default_file)
            except Exception:
                pass
        config = PolicyConfig()
        self.save_to_file(config, default_file)
        return config

    @property
    def active_policy(self) -> PolicyConfig:
        return self._active_policy

    def set_active_policy(self, policy: PolicyConfig) -> None:
        self._active_policy = policy

    def load_from_file(self, path: Path) -> PolicyConfig:
        """Загружает политику из JSON файла."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        thresholds_data = data.pop("thresholds", {})
        thresholds = AutomationThresholds(**thresholds_data) if thresholds_data else AutomationThresholds()
        return PolicyConfig(**data, thresholds=thresholds)

    def save_to_file(self, policy: PolicyConfig, path: Path) -> None:
        """Сохраняет политику в JSON файл с детерминированной сортировкой ключей."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(policy.model_dump_json(indent=2))

    def validate_patch(
        self, current: PolicyConfig, field: str, new_value: float
    ) -> Tuple[bool, str]:
        """
        Проверяет допустимость изменения поля согласно POLICY_WHITELIST:
        - Поле должно присутствовать в белом списке;
        - Значение должно попадать в диапазон [lo, hi];
        - Для tighten_only допускается только ужесточение (уменьшение alpha/cautious_calib_age_h, увеличение confidence).
        """
        entry = WHITELIST_BY_FIELD.get(field)
        if entry is None:
            return False, f"Поле '{field}' отсутствует в белом списке разрешенных параметров политики (POLICY_WHITELIST)"

        if not (entry.lo <= new_value <= entry.hi):
            return False, f"Значение {new_value} для поля '{field}' вне допустимого диапазона [{entry.lo}, {entry.hi}]"

        if entry.direction == "tighten_only":
            current_val = getattr(current, field, None)
            if current_val is None and hasattr(current.thresholds, field):
                current_val = getattr(current.thresholds, field)

            if current_val is not None:
                # Ужесточение:
                # alpha_quality: меньшее alpha -> большее z -> строже
                # cautious_calib_age_h: меньший возраст -> раньше осторожность
                # cautious_step_scale: меньший шаг -> консервативнее
                # furnace_benefit_confidence: большая уверенность -> строже
                if field in ("alpha_quality", "cautious_calib_age_h", "cautious_step_scale"):
                    if new_value > current_val + 1e-9:
                        return False, f"Поле '{field}' допускает только ужесточение (уменьшение), текущее={current_val}, предложено={new_value}"
                elif field in ("furnace_benefit_confidence",):
                    if new_value < current_val - 1e-9:
                        return False, f"Поле '{field}' допускает только ужесточение (увеличение), текущее={current_val}, предложено={new_value}"

        return True, "OK"

    def apply_patch(
        self, current: PolicyConfig, patch: Mapping[str, float], new_version: str
    ) -> PolicyConfig:
        """Применяет валидированный патч к текущей конфигурации политики."""
        data = current.model_dump()
        thresholds_data = dict(data.get("thresholds", {}))

        for field, val in patch.items():
            ok, err = self.validate_patch(current, field, val)
            if not ok:
                raise ValueError(err)

            if field in thresholds_data:
                thresholds_data[field] = val
            elif field in data:
                data[field] = val
            elif hasattr(current.thresholds, field):
                thresholds_data[field] = val
            else:
                raise KeyError(f"Неизвестное поле '{field}'")

        data["version"] = new_version
        data["thresholds"] = AutomationThresholds(**thresholds_data)
        return PolicyConfig(**data)


# Синглтон хранилища политик
DEFAULT_POLICY_STORE = PolicyStore()
