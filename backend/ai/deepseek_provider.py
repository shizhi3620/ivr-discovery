"""DeepSeek implementation of the AI Provider boundary.

DeepSeek exposes an OpenAI-compatible `/chat/completions` endpoint. The default
model is `deepseek-flash`; callers can override it with `DEEPSEEK_MODEL` because
DeepSeek model names have changed over time.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ai.base import AICapabilities

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 60.0


class DeepSeekProvider:
    """AI provider for DeepSeek's OpenAI-compatible chat API."""

    name = "deepseek"
    capabilities = AICapabilities(json_mode=True)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._client = client

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when AI_PROVIDER=deepseek")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if json_mode:
            if not self.capabilities.json_mode:
                raise ValueError(f"{self.name} does not support JSON mode")
            payload["response_format"] = {"type": "json_object"}

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return _extract_message_content(data)
        finally:
            if owns_client:
                await client.aclose()


def _extract_message_content(data: Any) -> str:
    """Extract text from an OpenAI-compatible chat completion payload."""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek response did not contain message content") from exc

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek returned an empty message")
    return content.strip()


__all__ = ["DeepSeekProvider"]
