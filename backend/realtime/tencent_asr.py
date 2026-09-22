"""Async Tencent realtime speech recognition WebSocket client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

RealtimeCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class TencentRealtimeASRError(RuntimeError):
    pass


class TencentRealtimeASRClient:
    """One realtime ASR session bound to one audio stream."""

    def __init__(
        self,
        *,
        app_id: str,
        secret_id: str,
        secret_key: str,
        engine_model_type: str = "8k_zh",
        on_message: RealtimeCallback,
        voice_format: int = 1,
    ) -> None:
        self.app_id = app_id
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.engine_model_type = engine_model_type
        self.on_message = on_message
        self.voice_format = voice_format
        self.voice_id = str(uuid.uuid4())
        self._ws: Any = None
        self._receiver_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def start(self) -> dict[str, Any]:
        self._require_credentials()
        uri = build_realtime_uri(
            app_id=self.app_id,
            secret_id=self.secret_id,
            secret_key=self.secret_key,
            engine_model_type=self.engine_model_type,
            voice_id=self.voice_id,
            voice_format=self.voice_format,
        )
        self._ws = await websockets.connect(
            uri,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
        first_raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        first = json.loads(first_raw)
        if int(first.get("code", -1)) != 0:
            await self.close()
            raise TencentRealtimeASRError(
                f"Tencent realtime ASR rejected connection: "
                f"{first.get('code')} {first.get('message')}"
            )
        self._receiver_task = asyncio.create_task(self._receive_loop())
        return first

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            raise TencentRealtimeASRError("Realtime ASR is not connected")
        await self._ws.send(pcm)

    async def finish(self, timeout: float = 10.0) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "end"}))
            if self._receiver_task:
                await asyncio.wait_for(self._receiver_task, timeout=timeout)
        except (asyncio.TimeoutError, websockets.WebSocketException):
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
        self._receiver_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if int(message.get("code", 0)) != 0:
                    raise TencentRealtimeASRError(
                        f"Tencent realtime ASR error: "
                        f"{message.get('code')} {message.get('message')}"
                    )
                callback_result = self.on_message(message)
                if inspect.isawaitable(callback_result):
                    await callback_result
                if int(message.get("final", 0)) == 1:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            callback_result = self.on_message(
                {
                    "code": -1,
                    "final": 1,
                    "message": str(exc),
                    "error": True,
                }
            )
            if inspect.isawaitable(callback_result):
                await callback_result

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("TENCENTCLOUD_APP_ID", self.app_id),
                ("TENCENTCLOUD_SECRET_ID", self.secret_id),
                ("TENCENTCLOUD_SECRET_KEY", self.secret_key),
            )
            if not value
        ]
        if missing:
            raise TencentRealtimeASRError(
                f"Missing Tencent realtime ASR credentials: {', '.join(missing)}"
            )


def build_realtime_uri(
    *,
    app_id: str,
    secret_id: str,
    secret_key: str,
    engine_model_type: str,
    voice_id: str,
    voice_format: int = 1,
    timestamp: int | None = None,
) -> str:
    timestamp = timestamp or int(time.time())
    params = {
        "appid": app_id,
        "secretid": secret_id,
        "timestamp": str(timestamp),
        "nonce": str(timestamp),
        "expired": str(timestamp + 24 * 60 * 60),
        "engine_model_type": engine_model_type,
        "voice_id": voice_id,
        "voice_format": str(voice_format),
        "needvad": "1",
        "convert_num_mode": "1",
    }
    sorted_params = sorted(params.items(), key=lambda item: item[0])
    sign_string = f"asr.cloud.tencent.com/asr/v2/{app_id}?"
    sign_string += "&".join(
        f"{key}={value}" for key, value in sorted_params if key != "appid"
    )
    signature = base64.b64encode(
        hmac.new(
            secret_key.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")
    query = urllib.parse.urlencode(
        [(key, value) for key, value in sorted_params if key != "appid"]
    )
    return (
        f"wss://asr.cloud.tencent.com/asr/v2/{app_id}?"
        f"{query}&signature={urllib.parse.quote(signature)}"
    )
