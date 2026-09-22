"""Audio Provider selection.

``get_audio_provider()`` is the single seam used by the Android SIM gateway.
Tencent Cloud is the default because the project targets China mainland calls.
"""

from __future__ import annotations

import os

from audio.base import (
    AudioCapabilities,
    AudioProvider,
    AudioProviderError,
)
from audio.tencent_provider import TencentAudioProvider

DEFAULT_PROVIDER = "tencent"

_instances: dict[str, AudioProvider] = {}


def get_audio_provider(
    name: str | None = None,
    *,
    force_new: bool = False,
) -> AudioProvider:
    """Return the configured audio provider.

    Selection order: explicit ``name``, then ``AUDIO_PROVIDER``, then Tencent.
    """
    resolved = (name or os.getenv("AUDIO_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

    if not force_new and resolved in _instances:
        return _instances[resolved]

    provider = _build(resolved)
    _instances[resolved] = provider
    return provider


def _build(name: str) -> AudioProvider:
    if name == "tencent":
        return TencentAudioProvider()
    raise ValueError(
        f"Unknown audio provider {name!r}. Known providers: tencent."
    )


def reset_audio_providers() -> None:
    """Drop cached provider instances. Intended for tests."""
    _instances.clear()


__all__ = [
    "AudioCapabilities",
    "AudioProvider",
    "AudioProviderError",
    "TencentAudioProvider",
    "get_audio_provider",
    "reset_audio_providers",
    "DEFAULT_PROVIDER",
]
