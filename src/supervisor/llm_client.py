"""OpenAI-совместимый LLM-клиент с поддержкой детерминированного реплея кассет.

Поддерживает работу с локальными моделями Qwen 27B / 32B через vLLM / Ollama
и 100% автономный запуск на эталонных кассетах (REPLAY_STRICT).
"""

from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel

from src.supervisor.cassettes import (
    CassetteNotFoundError,
    CassetteStore,
    LLMDisabledError,
    calculate_fingerprint,
)


def extract_json_payload(raw_text: str) -> Dict[str, Any]:
    """Извлекает структурированный JSON из текста ответа LLM.
    
    Поддерживает:
    1. Чистый JSON.
    2. Блоки markdown ```json ... ``` или ``` ... ```.
    3. Поиск первого '{' и последнего '}'.
    """
    text = raw_text.strip()
    if not text:
        raise ValueError("Пустой текст ответа LLM, невозможно извлечь JSON")

    # 1. Попытка распарсить напрямую
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # 2. Поиск блоков markdown с json (жадный поиск, т.к. JSON может содержать вложенные объекты)
    markdown_pattern = r"```(?:json)?\s*(\{.*\})\s*```"
    match = re.search(markdown_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Fallback: поиск подстроки от первого '{' до последнего '}'
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = text[start_idx : end_idx + 1].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as err:
            raise ValueError(f"Не удалось декодировать JSON из подстроки: {err}\nТекст: {candidate[:200]}")

    raise ValueError(f"JSON-объект не найден в ответе LLM:\n{text[:200]}")


class LLMMessage:
    """Унифицированное представление сообщения в ответе."""
    def __init__(self, content: str, role: str = "assistant"):
        self.content = content
        self.role = role

    def to_dict(self) -> Dict[str, Any]:
        return {"content": self.content, "role": self.role}


class LLMChoice:
    """Унифицированное представление выбранного ответа."""
    def __init__(self, message: LLMMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason

    def to_dict(self) -> Dict[str, Any]:
        return {"message": self.message.to_dict(), "finish_reason": self.finish_reason}


class LLMResponse:
    """Унифицированный ответ вызова completion."""
    def __init__(
        self,
        choices: List[LLMChoice],
        model: str,
        usage: Optional[Dict[str, int]] = None,
        fingerprint: Optional[str] = None,
    ):
        self.choices = choices
        self.model = model
        self.usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.fingerprint = fingerprint

    @property
    def content(self) -> str:
        if not self.choices:
            return ""
        return self.choices[0].message.content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "choices": [c.to_dict() for c in self.choices],
            "model": self.model,
            "usage": self.usage,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], fingerprint: Optional[str] = None) -> LLMResponse:
        choices = []
        for c in data.get("choices", []):
            msg_data = c.get("message", {})
            msg = LLMMessage(content=msg_data.get("content", ""), role=msg_data.get("role", "assistant"))
            choices.append(LLMChoice(message=msg, finish_reason=c.get("finish_reason", "stop")))
        return cls(
            choices=choices,
            model=data.get("model", "unknown"),
            usage=data.get("usage"),
            fingerprint=fingerprint or data.get("fingerprint"),
        )


