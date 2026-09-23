"""Local realtime audio relay for FreeSWITCH and Tencent realtime ASR."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import websockets
from dotenv import load_dotenv

from realtime.audio import (
    AudioFormatError,
    apply_gain_pcm16,
    decode_to_mono_pcm,
    pcm16_rms,
    resample_pcm16_mono,
)
from realtime.decision import RealtimeDecisionEngine
from realtime.events import EventBus
from realtime.shadow import ShadowJudge
from realtime.tencent_asr import TencentRealtimeASRClient, TencentRealtimeASRError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelayConfig:
    host: str = "127.0.0.1"
    port: int = 18031
    app_id: str = ""
    secret_id: str = ""
    secret_key: str = ""
    engine_model_type: str = "16k_zh"

    @classmethod
    def from_env(cls) -> "RelayConfig":
        load_dotenv()
        return cls(
            host=os.getenv("REALTIME_RELAY_HOST", "127.0.0.1"),
            port=int(os.getenv("REALTIME_RELAY_PORT", "18031")),
            app_id=os.getenv("TENCENTCLOUD_APP_ID", "").strip(),
            secret_id=os.getenv("TENCENTCLOUD_SECRET_ID", "").strip(),
            secret_key=os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip(),
            engine_model_type=os.getenv(
                "TENCENT_REALTIME_ASR_ENGINE_MODEL",
                "16k_zh",
            ),
        )


class RealtimeRelay:
    def __init__(self, config: RelayConfig):
        self.config = config
        self.event_bus = EventBus()
        self.shadow_judge = ShadowJudge.from_env()
        self._server: Any = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            max_size=None,
        )
        logger.info(
            "Realtime relay listening on ws://%s:%s",
            self.config.host,
            self.config.port,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_connection(self, websocket, path: str) -> None:
        if path == "/events":
            await self._handle_events(websocket)
        elif path == "/audio":
            await self._handle_audio(websocket)
        else:
            await websocket.close(code=1008, reason="unknown relay path")

    async def _handle_events(self, websocket) -> None:
        queue = self.event_bus.subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send(json.dumps(event, ensure_ascii=False))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.event_bus.unsubscribe(queue)

    async def _handle_audio(self, websocket) -> None:
        asr: TencentRealtimeASRClient | None = None
        decision: RealtimeDecisionEngine | None = None
        metadata: dict[str, Any] = {}
        try:
            first = await asyncio.wait_for(websocket.recv(), timeout=10)
            if not isinstance(first, str):
                raise ValueError("first audio message must be JSON metadata")
            metadata = json.loads(first)
            channel_uuid = str(metadata["channel_uuid"])
            exploration_call_id = str(metadata["exploration_call_id"])
            target_key = metadata.get("target_key")
            encoding = str(metadata.get("encoding", "l16be"))
            channels = int(metadata.get("channels", 2))
            remote_channel = int(metadata.get("remote_channel", 0))
            sample_rate = int(metadata.get("sample_rate", 8000))
            gain = float(metadata.get("gain", 1.0))
            debug_audio = os.getenv("REALTIME_DEBUG_AUDIO") == "1"
            frame_count = 0

            async def publish(event: dict[str, Any]) -> None:
                await self.event_bus.publish(event)

            decision = RealtimeDecisionEngine(
                channel_uuid=channel_uuid,
                exploration_call_id=exploration_call_id,
                target_key=str(target_key) if target_key is not None else None,
                on_event=publish,
                silence_ms=int(metadata.get("silence_ms", 800)),
                menu_completion_ms=int(
                    metadata.get("menu_completion_ms", 8000)
                ),
                no_speech_timeout_ms=int(
                    metadata.get("no_speech_timeout_ms", 5000)
                ),
                allow_target_key_fallback=bool(
                    metadata.get("allow_target_key_fallback", False)
                ),
                shadow_judge=self.shadow_judge,
                hold_through_human_boundary=bool(
                    metadata.get("hold_through_human_boundary", False)
                ),
                human_boundary_menu_grace_ms=int(
                    metadata.get(
                        "human_boundary_menu_grace_ms",
                        os.getenv("CALL_HUMAN_BOUNDARY_MENU_GRACE_MS", "12000"),
                    )
                ),
            )

            async def on_asr_message(message: dict[str, Any]) -> None:
                if message.get("error"):
                    await self.event_bus.publish(
                        {
                            "channel_uuid": channel_uuid,
                            "exploration_call_id": exploration_call_id,
                            "event_type": "asr_error",
                            "text": str(message.get("message") or ""),
                        }
                    )
                    await decision.close()
                    return

                sentences = (message.get("sentences") or {}).get(
                    "sentence_list",
                    [],
                )
                if not sentences and isinstance(message.get("result"), dict):
                    result = message["result"]
                    text = str(
                        result.get("voice_text_str")
                        or result.get("voice_text")
                        or ""
                    ).strip()
                    if text:
                        slice_type = int(result.get("slice_type", 0))
                        sentences = [
                            {
                                "sentence": text,
                                "sentence_type": 1 if slice_type == 2 else 0,
                            }
                        ]
                for sentence in sentences:
                    text = str(sentence.get("sentence") or "").strip()
                    if not text:
                        continue
                    is_final = int(sentence.get("sentence_type", 0)) == 1
                    await self.event_bus.publish(
                        {
                            "channel_uuid": channel_uuid,
                            "exploration_call_id": exploration_call_id,
                            "event_type": "final" if is_final else "partial",
                            "text": text,
                        }
                    )
                    await decision.feed(text, is_final=is_final)

            asr = TencentRealtimeASRClient(
                app_id=self.config.app_id,
                secret_id=self.config.secret_id,
                secret_key=self.config.secret_key,
                engine_model_type=self.config.engine_model_type,
                on_message=on_asr_message,
            )
            await asr.start()
            await decision.start()
            await self.event_bus.publish(
                {
                    "channel_uuid": channel_uuid,
                    "exploration_call_id": exploration_call_id,
                    "event_type": "asr_connected",
                    "text": "",
                }
            )

            async for payload in websocket:
                if not isinstance(payload, bytes):
                    continue
                frame_count += 1
                pcm = decode_to_mono_pcm(
                    payload,
                    encoding=encoding,
                    channels=channels,
                    remote_channel=remote_channel,
                )
                pcm = resample_pcm16_mono(
                    pcm,
                    source_rate=sample_rate,
                    target_rate=16000,
                )
                pcm = apply_gain_pcm16(pcm, gain)
                if debug_audio and (
                    frame_count <= 5 or frame_count % 50 == 0
                ):
                    logger.info(
                        "audio frame=%d bytes=%d rms=%.1f channel=%d",
                        frame_count,
                        len(pcm),
                        pcm16_rms(pcm),
                        remote_channel,
                    )
                await asr.send_audio(pcm)
        except asyncio.CancelledError:
            raise
        except (AudioFormatError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("Rejected realtime audio stream: %s", exc)
            await websocket.close(code=1008, reason=str(exc))
        except TencentRealtimeASRError as exc:
            logger.error("Tencent realtime ASR connection failed: %s", exc)
            await websocket.close(code=1011, reason=str(exc)[:120])
        except websockets.ConnectionClosed:
            pass
        finally:
            if decision is not None:
                await decision.close()
            if asr is not None:
                await asr.finish()
            if metadata:
                await self.event_bus.publish(
                    {
                        "channel_uuid": metadata.get("channel_uuid", ""),
                        "exploration_call_id": metadata.get(
                            "exploration_call_id",
                            "",
                        ),
                        "event_type": "stream_stopped",
                        "text": "",
                    }
                )


async def _run(config: RelayConfig) -> None:
    relay = RealtimeRelay(config)
    await relay.start()
    try:
        await asyncio.Future()
    finally:
        await relay.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the realtime audio relay")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    config = RelayConfig.from_env()
    if args.host is not None or args.port is not None:
        config = RelayConfig(
            host=args.host or config.host,
            port=args.port or config.port,
            app_id=config.app_id,
            secret_id=config.secret_id,
            secret_key=config.secret_key,
            engine_model_type=config.engine_model_type,
        )
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
