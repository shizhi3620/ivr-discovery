"""AI Provider boundary.

Transcript parsing needs a text-completion model, but the project spans two
regions with different model availability: Anthropic (original demo) and
DeepSeek (China mainland). This boundary keeps the parser independent of which
vendor answers, mirroring the telephony Provider seam (docs/adr/0004).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AICapabilities:
    """What a model backend can actually do, so callers don't over-ask."""

    # Supports a structured JSON-output mode (not just prompt-and-hope).
    json_mode: bool = False


class AIProvider(Protocol):
    """Produces a single text completion for a prompt."""

    name: str
    capabilities: AICapabilities

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        """Return the model's raw text response.

        Callers must still defend against malformed output; `json_mode` is a
        hint, not a guarantee across providers.
        """
        ...


__all__ = ["AIProvider", "AICapabilities"]
