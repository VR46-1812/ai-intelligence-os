"""Typed, localhost-only, single-generation Ollama client."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from app.analysis.models import ModelStatus
from app.config import GenerationModelProfile, ResourceBudgetSettings


class OllamaError(RuntimeError):
    def __init__(self, code: str, safe_detail: str) -> None:
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail


@dataclass(frozen=True, slots=True)
class OllamaGeneration:
    response_text: str
    duration_ms: int
    prompt_tokens: int
    output_tokens: int


class _RuntimePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: str = "unknown"


class _ModelPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    size_vram: int = 0


class _ModelsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    models: tuple[_ModelPayload, ...] = ()


class ScoutGenerator(Protocol):
    async def status(self, model: str) -> ModelStatus: ...

    async def generate(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        profile: GenerationModelProfile,
    ) -> OllamaGeneration: ...


class OllamaClient:
    """Call only a loopback Ollama runtime and unload the model after each response."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        generation_semaphore: asyncio.Semaphore,
        resources: ResourceBudgetSettings,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama base URL must be a local HTTP loopback endpoint")
        if resources.llm_generation_concurrency != 1:
            raise ValueError("Ollama generation concurrency must be exactly one")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._generation_semaphore = generation_semaphore
        self._resources = resources

    async def status(self, model: str) -> ModelStatus:
        try:
            version_response = await self._client.get(f"{self._base_url}/api/version")
            tags_response = await self._client.get(f"{self._base_url}/api/tags")
            ps_response = await self._client.get(f"{self._base_url}/api/ps")
            version_response.raise_for_status()
            tags_response.raise_for_status()
            ps_response.raise_for_status()
            version_payload = _RuntimePayload.model_validate(version_response.json())
            tags_payload = _ModelsPayload.model_validate(tags_response.json())
            ps_payload = _ModelsPayload.model_validate(ps_response.json())
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError):
            return ModelStatus(
                available=False,
                model=model,
                model_installed=False,
                detail="The local Ollama runtime is unavailable.",
            )
        installed = {item.name for item in tags_payload.models}
        active_models = list(ps_payload.models)
        active = any(item.name == model for item in active_models)
        size_vram = sum(item.size_vram for item in active_models if item.name == model)
        model_installed = model in installed
        return ModelStatus(
            available=True,
            model=model,
            model_installed=model_installed,
            runtime_version=version_payload.version,
            active=active,
            size_vram_mb=max(0, size_vram // (1024 * 1024)),
            detail=(
                "Scout model is installed and ready on demand."
                if model_installed
                else "The configured Scout model is not installed in Ollama."
            ),
        )

    async def generate(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        profile: GenerationModelProfile,
    ) -> OllamaGeneration:
        runtime = await self.status(profile.model)
        if not runtime.available:
            raise OllamaError("OLLAMA_UNAVAILABLE", runtime.detail)
        if not runtime.model_installed:
            raise OllamaError("MODEL_MISSING", runtime.detail)
        if runtime.size_vram_mb > self._resources.vram_target_mb:
            raise OllamaError("VRAM_BUDGET", "The active model exceeds the configured VRAM target.")
        payload = {
            "model": profile.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": profile.keep_alive_seconds,
            "options": {
                "num_ctx": profile.maximum_context_tokens,
                "num_predict": profile.maximum_output_tokens,
                "temperature": profile.temperature,
            },
        }
        try:
            async with self._generation_semaphore:
                response = await self._client.post(f"{self._base_url}/api/generate", json=payload)
                if response.status_code == 400 and "parse grammar" in response.text.casefold():
                    # Ollama's grammar compiler supports less JSON Schema than Pydantic.
                    # JSON mode still constrains syntax; typed validation remains authoritative.
                    payload["format"] = "json"
                    payload["prompt"] = (
                        prompt
                        + "\nREQUIRED_JSON_SCHEMA:\n"
                        + json.dumps(schema, separators=(",", ":"), ensure_ascii=True)
                    )
                    response = await self._client.post(
                        f"{self._base_url}/api/generate", json=payload
                    )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException as error:
            raise OllamaError("GENERATION_TIMEOUT", "Local model generation timed out.") from error
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise OllamaError(
                "GENERATION_FAILED", "The local model returned an unusable response."
            ) from error
        response_text = result.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            raise OllamaError("EMPTY_RESPONSE", "The local model returned no structured output.")
        return OllamaGeneration(
            response_text=response_text,
            duration_ms=max(0, int(result.get("total_duration", 0)) // 1_000_000),
            prompt_tokens=max(0, int(result.get("prompt_eval_count", 0))),
            output_tokens=max(0, int(result.get("eval_count", 0))),
        )
