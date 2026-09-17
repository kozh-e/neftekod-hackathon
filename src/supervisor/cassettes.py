"""Хранилище эталонных кассет LLM-запросов и ответов (CassetteStore).

Обеспечивает 100% детерминированное воспроизведение (REPLAY_STRICT)
без обращения к внешней сети или локальной GPU.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


class CassetteNotFoundError(FileNotFoundError):
    """Кассета с заданным отпечатком не найдена в хранилище."""
    pass


class LLMDisabledError(RuntimeError):
    """LLM отключен конфигурацией NEFTEKOD_LLM_MODE=OFF."""
    pass


def canonical_json(obj: Any) -> str:
    """Каноническая сериализация JSON с сортировкой ключей и компактными разделителями."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def calculate_fingerprint(params: Dict[str, Any]) -> str:
    """Вычисляет SHA-256 хэш от канонического представления параметров запроса."""
    # Нормализуем параметры для исключения несущественных различий
    normalized = {}
    for key in sorted(params.keys()):
        val = params[key]
        if key == "messages":
            # Нормализация сообщений
            norm_messages = []
            for msg in val:
                norm_messages.append({
                    "role": msg.get("role", ""),
                    "content": str(msg.get("content", "")).strip(),
                })
            normalized["messages"] = norm_messages
        elif key in ("model", "temperature", "response_format"):
            normalized[key] = val
        elif key == "system":
            normalized["system"] = str(val).strip()

    payload_str = canonical_json(normalized)
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()


class CassetteStore:
    """Файловое хранилище кассет запросов и ответов LLM."""

    def __init__(self, directory: Optional[Path] = None):
        if directory is None:
            directory = Path(__file__).resolve().parent.parent.parent / "data" / "llm_cassettes"
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _find_file(self, filename: str) -> Optional[Path]:
        """Поиск файла в директории и подпапках (например, demo/)."""
        target = self.directory / filename
        if target.exists():
            return target
        for p in self.directory.rglob(filename):
            if p.is_file():
                return p
        return None

    def has(self, fingerprint: str) -> bool:
        """Проверяет наличие кассеты по отпечатку."""
        return self._find_file(f"{fingerprint}.json") is not None

    def load(self, fingerprint: str) -> Dict[str, Any]:
        """Загружает кассету по отпечатку. Вызывает CassetteNotFoundError при промахе."""
        file_path = self._find_file(f"{fingerprint}.json")
        if not file_path:
            raise CassetteNotFoundError(
                f"Кассета для отпечатка '{fingerprint}' не найдена в {self.directory}"
            )
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_by_name(self, name: str) -> Dict[str, Any]:
        """Загружает кассету по имени сценария (например, 'briefing_demo')."""
        clean_name = name if name.endswith(".json") else f"{name}.json"
        file_path = self._find_file(clean_name)
        if not file_path:
            raise CassetteNotFoundError(
                f"Кассета с именем '{name}' не найдена в {self.directory}"
            )
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(
        self,
        fingerprint: str,
        request_params: Dict[str, Any],
        response_data: Dict[str, Any],
        name: Optional[str] = None,
        subdir: Optional[str] = None,
    ) -> Path:
        """Сохраняет пару запрос-ответ в JSON-файл кассеты."""
        target_dir = self.directory / subdir if subdir else self.directory
        target_dir.mkdir(parents=True, exist_ok=True)

        cassette_data = {
            "fingerprint": fingerprint,
            "name": name,
            "request": request_params,
            "response": response_data,
        }

        # Основное сохранение по отпечатку
        fp_path = target_dir / f"{fingerprint}.json"
        with open(fp_path, "w", encoding="utf-8") as f:
            json.dump(cassette_data, f, ensure_ascii=False, indent=2)

        # Если задано человекочитаемое имя, сохраняем также или линкуем по имени
        if name:
            clean_name = name if name.endswith(".json") else f"{name}.json"
            name_path = target_dir / clean_name
            with open(name_path, "w", encoding="utf-8") as f:
                json.dump(cassette_data, f, ensure_ascii=False, indent=2)

        return fp_path
