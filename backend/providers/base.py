"""Telephony Provider boundary.

The discovery engine must not know which vendor actually places a call. It asks a
Provider to place a call, wait for it, and hand back a normalized result.

Providers differ in what they can deliver. A cloud voice-AI provider (Bland)
returns an ASR transcript for free; a raw carrier gateway (Android SIM) only
delivers audio, so its transcript stays empty until the ASR stage is wired in.
The `capabilities` field makes that difference explicit instead of pretending all
providers are interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable

# Normalized call statuses. Providers map their vendor-specific statuses onto
# these so the discovery engine can reason about them uniformly.
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_BUSY = "busy"
STATUS_NO_ANSWER = "no-answer"
STATUS_CANCELED = "canceled"
STATUS_ERROR = "error"
STATUS_IN_PROGRESS = "in-progress"
STATUS_UNKNOWN = "unknown"

TERMINAL_STATUSES = frozenset(
    {
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_BUSY,
        STATUS_NO_ANSWER,
        STATUS_CANCELED,
        STATUS_ERROR,
    }
)

TranscriptCallback = Callable[[str], Optional[Awaitable[None]]]


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can actually deliver, so callers don't assume too much."""

    # Provider runs its own ASR and can return a text transcript.
    transcript: bool = False
    # Provider can play/generate speech on the outbound leg itself.
    speech: bool = False
    # Provider can forward DTMF keypresses to the called party.
    dtmf: bool = False


@dataclass
class CallResult:
    """Normalized result of a call, independent of the underlying vendor."""

    call_id: str
    status: str = STATUS_UNKNOWN
    transcript: str = ""
    cost: float = 0.0
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def as_legacy_dict(self) -> dict:
        """Shape used by the current discovery/frontend code paths."""
        return {
            "call_id": self.call_id,
            "status": self.status,
            "concatenated_transcript": self.transcript,
            "price": self.cost,
        }


class ProviderCapabilityError(RuntimeError):
    """Raised when a caller asks for something a provider cannot do."""


@runtime_checkable
class TelephonyProvider(Protocol):
    """Places and monitors a single outbound call.

    Providers own telephony execution only. ASR, text decision making and TTS are
    separate concerns that consume whatever a provider returns.
    """

    name: str
    capabilities: ProviderCapabilities

    async def place_call(
        self,
        phone_number: str,
        *,
        task: str | None = None,
        dtmf_sequence: str | None = None,
        voice_option: str | None = None,
        max_duration: int = 60,
    ) -> str:
        """Start a call and return a provider call id."""
        ...

    async def wait_for_call(
        self,
        call_id: str,
        on_transcript: TranscriptCallback | None = None,
    ) -> CallResult:
        """Block until the call reaches a terminal status (or times out)."""
        ...

    async def get_call(self, call_id: str) -> CallResult:
        """Fetch the current state of a call."""
        ...

    async def stop_call(self, call_id: str) -> None:
        """End an in-progress call. Must be idempotent."""
        ...
