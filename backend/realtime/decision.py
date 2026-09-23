"""Deterministic realtime DTMF and human-boundary decisions.

The deterministic rules own every realtime action. When shadow judgment is
enabled (see docs/adr/0040), an LLM classifier may additionally veto an action:
it can block a DTMF injection, cancel a rule-driven hangup, or turn an automated
notice into a one-time listening extension. The model can never pick a key, send
DTMF, or advance navigation on its own.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from realtime.corrections import apply_asr_corrections
from realtime.shadow import (
    AUTOMATED_NOTICE,
    CANCEL_HANGUP_MIN_CONFIDENCE,
    HUMAN_OR_UNKNOWN,
    IVR_MENU,
    VETO_CONTINUE_MIN_CONFIDENCE,
)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

HUMAN_BOUNDARY_PATTERNS = (
    "转人工",
    "人工坐席",
    "人工客服",
    "为您转接",
    "正在转接",
    "转接人工",
    "接通人工",
    "请稍等",
    "排队",
    "representative",
    "please hold",
    "transfer you to",
    "connect you to an agent",
    "live agent",
)

AFTER_HOURS_COMPLETE_PATTERN = re.compile(
    r"作为评估和培训客服人员.{0,30}?改进客服中心技术质量.{0,30}?请稍等",
    re.DOTALL,
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

# Realtime ASR may stream a long announcement as one cumulative sentence or as
# several consecutive sentence fragments. Keep a bounded rolling window of the
# most recent fragments so context-dependent rules still see their full
# sentence without growing for the whole call.
_MAX_CONTEXT_SEGMENTS = 80

DEFAULT_BOUNDARY_CANCEL_WINDOW_MS = 1500
DEFAULT_AUTOMATED_NOTICE_EXTENSION_MS = 10000


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


def matched_human_keywords(text: str) -> set[str]:
    """Return the human-boundary keywords present in ``text``."""
    cleaned = AFTER_HOURS_COMPLETE_PATTERN.sub("", text)
    lowered = cleaned.lower()
    return {pattern for pattern in HUMAN_BOUNDARY_PATTERNS if pattern in lowered}


def has_human_boundary(text: str) -> bool:
    return bool(matched_human_keywords(text))


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
        allow_target_key_fallback: bool = False,
        shadow_judge: Any = None,
        boundary_cancel_window_ms: int = DEFAULT_BOUNDARY_CANCEL_WINDOW_MS,
        automated_notice_extension_ms: int = DEFAULT_AUTOMATED_NOTICE_EXTENSION_MS,
    ) -> None:
        self.channel_uuid = channel_uuid
        self.exploration_call_id = exploration_call_id
        self.target_key = target_key
        self.on_event = on_event
        self.silence_seconds = max(0.0, silence_ms / 1000)
        self.menu_completion_seconds = max(0.0, menu_completion_ms / 1000)
        self.no_speech_timeout_seconds = max(0.0, no_speech_timeout_ms / 1000)
        self.allow_target_key_fallback = allow_target_key_fallback
        self.shadow_judge = shadow_judge
        self.boundary_cancel_window_seconds = max(
            0.0, boundary_cancel_window_ms / 1000
        )
        self.automated_notice_extension_seconds = max(
            0.0, automated_notice_extension_ms / 1000
        )
        self._revision = 0
        self._final_text = ""
        self._context_segments: list[tuple[str, float]] = []
        self._last_segment_partial = False
        self._seen_keys: set[str] = set()
        self._fallback_seen = False
        self._stopped = False
        self._settle_task: asyncio.Task | None = None
        self._no_speech_task: asyncio.Task | None = None
        self._shadow_task: asyncio.Task | None = None
        self._shadow_task_revision = -1
        self._pending_boundary_task: asyncio.Task | None = None
        self._pending_boundary = False
        self._cancelled_keywords: set[str] = set()
        self._extension_used = False

    @property
    def shadow_enabled(self) -> bool:
        return bool(getattr(self.shadow_judge, "enabled", False))

    async def start(self) -> None:
        if self.no_speech_timeout_seconds:
            self._no_speech_task = asyncio.create_task(self._no_speech_timeout())

    async def feed(self, text: str, *, is_final: bool) -> None:
        original = text.strip()
        text = apply_asr_corrections(original)
        if text != original:
            logger.info("ASR correction: %r -> %r", original, text)
        if not text or self._stopped:
            return

        if self._no_speech_task and not self._no_speech_task.done():
            self._no_speech_task.cancel()
            self._no_speech_task = None

        # Realtime ASR delivers a single announcement as several callbacks.
        # Merge partial revisions of the same sentence and keep earlier
        # fragments so context-dependent exemptions such as the after-hours
        # hold message are matched against the whole sentence instead of one
        # fragment at a time.
        self._remember_segment(text, is_final=is_final)
        context = self._context_text()

        if has_human_boundary(context):
            await self._handle_human_boundary(text=text, context=context)
            if self._stopped:
                return

        if is_final:
            self._final_text = f"{self._final_text} {text}".strip()
            logger.info(
                "ASR final: %r keys=%s target=%r",
                text,
                sorted(extract_dtmf_keys(text)),
                self.target_key,
            )

        keys = extract_dtmf_keys(text)
        fallback_ready = self._fallback_ready(text)
        if is_final or keys or fallback_ready:
            self._seen_keys.update(keys)
            self._revision += 1
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()
            self._start_shadow_early(trigger="target_key")
            self._settle_task = asyncio.create_task(
                self._settle_after_silence(self._revision)
            )

    # -- human boundary ------------------------------------------------------

    async def _handle_human_boundary(self, *, text: str, context: str) -> None:
        if self._pending_boundary:
            return
        if not self.shadow_enabled:
            await self._stop_with(
                "human_boundary",
                text=text,
                context=context,
                reason="strong human-service keyword",
            )
            return

        matched = matched_human_keywords(context)
        # A previously cancelled after-hours notice must not re-trigger unless a
        # genuinely new human keyword appears in the transcript.
        if matched and matched <= self._cancelled_keywords:
            return

        self._pending_boundary = True
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=self.boundary_cancel_window_seconds
        )
        await self._emit(
            "boundary_pending",
            text=text,
            context=context,
            reason="strong human-service keyword",
            deadline_at=deadline.isoformat(),
        )
        self._pending_boundary_task = asyncio.create_task(
            self._resolve_pending_boundary(text=text, context=context)
        )

    async def _resolve_pending_boundary(self, *, text: str, context: str) -> None:
        verdict = await self._judge(trigger="boundary_pending")
        if self._stopped:
            return
        if (
            verdict is not None
            and verdict.usable
            and verdict.classification in (IVR_MENU, AUTOMATED_NOTICE)
            and verdict.confidence >= CANCEL_HANGUP_MIN_CONFIDENCE
        ):
            self._pending_boundary = False
            self._cancelled_keywords |= matched_human_keywords(context)
            await self._emit(
                "boundary_cancelled",
                classification=verdict.classification,
                confidence=verdict.confidence,
                reason=verdict.reason,
                context=context,
            )
            return
        self._pending_boundary = False
        await self._stop_with(
            "human_boundary",
            text=text,
            context=context,
            reason="strong human-service keyword",
        )

    # -- target key settle ---------------------------------------------------

    def _remember_segment(self, text: str, *, is_final: bool) -> None:
        segments = self._context_segments
        now = time.monotonic()
        if is_final:
            if self._last_segment_partial and segments:
                segments[-1] = (text, now)
            else:
                segments.append((text, now))
            self._last_segment_partial = False
        else:
            if (
                self._last_segment_partial
                and segments
                and (text.startswith(segments[-1][0]) or segments[-1][0].startswith(text))
            ):
                segments[-1] = (text, now)
            else:
                segments.append((text, now))
            self._last_segment_partial = True
        if len(segments) > _MAX_CONTEXT_SEGMENTS:
            del segments[: len(segments) - _MAX_CONTEXT_SEGMENTS]

    def _context_text(self) -> str:
        return " ".join(text for text, _ in self._context_segments).strip()

    def _context_for_shadow(self) -> list[tuple[str, float]]:
        return list(self._context_segments)

    async def close(self) -> None:
        self._stopped = True
        for task in (
            self._settle_task,
            self._no_speech_task,
            self._shadow_task,
            self._pending_boundary_task,
        ):
            if task and not task.done():
                task.cancel()

    async def _settle_after_silence(
        self, revision: int, *, extra_delay: float = 0.0
    ) -> None:
        try:
            delay = (
                self.silence_seconds
                if self.target_key
                and (self.target_key in self._seen_keys or self._fallback_seen)
                else self.menu_completion_seconds
            )
            await asyncio.sleep(delay + extra_delay)
        except asyncio.CancelledError:
            return
        if self._stopped or revision != self._revision:
            return

        if self.target_key and (
            self.target_key in self._seen_keys or self._fallback_seen
        ):
            await self._resolve_target_key()
            return

        reason = (
            "target key not found in explicit DTMF menu"
            if self._seen_keys
            else "no explicit DTMF menu"
        )
        await self._resolve_unknown_boundary(reason=reason)

    async def _resolve_target_key(self) -> None:
        assert self.target_key is not None
        verdict = await self._consume_shadow()
        if self._stopped:
            return
        if (
            verdict is not None
            and verdict.usable
            and verdict.confidence >= VETO_CONTINUE_MIN_CONFIDENCE
        ):
            if verdict.classification == HUMAN_OR_UNKNOWN:
                await self._stop_with(
                    "human_boundary",
                    text=self._final_text,
                    key=self.target_key,
                    reason="shadow judge vetoed DTMF on human/unknown",
                )
                return
            if (
                verdict.classification == AUTOMATED_NOTICE
                and not self._extension_used
            ):
                self._extension_used = True
                # Require fresh key evidence during the extension so the same
                # notice cannot immediately re-trigger the target key.
                self._seen_keys.clear()
                self._fallback_seen = False
                self._revision += 1
                logger.info(
                    "Shadow judge deferred DTMF: automated notice, extending once"
                )
                self._settle_task = asyncio.create_task(
                    self._settle_after_silence(
                        self._revision,
                        extra_delay=self.automated_notice_extension_seconds,
                    )
                )
                return
        await self._stop_with(
            "dtmf_ready",
            key=self.target_key,
            text=self._final_text or "English support fallback",
        )

    async def _resolve_unknown_boundary(self, *, reason: str) -> None:
        verdict = await self._consume_shadow()
        if self._stopped:
            return
        if (
            verdict is not None
            and verdict.usable
            and verdict.classification == IVR_MENU
            and verdict.confidence >= CANCEL_HANGUP_MIN_CONFIDENCE
        ):
            logger.info("Shadow judge cancelled unknown boundary: ivr_menu")
            self._revision += 1
            self._settle_task = asyncio.create_task(
                self._settle_after_silence(self._revision)
            )
            return
        await self._stop_with(
            "unknown_boundary",
            text=self._final_text,
            keys=sorted(self._seen_keys),
            reason=reason,
        )

    # -- shadow plumbing -----------------------------------------------------

    def _start_shadow_early(self, *, trigger: str) -> None:
        if not self.shadow_enabled:
            return
        if self._shadow_task and not self._shadow_task.done():
            return
        self._shadow_task_revision = self._revision
        self._shadow_task = asyncio.create_task(self._judge(trigger=trigger))

    async def _consume_shadow(self):
        if not self.shadow_enabled:
            return None
        task = self._shadow_task
        if task is None:
            return await self._judge(trigger="action_point")
        try:
            return await task
        except asyncio.CancelledError:
            return None
        except Exception:  # noqa: BLE001 - shadow never breaks the call
            return None

    async def _judge(self, *, trigger: str):
        judge = self.shadow_judge
        if judge is None or not getattr(judge, "enabled", False):
            return None
        try:
            verdict = await judge.judge(
                channel_uuid=self.channel_uuid,
                exploration_call_id=self.exploration_call_id,
                segments=self._context_for_shadow(),
                trigger=trigger,
            )
        except Exception as exc:  # noqa: BLE001 - shadow must not raise upward
            logger.info("Shadow judge error: %s", exc)
            return None
        if verdict is not None:
            await self._emit(
                "shadow_verdict",
                trigger=trigger,
                classification=getattr(verdict, "classification", ""),
                confidence=getattr(verdict, "confidence", 0.0),
                status=getattr(verdict, "status", ""),
                latency_ms=getattr(verdict, "latency_ms", 0),
                reason=getattr(verdict, "reason", ""),
            )
        return verdict

    def _fallback_ready(self, text: str) -> bool:
        if not self.allow_target_key_fallback or self.target_key != "2":
            self._fallback_seen = False
            return False
        lowered = text.lower()
        self._fallback_seen = "english" in lowered
        if self._fallback_seen:
            logger.info("Target key 2 accepted from English support phrase fallback")
        return self._fallback_seen

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

    async def _emit(self, event_type: str, **payload: Any) -> None:
        await self.on_event(
            {
                "channel_uuid": self.channel_uuid,
                "exploration_call_id": self.exploration_call_id,
                "event_type": event_type,
                **payload,
            }
        )

    async def _stop_with(self, event_type: str, **payload: Any) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self._emit(event_type, **payload)
