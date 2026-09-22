"""Anthropic implementation of the AI Provider boundary.

Anthropic remains available for the original demo and for users outside the
China mainland scenario, but it is not the default provider.
"""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

from ai.base import AICapabilities

DEFAULT_MODEL = "claude-sonnet-4-20250514"


class AnthropicProvider:
    """AI provider backed by Anthropic's Messages API."""

    name = "anthropic"
    capabilities = AICapabilities(json_mode=False)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._client = client

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        if json_mode and not self.capabilities.json_mode:
            # Prompt-level JSON instructions remain in transcript_parser; the
            # Messages API has no response_format equivalent for this call site.
            json_mode = False

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            return response.content[0].text.strip()
        except (AttributeError, IndexError) as exc:
            raise RuntimeError("Anthropic response did not contain text content") from exc


__all__ = ["AnthropicProvider"]
