"""Replay a WAV file through the realtime relay without placing a phone call."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
import wave
from pathlib import Path

import websockets


async def _print_events(event_url: str, ready: asyncio.Event) -> None:
    async with websockets.connect(event_url, max_size=None) as websocket:
        ready.set()
        try:
            async for raw in websocket:
                event = json.loads(raw)
                print(json.dumps(event, ensure_ascii=False))
                if event.get("event_type") in (
                    "dtmf_ready",
                    "human_boundary",
                    "asr_error",
                    "stream_stopped",
                ):
                    break
        except websockets.ConnectionClosed:
            pass


async def replay(
    *,
    wav_path: Path,
    relay_base: str,
    target_key: str | None,
    remote_channel: int,
    speed: float,
) -> None:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        if sample_width != 2:
            raise ValueError("Only 16-bit PCM WAV files are supported")
        if remote_channel < 0 or remote_channel >= channels:
            raise ValueError(
                f"remote channel {remote_channel} is outside 0..{channels - 1}"
            )

        event_ready = asyncio.Event()
        event_task = asyncio.create_task(
            _print_events(f"{relay_base}/events", event_ready)
        )
        await asyncio.wait_for(event_ready.wait(), timeout=5)

        async with websockets.connect(
            f"{relay_base}/audio",
            max_size=None,
        ) as websocket:
            channel_uuid = f"replay-{uuid.uuid4()}"
            exploration_call_id = str(uuid.uuid4())
            await websocket.send(
                json.dumps(
                    {
                        "channel_uuid": channel_uuid,
                        "exploration_call_id": exploration_call_id,
                        "target_key": target_key,
                        "encoding": "s16le",
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "remote_channel": remote_channel,
                        "silence_ms": 800,
                        # Offline replay may start slower than a live call.
                        "no_speech_timeout_ms": 30000,
                    }
                )
            )

            frames_per_chunk = max(1, int(sample_rate * 0.1))
            delay = 0.1 / speed
            while True:
                frames = wav.readframes(frames_per_chunk)
                if not frames:
                    break
                await websocket.send(frames)
                await asyncio.sleep(delay)

        await asyncio.wait_for(event_task, timeout=15)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay WAV through realtime relay")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--relay", default="ws://127.0.0.1:18031")
    parser.add_argument("--target-key", default="1")
    parser.add_argument("--remote-channel", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    target_key = None if args.target_key.lower() in ("", "none") else args.target_key
    asyncio.run(
        replay(
            wav_path=args.wav,
            relay_base=args.relay.rstrip("/"),
            target_key=target_key,
            remote_channel=args.remote_channel,
            speed=args.speed,
        )
    )


if __name__ == "__main__":
    main()
