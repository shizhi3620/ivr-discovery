"""Offline calibration for shadow judgment (ADR 0040).

Replays historical call recordings through the real realtime relay (Tencent
realtime ASR + deterministic decision engine with shadow judgment enabled in
report-only mode) and writes one JSONL record per decision point. Those records
are then human-labelled to compute the calibration gates:

- cancel-hangup direction: zero false positives
- request-hangup direction: at most one false positive
- overall three-way accuracy: >= 0.9

This script never places a real phone call and never spends call budget.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
import wave
from pathlib import Path

import websockets
from dotenv import load_dotenv


async def _collect(
    *,
    wav_path: Path,
    relay_base: str,
    remote_channel: int,
    speed: float,
    out_path: Path,
) -> None:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        if wav.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV files are supported")
        if remote_channel < 0 or remote_channel >= channels:
            raise ValueError("remote_channel outside recording channel range")

        records: list[dict] = []
        capture_done = asyncio.Event()

        async def listen() -> None:
            async with websockets.connect(
                f"{relay_base}/events", max_size=None
            ) as websocket:
                try:
                    async for raw in websocket:
                        event = json.loads(raw)
                        if event.get("event_type") in (
                            "boundary_pending",
                            "boundary_cancelled",
                            "human_boundary",
                            "unknown_boundary",
                            "shadow_verdict",
                        ):
                            event["wav"] = wav_path.name
                            records.append(event)
                        if event.get("event_type") in (
                            "boundary_cancelled",
                            "human_boundary",
                            "unknown_boundary",
                        ):
                            capture_done.set()
                            return
                except websockets.ConnectionClosed:
                    pass

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.3)

        async with websockets.connect(
            f"{relay_base}/audio", max_size=None
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "channel_uuid": f"calib-{uuid.uuid4()}",
                        "exploration_call_id": str(uuid.uuid4()),
                        "target_key": "1",
                        "encoding": "s16le",
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "remote_channel": remote_channel,
                        "silence_ms": 800,
                        "no_speech_timeout_ms": 30000,
                    }
                )
            )
            frames_per_chunk = max(1, int(sample_rate * 0.1))
            delay = 0.1 / speed
            while not capture_done.is_set():
                frames = wav.readframes(frames_per_chunk)
                if not frames:
                    break
                await websocket.send(frames)
                await asyncio.sleep(delay)

        try:
            await asyncio.wait_for(listener, timeout=20)
        except asyncio.TimeoutError:
            listener.cancel()

        with out_path.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{wav_path.name}: {len(records)} events")


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow judgment calibration")
    parser.add_argument("wavs", nargs="+", type=Path)
    parser.add_argument("--relay", default="ws://127.0.0.1:18031")
    parser.add_argument("--remote-channel", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--out", type=Path, default=Path("/tmp/ivr-discovery-shadow/calibration.jsonl")
    )
    args = parser.parse_args()

    load_dotenv(".env")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for wav in args.wavs:
        asyncio.run(
            _collect(
                wav_path=wav,
                relay_base=args.relay.rstrip("/"),
                remote_channel=args.remote_channel,
                speed=args.speed,
                out_path=args.out,
            )
        )


if __name__ == "__main__":
    main()
