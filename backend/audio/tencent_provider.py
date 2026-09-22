"""Tencent Cloud implementation of the Audio Provider boundary.

The implementation uses the public JSON API directly instead of the Tencent
Cloud SDK. This keeps the dependency surface small while following the same
TC3-HMAC-SHA256 request signing contract as the official SDK.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from audio.base import AudioCapabilities, AudioProviderError

logger = logging.getLogger(__name__)

ASR_HOST = "asr.tencentcloudapi.com"
ASR_SERVICE = "asr"
ASR_VERSION = "2019-06-14"
TTS_HOST = "tts.tencentcloudapi.com"
TTS_SERVICE = "tts"
TTS_VERSION = "2019-08-23"
JSON_CONTENT_TYPE = "application/json"
MAX_LOCAL_AUDIO_BYTES = 5 * 1024 * 1024


class TencentAudioProvider:
    """Tencent Cloud 8 kHz Mandarin ASR and speech synthesis."""

    name = "tencent"
    capabilities = AudioCapabilities(transcription=True, synthesis=True)

    def __init__(
        self,
        *,
        secret_id: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        asr_engine_model: str | None = None,
        asr_channel_num: int | None = None,
        tts_voice_type: int | None = None,
        tts_sample_rate: int | None = None,
        poll_interval: float = 3.0,
        max_polls: int = 100,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.secret_id = (
            secret_id
            if secret_id is not None
            else os.getenv("TENCENTCLOUD_SECRET_ID", "")
        ).strip()
        self.secret_key = (
            secret_key
            if secret_key is not None
            else os.getenv("TENCENTCLOUD_SECRET_KEY", "")
        ).strip()
        self.region = (
            region
            if region is not None
            else os.getenv("TENCENTCLOUD_REGION", "ap-guangzhou")
        ).strip()
        self.asr_engine_model = (
            asr_engine_model
            if asr_engine_model is not None
            else os.getenv("TENCENT_ASR_ENGINE_MODEL", "8k_zh")
        )
        self.asr_channel_num = (
            asr_channel_num
            if asr_channel_num is not None
            else int(os.getenv("TENCENT_ASR_CHANNEL_NUM", "2"))
        )
        configured_voice = os.getenv("TENCENT_TTS_VOICE_TYPE", "").strip()
        self.tts_voice_type = (
            tts_voice_type
            if tts_voice_type is not None
            else int(configured_voice) if configured_voice else None
        )
        self.tts_sample_rate = (
            tts_sample_rate
            if tts_sample_rate is not None
            else int(os.getenv("TENCENT_TTS_SAMPLE_RATE", "8000"))
        )
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.timeout = timeout
        self._client = client

    @property
    def is_configured(self) -> bool:
        return bool(self.secret_id and self.secret_key)

    async def transcribe(self, audio_path: str | Path) -> str:
        if not self.is_configured:
            raise AudioProviderError(
                "Tencent ASR requires TENCENTCLOUD_SECRET_ID and "
                "TENCENTCLOUD_SECRET_KEY"
            )

        path = Path(audio_path)
        try:
            audio = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise AudioProviderError(f"Cannot read call recording {path}: {exc}") from exc

        if not audio:
            raise AudioProviderError(f"Call recording is empty: {path}")
        if len(audio) > MAX_LOCAL_AUDIO_BYTES:
            raise AudioProviderError(
                f"Call recording exceeds Tencent's 5 MB local-audio limit: {path}"
            )

        payload = {
            "EngineModelType": self.asr_engine_model,
            "ChannelNum": self.asr_channel_num,
            "ResTextFormat": 0,
            "SourceType": 1,
            "Data": base64.b64encode(audio).decode("ascii"),
            "DataLen": len(audio),
        }

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self._call(
                client,
                host=ASR_HOST,
                service=ASR_SERVICE,
                action="CreateRecTask",
                version=ASR_VERSION,
                payload=payload,
            )
            task_id = (response.get("Data") or {}).get("TaskId")
            if task_id is None:
                raise AudioProviderError("Tencent ASR returned no TaskId")

            return await self._wait_for_transcript(client, task_id)
        finally:
            if owns_client:
                await client.aclose()

    async def synthesize(self, text: str, output_path: str | Path) -> Path:
        if not self.is_configured:
            raise AudioProviderError(
                "Tencent TTS requires TENCENTCLOUD_SECRET_ID and "
                "TENCENTCLOUD_SECRET_KEY"
            )
        if not text.strip():
            raise AudioProviderError("Cannot synthesize empty text")

        payload: dict[str, Any] = {
            "Text": text,
            "SessionId": str(uuid.uuid4()),
            "ModelType": 1,
            "PrimaryLanguage": 1,
            "SampleRate": self.tts_sample_rate,
            "Codec": "wav",
            "Speed": 0.0,
            "Volume": 0.0,
        }
        if self.tts_voice_type is not None:
            payload["VoiceType"] = self.tts_voice_type

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self._call(
                client,
                host=TTS_HOST,
                service=TTS_SERVICE,
                action="TextToVoice",
                version=TTS_VERSION,
                payload=payload,
            )
            encoded_audio = response.get("Audio")
            if not encoded_audio:
                raise AudioProviderError("Tencent TTS returned no Audio data")
            try:
                audio = base64.b64decode(encoded_audio, validate=True)
            except (ValueError, TypeError) as exc:
                raise AudioProviderError("Tencent TTS returned invalid Base64") from exc

            path = Path(output_path)
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, audio)
            return path
        finally:
            if owns_client:
                await client.aclose()

    async def _wait_for_transcript(
        self,
        client: httpx.AsyncClient,
        task_id: Any,
    ) -> str:
        for attempt in range(self.max_polls):
            response = await self._call(
                client,
                host=ASR_HOST,
                service=ASR_SERVICE,
                action="DescribeTaskStatus",
                version=ASR_VERSION,
                payload={"TaskId": task_id},
            )
            data = response.get("Data") or {}
            status = int(data.get("Status", 0))

            if status == 2:
                return _normalize_transcript(str(data.get("Result") or ""))
            if status == 3:
                detail = data.get("ErrorMsg") or data.get("StatusStr") or "unknown error"
                raise AudioProviderError(f"Tencent ASR task failed: {detail}")

            if attempt < self.max_polls - 1:
                await asyncio.sleep(self.poll_interval)

        raise AudioProviderError(
            f"Tencent ASR task {task_id} did not finish within "
            f"{self.max_polls * self.poll_interval:.0f} seconds"
        )

    async def _call(
        self,
        client: httpx.AsyncClient,
        *,
        host: str,
        service: str,
        action: str,
        version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timestamp = int(time.time())
        headers = {
            "Authorization": _build_authorization(
                secret_id=self.secret_id,
                secret_key=self.secret_key,
                service=service,
                host=host,
                timestamp=timestamp,
                body=body,
            ),
            "Content-Type": JSON_CONTENT_TYPE,
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": version,
        }
        if self.region:
            headers["X-TC-Region"] = self.region

        try:
            response = await client.post(
                f"https://{host}",
                headers=headers,
                content=body.encode("utf-8"),
            )
            response.raise_for_status()
            parsed = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AudioProviderError(f"Tencent API request failed: {exc}") from exc

        result = parsed.get("Response") if isinstance(parsed, dict) else None
        if not isinstance(result, dict):
            raise AudioProviderError("Tencent API returned an invalid response")
        error = result.get("Error")
        if isinstance(error, dict):
            code = error.get("Code", "Unknown")
            message = error.get("Message", "unknown error")
            raise AudioProviderError(f"Tencent API error {code}: {message}")
        return result


def _build_authorization(
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    timestamp: int,
    body: str,
) -> str:
    """Build the TC3-HMAC-SHA256 Authorization header.

    Tencent's official SDK signs only ``content-type;host``. The action,
    version, timestamp and region are sent as additional public headers.
    """
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    canonical_headers = f"content-type:{JSON_CONTENT_TYPE}\nhost:{host}\n"
    signed_headers = "content-type;host"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = _sign_tc3(secret_key, date, service, string_to_sign)
    return (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _sign_tc3(secret_key: str, date: str, service: str, string_to_sign: str) -> str:
    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    return hmac.new(
        secret_signing,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalize_transcript(text: str) -> str:
    """Remove Tencent's per-line channel/time markers from ``Result``."""
    marker = re.compile(
        r"^\[\d+:\d+(?:\.\d+)?,\d+:\d+(?:\.\d+)?,\d+\]\s*",
        re.MULTILINE,
    )
    return marker.sub("", text).strip()


__all__ = ["TencentAudioProvider"]
