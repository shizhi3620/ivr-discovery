"""Deterministic realtime DTMF and human-boundary decisions."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from realtime.corrections import apply_asr_corrections

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

HUMAN_BOUNDARY_PATTERNS = (
    "转人工",
    "人工坐席",
    "人工客服",
    "为您转接",
    "正在转接",
    "转接人工",
    "接通人工",
    "representative",
    "transfer you to",
    "connect you to an agent",
    "live agent",
)

_DIGIT_WORDS = {
    "零": "0",
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}

_KEY_WORDS = {
    "star": "*",
    "asterisk": "*",
    "hash": "#",
    "pound": "#",
}

_ENGLISH_DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

_DTMF_PATTERNS = (
    re.compile(r"(?:按|请按)\s*([0-9*#])"),
    re.compile(r"press\s+([0-9*#])", re.IGNORECASE),
    re.compile(r"key\s+([0-9*#])", re.IGNORECASE),
)


def extract_dtmf_keys(text: str) -> set[str]:
    """Extract explicit DTMF keys from Chinese or English IVR text."""
    keys: set[str] = set()
    for pattern in _DTMF_PATTERNS:
        keys.update(pattern.findall(text))

    for word, key in _DIGIT_WORDS.items():
        if re.search(rf"(?:按|请按)\s*{word}", text):
            keys.add(key)

    lowered = text.lower()
    for word, key in _ENGLISH_DIGIT_WORDS.items():
        if re.search(rf"(?:press|key)\s+{word}\b", lowered):
            keys.add(key)
    for word, key in _KEY_WORDS.items():
        if re.search(rf"(?:press|key)\s+{word}", lowered):
            keys.add(key)
    return keys


def has_human_boundary(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in HUMAN_BOUNDARY_PATTERNS)


class RealtimeDecisionEngine:
    """Consume ASR text and emit deterministic control events."""

    def __init__(
        self,
        *,
        channel_uuid: str,
        exploration_call_id: str,
        target_key: str | None,
        on_event: EventCallback,
        silence_ms: int = 800,
        menu_completion_ms: int = 8000,
        no_speech_timeout_ms: int = 5000,
    ) -> None:
        self.channel_uuid = channel_uuid
        self.exploration_call_id = exploration_call_id
        self.target_key = target_key
        self.on_event = on_event
        self.silence_seconds = max(0.0, silence_ms / 1000)
        self.menu_completion_seconds = max(0.0, menu_completion_ms / 1000)
        self.no_speech_timeout_seconds = max(0.0, no_speech_timeout_ms / 1000)
        self._revision = 0
        self._final_text = ""
        self._seen_keys: set[str] = set()
        self._stopped = False
        self._settle_task: asyncio.Task | None = None
        self._no_speech_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.no_speech_timeout_seconds:
            self._no_speech_task = asyncio.create_task(self._no_speech_timeout())

    async def feed(self, text: str, *, is_final: bool) -> None:
        text = apply_asr_corrections(text.strip())
        if not text or self._stopped:
            return

        if self._no_speech_task and not self._no_speech_task.done():
            self._no_speech_task.cancel()
            self._no_speech_task = None

        if has_human_boundary(text):
            await self._stop_with(
                "human_boundary",
                text=text,
                reason="strong human-service keyword",
            )
            return

        if is_final:
            self._final_text = f"{self._final_text} {text}".strip()

        if is_final:
            self._seen_keys.update(extract_dtmf_keys(text))
            self._revision += 1
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()
            self._settle_task = asyncio.create_task(
                self._settle_after_silence(self._revision)
            )

    async def close(self) -> None:
        self._stopped = True
        for task in (self._settle_task, self._no_speech_task):
            if task and not task.done():
                task.cancel()

    async def _settle_after_silence(self, revision: int) -> None:
        try:
            delay = (
                self.silence_seconds
                if self.target_key and self.target_key in self._seen_keys
                else self.menu_completion_seconds
            )
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._stopped or revision != self._revision:
            return

        if self.target_key and self.target_key in self._seen_keys:
            await self._stop_with(
                "dtmf_ready",
                key=self.target_key,
                text=self._final_text,
            )
            return

        reason = (
            "target key not found in explicit DTMF menu"
            if self._seen_keys
            else "no explicit DTMF menu"
        )
        await self._stop_with(
            "unknown_boundary",
            text=self._final_text,
            keys=sorted(self._seen_keys),
            reason=reason,
        )

    async def _no_speech_timeout(self) -> None:
        try:
            await asyncio.sleep(self.no_speech_timeout_seconds)
        except asyncio.CancelledError:
            return
        if not self._stopped:
            await self._stop_with(
                "technical_unknown",
                text="",
                reason="no ASR text before timeout",
            )

    async def _stop_with(self, event_type: str, **payload: Any) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self.on_event(
            {
                "channel_uuid": self.channel_uuid,
                "exploration_call_id": self.exploration_call_id,
                "event_type": event_type,
                **payload,
            }
        )
