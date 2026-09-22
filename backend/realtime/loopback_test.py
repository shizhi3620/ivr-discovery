"""Local FreeSWITCH loopback test for realtime ASR and DTMF control."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import websockets
from dotenv import load_dotenv

from providers.android_sim_provider import GatewayConfig
from providers.esl import EslClient, EslConfig


class LoopbackHarness:
    def __init__(
        self,
        *,
        wav_path: Path,
        relay_base: str,
        target_key: str,
        encoding: str,
        remote_channel: int,
        stream_leg: str,
        mix_type: str,
        gain: float,
    ):
        self.wav_path = wav_path
        self.relay_base = relay_base.rstrip("/")
        self.target_key = target_key
        self.encoding = encoding
        self.remote_channel = remote_channel
        self.stream_leg = stream_leg
        self.mix_type = mix_type
        self.gain = gain
        config = GatewayConfig.from_env()
        self.esl_config = EslConfig(
            host=config.esl_host,
            port=config.esl_port,
            password=config.esl_password,
        )

    def run_api(self, command: str) -> str:
        with EslClient(self.esl_config) as client:
            return client.api(command)

    async def run(self) -> None:
        test_id = f"realtime-loopback-{uuid.uuid4()}"
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def consume_events() -> None:
            async with websockets.connect(
                f"{self.relay_base}/events",
                max_size=None,
            ) as websocket:
                async for raw in websocket:
                    event = json.loads(raw)
                    print(json.dumps(event, ensure_ascii=False))
                    await events.put(event)

        event_task = asyncio.create_task(consume_events())
        await asyncio.sleep(0.2)

        originate = (
            "originate {"
            f"origination_caller_id_number={test_id},"
            f"test_audio_path={self.wav_path}"
            "}loopback/9998 &park()"
        )
        print("originate=", await asyncio.to_thread(self.run_api, f"bgapi {originate}"))

        aleg, bleg = await self._wait_for_loopback(test_id)
        print("aleg=", aleg)
        print("bleg=", bleg)
        stream_channel = aleg if self.stream_leg == "aleg" else bleg
        channels = 1 if self.mix_type in ("mono", "mixed") else 2
        if self.mix_type in ("mono", "mixed"):
            remote_channel = 0
        else:
            remote_channel = self.remote_channel

        metadata = json.dumps(
            {
                "channel_uuid": stream_channel,
                "exploration_call_id": test_id,
                "target_key": self.target_key,
                "encoding": self.encoding,
                "sample_rate": 8000,
                "channels": channels,
                "remote_channel": remote_channel,
                "silence_ms": 800,
                "menu_completion_ms": 3000,
                "no_speech_timeout_ms": 5000,
                "gain": self.gain,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        start_command = (
            f"uuid_audio_stream {stream_channel} start "
            f"{self.relay_base}/audio "
            f"{self.mix_type} 8000 {metadata}"
        )
        print(
            "audio_stream_start=",
            await asyncio.to_thread(self.run_api, start_command),
        )
        print(
            "audio_broadcast=",
            await asyncio.to_thread(
                self.run_api,
                f"uuid_broadcast {stream_channel} {self.wav_path} aleg",
            ),
        )

        try:
            terminal = await asyncio.wait_for(self._next_terminal_event(events), 35)
            print("terminal_event=", json.dumps(terminal, ensure_ascii=False))

            if terminal["event_type"] == "dtmf_ready":
                result = await asyncio.to_thread(
                    self.run_api,
                    f"uuid_send_dtmf {aleg} {terminal['key']}",
                )
                print("dtmf_inject=", result)
                await asyncio.sleep(2)
                dtmf_result = Path("/tmp/realtime-dtmf-result")
                print(
                    "dtmf_captured=",
                    dtmf_result.read_text().strip()
                    if dtmf_result.exists()
                    else "",
                )
        finally:
            await asyncio.to_thread(
                self.run_api,
                f"uuid_audio_stream {stream_channel} stop",
            )
            for channel in (aleg, bleg):
                try:
                    await asyncio.to_thread(
                        self.run_api,
                        f"uuid_kill {channel}",
                    )
                except Exception:
                    pass
            event_task.cancel()

    async def _wait_for_loopback(self, test_id: str) -> tuple[str, str]:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            payload = json.loads(
                await asyncio.to_thread(
                    self.run_api,
                    "show channels as json",
                )
            )
            rows = payload.get("rows", [])
            a_leg = next(
                (
                    row
                    for row in rows
                    if str(row.get("direction")) == "outbound"
                    and str(row.get("dest")) == "9998"
                ),
                None,
            )
            b_leg = next(
                (
                    row
                    for row in rows
                    if str(row.get("cid_num")) == test_id
                ),
                None,
            )
            if a_leg and b_leg:
                return str(a_leg["uuid"]), str(b_leg["uuid"])
            await asyncio.sleep(0.1)
        raise RuntimeError("Timed out waiting for loopback channels")

    @staticmethod
    async def _next_terminal_event(
        queue: asyncio.Queue[dict[str, Any]],
    ) -> dict[str, Any]:
        terminal_types = {
            "dtmf_ready",
            "human_boundary",
            "unknown_boundary",
            "technical_unknown",
            "asr_error",
        }
        while True:
            event = await queue.get()
            if event.get("event_type") in terminal_types:
                return event


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local realtime loopback test")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--relay", default="ws://127.0.0.1:18031")
    parser.add_argument("--target-key", default="1")
    parser.add_argument("--encoding", default="s16le")
    parser.add_argument("--remote-channel", type=int, default=0)
    parser.add_argument("--stream-leg", choices=("aleg", "bleg"), default="bleg")
    parser.add_argument(
        "--mix-type",
        choices=("mono", "mixed", "stereo"),
        default="stereo",
    )
    parser.add_argument("--gain", type=float, default=1.0)
    args = parser.parse_args()

    load_dotenv(".env")
    asyncio.run(
        LoopbackHarness(
            wav_path=args.wav,
            relay_base=args.relay,
            target_key=args.target_key,
            encoding=args.encoding,
            remote_channel=args.remote_channel,
            stream_leg=args.stream_leg,
            mix_type=args.mix_type,
            gain=args.gain,
        ).run()
    )


if __name__ == "__main__":
    main()
