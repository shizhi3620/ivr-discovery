"""Controlled real-call probe for one known realtime DTMF prefix."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

import websockets
from dotenv import load_dotenv

from providers.android_sim_provider import AndroidSimGatewayProvider

TERMINAL_EVENTS = {
    "dtmf_ready",
    "human_boundary",
    "unknown_boundary",
    "technical_unknown",
    "asr_error",
}


class RealCallProbe:
    """Attach the realtime relay to a provider-owned outbound call.

    The probe sends at most one target DTMF key. It stops immediately on a human,
    unknown or ASR failure boundary; only an explicit deterministic
    ``dtmf_ready`` event is allowed to inject a key.
    """

    def __init__(
        self,
        *,
        provider: AndroidSimGatewayProvider,
        relay_base: str,
        target_key: str,
        encoding: str,
        mix_type: str,
        gain: float,
        silence_ms: int,
        menu_completion_ms: int,
        no_speech_timeout_ms: int,
    ) -> None:
        self.provider = provider
        self.relay_base = relay_base.rstrip("/")
        self.target_key = target_key
        self.encoding = encoding
        self.mix_type = mix_type
        self.gain = gain
        self.silence_ms = silence_ms
        self.menu_completion_ms = menu_completion_ms
        self.no_speech_timeout_ms = no_speech_timeout_ms

    async def run(
        self,
        phone_number: str,
        *,
        max_duration: int,
    ) -> dict[str, Any]:
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        event_task = asyncio.create_task(self._consume_events(events))
        await asyncio.sleep(0.2)

        call_id: str | None = None
        stream_started = False
        stream_stopped = False
        try:
            call_id = await self.provider.place_call(
                phone_number,
                max_duration=max_duration,
            )
            print("call_id", call_id, flush=True)
            await self._start_stream(call_id)
            stream_started = True
            terminal = await asyncio.wait_for(
                self._next_terminal_event(events, call_id),
                timeout=max_duration + 15,
            )
            print(
                "terminal_event",
                json.dumps(terminal, ensure_ascii=False),
                flush=True,
            )

            if terminal["event_type"] != "dtmf_ready":
                await self.provider.stop_call(call_id)
                return {"call_id": call_id, "terminal": terminal}

            dtmf_result = await asyncio.to_thread(
                self.provider._run_api,
                f"uuid_send_dtmf {call_id} {terminal['key']}",
            )
            print("dtmf_inject", terminal["key"], dtmf_result, flush=True)

            # The decision engine stops after dtmf_ready. The audio stream is no
            # longer needed; continue recording the resulting next-level menu.
            await self._stop_stream(call_id)
            stream_stopped = True

            result = await asyncio.wait_for(
                self.provider.wait_for_call(call_id),
                timeout=max_duration + 60,
            )
            print(
                "final_status",
                result.status,
                "transcript_len",
                len(result.transcript),
                flush=True,
            )
            print("transcript_begin", flush=True)
            print(result.transcript, flush=True)
            print("transcript_end", flush=True)
            return {
                "call_id": call_id,
                "terminal": terminal,
                "status": result.status,
                "transcript": result.transcript,
            }
        except Exception:
            if call_id is not None:
                await self.provider.stop_call(call_id)
            raise
        finally:
            if call_id is not None and stream_started and not stream_stopped:
                try:
                    await self._stop_stream(call_id)
                except Exception:
                    pass
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)

    async def _consume_events(
        self,
        events: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with websockets.connect(
            f"{self.relay_base}/events",
            max_size=None,
        ) as websocket:
            async for raw in websocket:
                event = json.loads(raw)
                print(json.dumps(event, ensure_ascii=False), flush=True)
                await events.put(event)

    async def _start_stream(self, call_id: str) -> None:
        channels = 1 if self.mix_type in ("mono", "mixed") else 2
        metadata = json.dumps(
            {
                "channel_uuid": call_id,
                "exploration_call_id": str(uuid.uuid4()),
                "target_key": self.target_key,
                "encoding": self.encoding,
                "sample_rate": 8000,
                "channels": channels,
                "remote_channel": 0,
                "silence_ms": self.silence_ms,
                "menu_completion_ms": self.menu_completion_ms,
                "no_speech_timeout_ms": self.no_speech_timeout_ms,
                "gain": self.gain,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        result = await asyncio.to_thread(
            self.provider._run_api,
            f"uuid_audio_stream {call_id} start "
            f"{self.relay_base}/audio {self.mix_type} 8000 {metadata}",
        )
        print("audio_stream_start", result, flush=True)

    async def _stop_stream(self, call_id: str) -> None:
        try:
            result = await asyncio.to_thread(
                self.provider._run_api,
                f"uuid_audio_stream {call_id} stop",
            )
        except Exception as exc:
            print("audio_stream_stop_error", str(exc), flush=True)
            return
        print("audio_stream_stop", result, flush=True)

    async def _next_terminal_event(
        self,
        events: asyncio.Queue[dict[str, Any]],
        call_id: str,
    ) -> dict[str, Any]:
        while True:
            event = await events.get()
            if event.get("channel_uuid") != call_id:
                continue
            if event.get("event_type") in TERMINAL_EVENTS:
                return event


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one controlled real-call realtime DTMF probe",
    )
    parser.add_argument("phone_number")
    parser.add_argument("--target-key", required=True)
    parser.add_argument("--relay", default="ws://127.0.0.1:18031")
    parser.add_argument("--max-duration", type=int, default=30)
    parser.add_argument("--encoding", default="s16le")
    parser.add_argument(
        "--mix-type",
        choices=("mono", "mixed", "stereo"),
        default="mono",
    )
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--silence-ms", type=int, default=800)
    parser.add_argument("--menu-completion-ms", type=int, default=8000)
    parser.add_argument("--no-speech-timeout-ms", type=int, default=30000)
    parser.add_argument(
        "--confirm-real-call",
        action="store_true",
        help="required acknowledgement before dialing a real phone number",
    )
    args = parser.parse_args()

    if not args.confirm_real_call:
        parser.error("--confirm-real-call is required")
    if args.max_duration < 1:
        parser.error("--max-duration must be positive")

    load_dotenv(".env")
    probe = RealCallProbe(
        provider=AndroidSimGatewayProvider(),
        relay_base=args.relay,
        target_key=args.target_key,
        encoding=args.encoding,
        mix_type=args.mix_type,
        gain=args.gain,
        silence_ms=args.silence_ms,
        menu_completion_ms=args.menu_completion_ms,
        no_speech_timeout_ms=args.no_speech_timeout_ms,
    )
    asyncio.run(
        probe.run(
            args.phone_number,
            max_duration=args.max_duration,
        )
    )


if __name__ == "__main__":
    main()
