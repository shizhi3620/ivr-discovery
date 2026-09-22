"""Bland AI adapter for the telephony Provider boundary.

Bland owns the whole call leg — it speaks, listens and returns its own ASR
transcript — so it advertises all capabilities. It stays available as a
non-default provider for the existing demo and tests, but it is not used for the
China mainland scenario (see docs/adr/0004).
"""

from __future__ import annotations

import logging

import bland_client
from providers.base import (
    CallResult,
    ProviderCapabilities,
    TranscriptCallback,
    STATUS_UNKNOWN,
)

logger = logging.getLogger(__name__)


class BlandProvider:
    name = "bland"
    capabilities = ProviderCapabilities(transcript=True, speech=True, dtmf=True)

    async def place_call(
        self,
        phone_number: str,
        *,
        task: str | None = None,
        dtmf_sequence: str | None = None,
        voice_option: str | None = None,
        max_duration: int = 60,
    ) -> str:
        # Bland expresses voice navigation as a spoken task; a DTMF path as a
        # keypress task. Resolve which one applies before delegating.
        if task is None and voice_option:
            task = bland_client.voice_branch_task(voice_option)
            dtmf_sequence = None

        result = await bland_client.place_call(
            phone_number=phone_number,
            task=task,
            dtmf_sequence=dtmf_sequence,
            max_duration=max_duration,
        )
        call_id = result.get("call_id")
        if not call_id:
            raise RuntimeError(f"Bland returned no call_id: {result}")
        return call_id

    async def wait_for_call(
        self,
        call_id: str,
        on_transcript: TranscriptCallback | None = None,
    ) -> CallResult:
        data = await bland_client.wait_for_call(call_id, on_transcript=on_transcript)
        return self._to_result(call_id, data)

    async def get_call(self, call_id: str) -> CallResult:
        data = await bland_client.get_call(call_id)
        return self._to_result(call_id, data)

    async def stop_call(self, call_id: str) -> None:
        await bland_client.stop_call(call_id)

    def _to_result(self, call_id: str, data: dict) -> CallResult:
        return CallResult(
            call_id=call_id,
            status=data.get("status", STATUS_UNKNOWN),
            transcript=data.get("concatenated_transcript", "") or "",
            cost=data.get("price", 0.0) or 0.0,
            capabilities=self.capabilities,
        )
