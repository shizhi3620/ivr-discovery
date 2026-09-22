"""AI Provider selection.

`get_ai_provider()` is the single seam transcript parsing uses. It reads the
`AI_PROVIDER` env var and defaults to DeepSeek for the China mainland scenario.
Anthropic remains available as an explicit alternative.
"""

from __future__ import annotations

import os

from ai.base import AIProvider, AICapabilities
from ai.anthropic_provider import AnthropicProvider
from ai.deepseek_provider import DeepSeekProvider

DEFAULT_PROVIDER = "deepseek"

_instances: dict[str, AIProvider] = {}


def get_ai_provider(name: str | None = None, *, force_new: bool = False) -> AIProvider:
    """Return the configured AI provider.

    Selection order: explicit `name`, then `AI_PROVIDER`, then `deepseek`.
    """
    resolved = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

    if not force_new and resolved in _instances:
        return _instances[resolved]

    provider = _build(resolved)
    _instances[resolved] = provider
    return provider


def _build(name: str) -> AIProvider:
    if name == "deepseek":
        return DeepSeekProvider()
    if name in ("anthropic", "claude"):
        return AnthropicProvider()
    raise ValueError(f"Unknown AI provider {name!r}. Known providers: deepseek, anthropic.")


def reset_ai_providers() -> None:
    """Drop cached provider instances. Intended for tests."""
    _instances.clear()


__all__ = [
    "AIProvider",
    "AICapabilities",
    "AnthropicProvider",
    "DeepSeekProvider",
    "get_ai_provider",
    "reset_ai_providers",
    "DEFAULT_PROVIDER",
]
