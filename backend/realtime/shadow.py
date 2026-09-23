"""Shadow LLM judgment for realtime IVR decisions.

The shadow judge runs *alongside* the deterministic decision engine. It never
sends DTMF, never picks a key and never advances navigation. It only classifies
the recent realtime ASR context into one of three buckets so the deterministic
engine can veto its own action:

- ``ivr_menu``: a navigable IVR menu is present.
- ``automated_notice``: an automated announcement (not a menu, not human).
- ``human_or_unknown``: a human agent, queue or unknown service boundary.

See docs/adr/0040-model-shadow-judgment-and-safety-veto.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ai.deepseek_provider import DeepSeekProvider

logger = logging.getLogger(__name__)

IVR_MENU = "ivr_menu"
AUTOMATED_NOTICE = "automated_notice"
HUMAN_OR_UNKNOWN = "human_or_unknown"
UNAVAILABLE = "unavailable"

VALID_CLASSIFICATIONS = {IVR_MENU, AUTOMATED_NOTICE, HUMAN_OR_UNKNOWN}

# Non-asymmetric: vetoing a continue (requesting a hangup) is cheap, so it uses a
# lower bar than cancelling a rule-driven hangup (which may resume on a human).
VETO_CONTINUE_MIN_CONFIDENCE = 0.6
CANCEL_HANGUP_MIN_CONFIDENCE = 0.9

DEFAULT_TIMEOUT_SECONDS = 1.5
DEFAULT_CONTEXT_SECONDS = 60.0
DEFAULT_CONTEXT_CHARS = 2000

_PROMPT_TEMPLATE = """You are a classifier for an automated IVR discovery system.
Decide which single category best describes the latest state of this phone
conversation, based only on the transcript excerpts below.

Categories:
- "ivr_menu": an automated IVR menu that offers DTMF choices is present.
- "automated_notice": an automated announcement or notice is playing that is
  NOT a navigable menu (for example an after-hours notice or a hold message).
- "human_or_unknown": a human agent or queue has been reached, or the audio is
  an unknown live service.

Return ONLY compact JSON with this exact shape:
{{"classification": "<one of ivr_menu|automated_notice|human_or_unknown>",
  "confidence": <number between 0 and 1>,
  "reason": "<short explanation>"}}

Transcript excerpts (most recent last, each prefixed with its ASR type):
{context}
"""


@dataclass(frozen=True)
class ShadowVerdict:
    """One shadow judgment result."""

    classification: str
    confidence: float
    reason: str
    status: str
    latency_ms: int
    # Exact rendered context sent to the model. Carried on the verdict so the
    # ``shadow_verdict`` event stream is self-contained and each decision point
    # can be human-labelled offline without joining a second audit file.
    context: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "ok" and self.classification in VALID_CLASSIFICATIONS


@dataclass
class ShadowJudge:
    """Async three-way classifier backed by DeepSeek."""

    enabled: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    context_seconds: float = DEFAULT_CONTEXT_SECONDS
    context_chars: int = DEFAULT_CONTEXT_CHARS
    audit_dir: str = "/tmp/ivr-discovery-shadow"
    provider: Any = None  # AIProvider-like; defaults to DeepSeekProvider.
    _provider: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "ShadowJudge":
        enabled = os.getenv("SHADOW_JUDGMENT_ENABLED", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        return cls(
            enabled=enabled,
            timeout_seconds=float(
                os.getenv("SHADOW_JUDGMENT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            ),
            audit_dir=os.getenv(
                "SHADOW_JUDGMENT_AUDIT_DIR", "/tmp/ivr-discovery-shadow"
            ),
        )

    def _get_provider(self) -> Any:
        if self._provider is not None:
            return self._provider
        if self.provider is not None:
            return self.provider
        return DeepSeekProvider()

    async def judge(
        self,
        *,
        channel_uuid: str,
        exploration_call_id: str,
        segments: list[tuple[str, float]],
        trigger: str,
    ) -> ShadowVerdict | None:
        """Classify the rolling context; return ``None`` when disabled."""
        if not self.enabled:
            return None

        context = self._render_context(segments)
        started = time.monotonic()
        verdict = replace(await self._classify(context, started), context=context)
        self._write_audit(
            channel_uuid=channel_uuid,
            exploration_call_id=exploration_call_id,
            trigger=trigger,
            context=context,
            verdict=verdict,
        )
        return verdict

    def _render_context(self, segments: list[tuple[str, float]]) -> str:
        if not segments:
            return ""
        newest = segments[-1][1]
        recent = [
            text
            for text, ts in segments
            if newest - ts <= self.context_seconds and text
        ]
        joined = "\n".join(recent)
        if len(joined) > self.context_chars:
            joined = joined[-self.context_chars :]
        return joined

    async def _classify(self, context: str, started: float) -> ShadowVerdict:
        provider = self._get_provider()
        prompt = _PROMPT_TEMPLATE.format(context=context or "(no transcript yet)")
        try:
            raw = await asyncio.wait_for(
                provider.complete(prompt, max_tokens=256, json_mode=True),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._failure("timeout", started)
        except Exception as exc:  # noqa: BLE001 - shadow must never raise upward
            status = "rate_limit" if "429" in str(exc) else "http_error"
            logger.info("Shadow judge call failed: %s", exc)
            return self._failure(status, started)

        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return self._failure("invalid_json", started, latency_ms=latency_ms)
        if not isinstance(payload, dict):
            return self._failure("invalid_json", started, latency_ms=latency_ms)

        classification = str(payload.get("classification") or "").strip()
        if classification not in VALID_CLASSIFICATIONS:
            return self._failure(
                "invalid_classification", started, latency_ms=latency_ms
            )
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError):
            return self._failure(
                "invalid_json", started, latency_ms=latency_ms
            )
        confidence = max(0.0, min(1.0, confidence))
        reason = str(payload.get("reason") or "")[:200]

        status = "ok" if confidence > 0 else "low_confidence"
        return ShadowVerdict(
            classification=classification,
            confidence=confidence,
            reason=reason,
            status=status,
            latency_ms=latency_ms,
        )

    def _failure(
        self,
        status: str,
        started: float,
        *,
        latency_ms: int | None = None,
    ) -> ShadowVerdict:
        return ShadowVerdict(
            classification=UNAVAILABLE,
            confidence=0.0,
            reason="",
            status=status,
            latency_ms=latency_ms
            if latency_ms is not None
            else int((time.monotonic() - started) * 1000),
        )

    def _write_audit(
        self,
        *,
        channel_uuid: str,
        exploration_call_id: str,
        trigger: str,
        context: str,
        verdict: ShadowVerdict | None,
    ) -> None:
        if verdict is None:
            return
        record = {
            "channel_uuid": channel_uuid,
            "exploration_call_id": exploration_call_id,
            "trigger": trigger,
            "classification": verdict.classification,
            "confidence": verdict.confidence,
            "reason": verdict.reason,
            "status": verdict.status,
            "latency_ms": verdict.latency_ms,
            "context": context,
            "timestamp": time.time(),
        }
        try:
            path = Path(self.audit_dir)
            path.mkdir(parents=True, exist_ok=True)
            with (path / "shadow-judgments.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write shadow audit record: %s", exc)


__all__ = [
    "ShadowJudge",
    "ShadowVerdict",
    "IVR_MENU",
    "AUTOMATED_NOTICE",
    "HUMAN_OR_UNKNOWN",
    "UNAVAILABLE",
    "VALID_CLASSIFICATIONS",
    "VETO_CONTINUE_MIN_CONFIDENCE",
    "CANCEL_HANGUP_MIN_CONFIDENCE",
]
