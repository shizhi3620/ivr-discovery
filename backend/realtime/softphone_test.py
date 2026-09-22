"""Local LAN test using Linphone as a real RTP media endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import websockets
from dotenv import load_dotenv

from providers.android_sim_provider import AndroidSimGatewayProvider
from providers.esl import EslClient


class SoftphoneHarness:
    def __init__(
        self,
        *,
        wav_path: Path,
        relay_base: str,
        target_key: str,
        encoding: str,
        mix_type: str,
        gain: float,
    ):
        self.wav_path = wav_path
        self.relay_base = relay_base.rstrip("/")
        self.target_key = target_key
        self.encoding = encoding
        self.mix_type = mix_type
        self.gain = gain
        provider = AndroidSimGatewayProvider()
        self.domain = provider.config.domain
        self.esl_config = provider._esl_config

    def run_api(self, command: str) -> str:
        with EslClient(self.esl_config) as client:
            return client.api(command)

    async def run(self) -> None:
        events: asyncio.Queue[dict] = asyncio.Queue()

        async def consume() -> None:
            async with websockets.connect(
                f"{self.relay_base}/events",
                max_size=None,
            ) as websocket:
                async for raw in websocket:
                    await events.put(json.loads(raw))

        event_task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)

        originate = (
            "originate {origination_caller_id_number=ivr-local-playback}"
            f"user/softphone@{self.domain} &playback({self.wav_path})"
        )
        print("请接听 Linphone 上的本地测试呼叫")
        print("originate=", await asyncio.to_thread(self.run_api, f"bgapi {originate}"))

        channel_id = await self._wait_for_answer()
        if not channel_id:
            print("answer_timeout")
            event_task.cancel()
            return
        print("answered=", channel_id)

        channels = 1 if self.mix_type in ("mono", "mixed") else 2
        metadata = json.dumps(
            {
                "channel_uuid": channel_id,
                "exploration_call_id": str(uuid.uuid4()),
                "target_key": self.target_key,
                "encoding": self.encoding,
                "sample_rate": 8000,
                "channels": channels,
                "remote_channel": 0,
                "silence_ms": 800,
                "menu_completion_ms": 3000,
                "no_speech_timeout_ms": 30000,
                "gain": self.gain,
            },
            separators=(",", ":"),
        )
        print(
            "stream=",
            await asyncio.to_thread(
                self.run_api,
                f"uuid_audio_stream {channel_id} start "
                f"{self.relay_base}/audio {self.mix_type} 8000 {metadata}",
            ),
        )

        try:
            while True:
                event = await asyncio.wait_for(events.get(), timeout=35)
                if event.get("channel_uuid") != channel_id:
                    continue
                print(json.dumps(event, ensure_ascii=False))
                if event.get("event_type") in (
                    "dtmf_ready",
                    "human_boundary",
                    "unknown_boundary",
                    "technical_unknown",
                    "asr_error",
                ):
                    if event.get("event_type") == "dtmf_ready":
                        print(
                            "dtmf_inject=",
                            await asyncio.to_thread(
                                self.run_api,
                                f"uuid_send_dtmf {channel_id} {event['key']}",
                            ),
                        )
                    break
        finally:
            try:
                await asyncio.to_thread(
                    self.run_api,
                    f"uuid_audio_stream {channel_id} stop",
                )
            except Exception:
                pass
            try:
                await asyncio.to_thread(
                    self.run_api,
                    f"uuid_kill {channel_id}",
                )
            except Exception:
                pass
            event_task.cancel()

    async def _wait_for_answer(self) -> str | None:
        for _ in range(450):
            await asyncio.sleep(0.1)
            payload = json.loads(
                await asyncio.to_thread(
                    self.run_api,
                    "show channels as json",
                )
            )
            row = next(
                (
                    item
                    for item in payload.get("rows", [])
                    if "softphone" in str(item.get("name"))
                ),
                None,
            )
            if row and row.get("callstate") == "ACTIVE":
                return str(row["uuid"])
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LAN softphone realtime test")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--relay", default="ws://127.0.0.1:18031")
    parser.add_argument("--target-key", default="1")
    parser.add_argument("--encoding", default="s16le")
    parser.add_argument(
        "--mix-type",
        choices=("mono", "mixed", "stereo"),
        default="mixed",
    )
    parser.add_argument("--gain", type=float, default=1.0)
    args = parser.parse_args()

    load_dotenv(".env")
    asyncio.run(
        SoftphoneHarness(
            wav_path=args.wav,
            relay_base=args.relay,
            target_key=args.target_key,
            encoding=args.encoding,
            mix_type=args.mix_type,
            gain=args.gain,
        ).run()
    )


if __name__ == "__main__":
    main()
