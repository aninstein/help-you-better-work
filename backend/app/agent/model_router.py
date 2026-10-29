"""Startup-loaded model configuration and ordered failover for all Agent calls."""

import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.domain.models import ModelSettings

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logger = logging.getLogger(__name__)
T = TypeVar("T")


class ModelsUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "所有 AI 模型暂时不可用，已依次尝试备用模型。请稍后重试或检查服务端额度与配置。"
        )


@dataclass
class StreamPart:
    text: str
    reset: bool = False


def load_models() -> list[ModelSettings]:
    raw = os.getenv("MODEL_CONFIG_JSON", "")
    official_key = os.getenv("DEEPSEEK_OFFICIAL_API_KEY", "")
    common = {
        "temperature": float(os.getenv("MODEL_TEMPERATURE", "0.3")),
        "stt_provider": os.getenv("STT_PROVIDER", "local"),
        "stt_model": os.getenv("STT_MODEL", "whisper-1"),
    }
    if raw:
        models = [
            ModelSettings.model_validate({**common, **item})
            for item in json.loads(raw)
            if item.get("enabled", True)
        ]
    elif not official_key:
        # A one-model Ollama configuration also works without an external account.
        models = [
            ModelSettings(
                **common,
                provider="ollama",
                model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            )
        ]
    else:
        models = []
    if official_key:
        official_model = os.getenv("DEEPSEEK_OFFICIAL_MODEL", "deepseek-flash")
        official = ModelSettings(
            provider="openai",
            model=official_model,
            model_label=(
                "DeepSeek Flash (official)"
                if official_model == "deepseek-flash"
                else "DeepSeek V4 Pro (official)"
            ),
            base_url=os.getenv("DEEPSEEK_OFFICIAL_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=official_key,
            **common,
        )
        models = [
            official,
            *[
                model
                for model in models
                if (model.base_url, model.model) != (official.base_url, official.model)
            ],
        ]
    if not models:
        raise ValueError("MODEL_CONFIG_JSON 至少需要配置一个启用的模型。")
    primary = os.getenv("PRIMARY_MODEL", "")
    if primary:
        selected = next((m for m in models if primary in [m.model, m.model_label]), None)
        if selected is None:
            raise ValueError("PRIMARY_MODEL 必须对应 MODEL_CONFIG_JSON 中启用的模型。")
        models = [selected, *(m for m in models if m is not selected)]
    return models


def build_model(settings: ModelSettings) -> Any:
    timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "35"))
    if settings.provider == "ollama":
        return ChatOllama(
            model=settings.model,
            base_url=settings.base_url,
            temperature=settings.temperature,
            num_ctx=8192,
            num_predict=2000,
            client_kwargs={"timeout": timeout},
        )
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key or "local",
        timeout=timeout,
        max_retries=0,
        temperature=settings.temperature,
        max_tokens=4096,
        extra_body=(
            {"thinking": {"type": "disabled"}}
            if "ark" in settings.base_url or "deepseek.com" in settings.base_url
            else None
        ),
    )


class ModelRouter:
    def __init__(self, models: list[ModelSettings]) -> None:
        self.models = models
        self.clients = [(settings, build_model(settings)) for settings in models]

    @staticmethod
    def failed(settings: ModelSettings, exc: Exception) -> None:
        logger.warning(
            "model_call_failed model=%s error=%s",
            settings.model_label or settings.model,
            type(exc).__name__,
        )

    async def run(self, operation: Callable[[Any, ModelSettings], Awaitable[T]]) -> T:
        for settings, model in self.clients:
            try:
                return await operation(model, settings)
            except Exception as exc:
                self.failed(settings, exc)
        raise ModelsUnavailable()

    async def stream(
        self, operation: Callable[[Any, ModelSettings], AsyncIterator[str]]
    ) -> AsyncIterator[StreamPart]:
        for index, (settings, model) in enumerate(self.clients):
            if index:
                yield StreamPart("", reset=True)
            produced = False
            try:
                async for chunk in operation(model, settings):
                    if chunk:
                        produced = True
                        yield StreamPart(chunk)
                if not produced:
                    raise ValueError("empty_model_response")
                return
            except Exception as exc:
                self.failed(settings, exc)
        yield StreamPart("", reset=True)
        raise ModelsUnavailable()
