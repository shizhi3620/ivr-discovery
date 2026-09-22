"""Telephony Provider selection.

`get_provider()` is the single seam the discovery engine uses. It reads the
`TELEPHONY_PROVIDER` env var and returns a provider instance, defaulting to the
China mainland Android SIM gateway.
"""

from __future__ import annotations

import os

from providers.base import (
    CallResult,
    ProviderCapabilities,
    ProviderCapabilityError,
    TelephonyProvider,
    TERMINAL_STATUSES,
)
from providers.bland_provider import BlandProvider

DEFAULT_PROVIDER = "android_sim"

_instances: dict[str, TelephonyProvider] = {}


def get_provider(name: str | None = None, *, force_new: bool = False) -> TelephonyProvider:
    """Return the configured telephony provider.

    Selection order: explicit `name` argument, then `TELEPHONY_PROVIDER`, then the
    default (Android SIM gateway for the China mainland scenario).
    """
    resolved = (name or os.getenv("TELEPHONY_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

    if not force_new and resolved in _instances:
        return _instances[resolved]

    provider = _build(resolved)
    _instances[resolved] = provider
    return provider


def _build(name: str) -> TelephonyProvider:
    if name in ("android_sim", "android_sim_gateway"):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        return AndroidSimGatewayProvider()
    if name == "bland":
        return BlandProvider()
    raise ValueError(
        f"Unknown telephony provider {name!r}. Known providers: android_sim, bland."
    )


def reset_providers() -> None:
    """Drop cached provider instances. Intended for tests."""
    _instances.clear()


__all__ = [
    "CallResult",
    "ProviderCapabilities",
    "ProviderCapabilityError",
    "TelephonyProvider",
    "TERMINAL_STATUSES",
    "BlandProvider",
    "get_provider",
    "reset_providers",
    "DEFAULT_PROVIDER",
]
