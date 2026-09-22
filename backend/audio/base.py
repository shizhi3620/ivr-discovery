"""Audio Provider boundary.

Telephony Providers own the call leg; this boundary owns speech processing that
is independent of the carrier. The first implementation uses Tencent Cloud
because its 8 kHz Mandarin model is appropriate for China mainland IVR audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class AudioProviderError(RuntimeError):
    """Raised when speech processing cannot be completed."""


@dataclass(frozen=True)
class AudioCapabilities:
    """Speech-processing abilities exposed to call providers."""

    transcription: bool = False
    synthesis: bool = False


@runtime_checkable
class AudioProvider(Protocol):
    """Transcribes recorded call audio and synthesizes speech."""

    name: str
    capabilities: AudioCapabilities

    @property
    def is_configured(self) -> bool:
        """Whether the provider has enough configuration to make API calls."""
        ...

    async def transcribe(self, audio_path: str | Path) -> str:
        """Return text for a local call recording."""
        ...

    async def synthesize(self, text: str, output_path: str | Path) -> Path:
        """Write synthesized speech to ``output_path`` and return that path."""
        ...


__all__ = [
    "AudioCapabilities",
    "AudioProvider",
    "AudioProviderError",
]