class ReplayingOpenAIClient:
    """Клиент LLM с поддержкой OpenAI API и реплея кассет.
    
    Режимы (NEFTEKOD_LLM_MODE):
    - REPLAY_STRICT (по умолчанию): только кассеты из data/llm_cassettes/. Промах -> CassetteNotFoundError.
    - LIVE_RECORD: реальный вызов к API OpenAI/vLLM и сохранение ответа в кассету.
    - OFF: генерация отключена, немедленно поднимает LLMDisabledError.
    """

    def __init__(
        self,
        mode: Optional[str] = None,
        cassette_store: Optional[CassetteStore] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.store = cassette_store or CassetteStore()

        # Ключ: NEFTEKOD_LLM_API_KEY имеет приоритет, но раньше OPENAI_API_KEY вообще не
        # учитывался здесь (хотя has_llm_key в src/console/api.py считает его валидным ключом) —
        # из-за этого при заданном только OPENAI_API_KEY клиент молча уходил в режим без ключа.
        env_neftekod_key = os.environ.get("NEFTEKOD_LLM_API_KEY")
        env_openai_key = os.environ.get("OPENAI_API_KEY")
        key_from_openai_env = bool(
            api_key is None and not (env_neftekod_key and env_neftekod_key != "EMPTY") and env_openai_key
        )
        self.api_key = api_key or env_neftekod_key or env_openai_key or "EMPTY"
        has_real_key = bool(self.api_key and self.api_key != "EMPTY")

        # Режим: явный аргумент/переменная окружения побеждают всегда. Если ни один не задан,
        # но при этом есть реальный ключ — по умолчанию делаем живой вызов (LIVE_RECORD), а не
        # REPLAY_STRICT, иначе наличие ключа никак не влияет на поведение (баг: оператор добавляет
        # ключ, а система по-прежнему молчит про "нужен ключ LLM").
        env_mode = os.environ.get("NEFTEKOD_LLM_MODE")
        if mode:
            self.mode = mode.upper()
        elif env_mode:
            self.mode = env_mode.upper()
        elif has_real_key:
            self.mode = "LIVE_RECORD"
        else:
            self.mode = "REPLAY_STRICT"

        # base_url: явный аргумент/переменная окружения побеждают. Иначе, если ключ пришёл из
        # OPENAI_API_KEY (а не из NEFTEKOD_LLM_API_KEY), это настоящий OpenAI — не подставляем
        # локальный vLLM/Ollama URL по умолчанию.
        env_base_url = os.environ.get("NEFTEKOD_LLM_BASE_URL")
        if base_url:
            self.base_url = base_url
        elif env_base_url:
            self.base_url = env_base_url
        elif key_from_openai_env:
            self.base_url = "https://api.openai.com/v1"
        else:
            self.base_url = "http://localhost:8000/v1"

        env_model = os.environ.get("NEFTEKOD_LLM_MODEL")
        if model:
            self.model = model
        elif env_model:
            self.model = env_model
        elif key_from_openai_env:
            self.model = "gpt-4o-mini"
        else:
            self.model = "Qwen/Qwen2.5-32B-Instruct"

        self._openai_client = None
        if self.mode == "LIVE_RECORD":
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(base_url=self.base_url, api_key=self.api_key)
            except Exception as e:
                # В режиме LIVE_RECORD клиент обязан инициализироваться
                pass

    def create(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        temperature: float = 0.0,
        cassette_name: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Создает completion с использованием OpenAI API или загружает из кассеты."""
        if self.mode == "OFF":
            raise LLMDisabledError("LLM-супервизор отключен (режим NEFTEKOD_LLM_MODE=OFF)")

        target_model = model or self.model
        resp_format = response_format or {"type": "json_object"}

        params = {
            "model": target_model,
            "messages": messages,
            "response_format": resp_format,
            "temperature": temperature,
        }

        fingerprint = calculate_fingerprint(params)

        if self.mode == "REPLAY_STRICT":
            # 1. Попытка найти по явному имени кассеты (если передано)
            if cassette_name:
                try:
                    cassette_data = self.store.load_by_name(cassette_name)
                    return LLMResponse.from_dict(cassette_data["response"], fingerprint=fingerprint)
                except CassetteNotFoundError:
                    pass

            # 2. Поиск по отпечатку параметров
            if self.store.has(fingerprint):
                cassette_data = self.store.load(fingerprint)
                return LLMResponse.from_dict(cassette_data["response"], fingerprint=fingerprint)

            raise CassetteNotFoundError(
                f"Кассета не найдена для отпечатка '{fingerprint}' "
                f"(модель: {target_model}, cassette_name: {cassette_name})"
            )

        if self.mode == "LIVE_RECORD":
            if self._openai_client is None:
                from openai import OpenAI
                self._openai_client = OpenAI(base_url=self.base_url, api_key=self.api_key)

            resp = self._openai_client.chat.completions.create(
                model=target_model,
                messages=messages,
                response_format=resp_format,
                temperature=temperature,
                **kwargs,
            )

            # Конвертируем ответ OpenAI в сериализуемый словарь
            resp_content = resp.choices[0].message.content or ""
            resp_dict = {
                "choices": [
                    {
                        "message": {"content": resp_content, "role": "assistant"},
                        "finish_reason": resp.choices[0].finish_reason or "stop",
                    }
                ],
                "model": resp.model,
                "usage": {
                    "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                    "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                    "total_tokens": resp.usage.total_tokens if resp.usage else 0,
                },
            }

            self.store.save(
                fingerprint=fingerprint,
                request_params=params,
                response_data=resp_dict,
                name=cassette_name,
            )

            return LLMResponse.from_dict(resp_dict, fingerprint=fingerprint)

        raise ValueError(f"Неизвестный режим LLM-клиента: {self.mode}")
