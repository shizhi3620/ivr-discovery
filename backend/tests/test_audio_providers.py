"""Tests for the Tencent Cloud audio Provider."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from audio.base import AudioProviderError
from audio.tencent_provider import TencentAudioProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestTencentAudioProvider:
    def test_requires_credentials(self):
        provider = TencentAudioProvider(secret_id="", secret_key="")
        assert provider.is_configured is False

    @pytest.mark.asyncio
    async def test_transcribes_local_audio_by_polling(self, tmp_path: Path):
        recording = tmp_path / "call.wav"
        recording.write_bytes(b"RIFF-test-audio")
        requests: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            action = request.headers["X-TC-Action"]
            payload = json.loads(request.content)
            requests.append((action, payload))
            assert request.headers["Authorization"].startswith("TC3-HMAC-SHA256 ")
            assert "SignedHeaders=content-type;host" in request.headers["Authorization"]

            if action == "CreateRecTask":
                return httpx.Response(
                    200,
                    json={"Response": {"Data": {"TaskId": 123}, "RequestId": "r1"}},
                )
            if action == "DescribeTaskStatus":
                return httpx.Response(
                    200,
                    json={
                        "Response": {
                            "Data": {
                                "TaskId": 123,
                                "Status": 2,
                                "StatusStr": "success",
                                "Result": " 按1查询话费，按2人工服务。 ",
                            },
                            "RequestId": "r2",
                        }
                    },
                )
            raise AssertionError(f"Unexpected action: {action}")

        provider = TencentAudioProvider(
            secret_id="secret-id",
            secret_key="secret-key",
            poll_interval=0,
            client=_client(handler),
        )
        try:
            transcript = await provider.transcribe(recording)
        finally:
            await provider._client.aclose()

        assert transcript == "按1查询话费，按2人工服务。"
        assert [action for action, _ in requests] == [
            "CreateRecTask",
            "DescribeTaskStatus",
        ]
        create_payload = requests[0][1]
        assert create_payload["EngineModelType"] == "8k_zh_large"
        assert create_payload["SourceType"] == 1
        assert create_payload["ChannelNum"] == 2
        assert create_payload["DataLen"] == len(b"RIFF-test-audio")
        assert base64.b64decode(create_payload["Data"]) == b"RIFF-test-audio"

    @pytest.mark.asyncio
    async def test_failed_asr_task_raises(self, tmp_path: Path):
        recording = tmp_path / "call.wav"
        recording.write_bytes(b"RIFF")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["X-TC-Action"] == "CreateRecTask":
                return httpx.Response(
                    200,
                    json={"Response": {"Data": {"TaskId": 7}, "RequestId": "r1"}},
                )
            return httpx.Response(
                200,
                json={
                    "Response": {
                        "Data": {"TaskId": 7, "Status": 3, "ErrorMsg": "bad audio"},
                        "RequestId": "r2",
                    }
                },
            )

        provider = TencentAudioProvider(
            secret_id="secret-id",
            secret_key="secret-key",
            poll_interval=0,
            client=_client(handler),
        )
        try:
            with pytest.raises(AudioProviderError, match="bad audio"):
                await provider.transcribe(recording)
        finally:
            await provider._client.aclose()

    @pytest.mark.asyncio
    async def test_rejects_local_audio_over_five_megabytes(self, tmp_path: Path):
        recording = tmp_path / "large.wav"
        recording.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        provider = TencentAudioProvider(secret_id="id", secret_key="key")

        with pytest.raises(AudioProviderError, match="5 MB"):
            await provider.transcribe(recording)

    @pytest.mark.asyncio
    async def test_synthesizes_wav(self, tmp_path: Path):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "Response": {
                        "Audio": base64.b64encode(b"RIFF-tts").decode("ascii"),
                        "SessionId": "session",
                        "RequestId": "r1",
                    }
                },
            )

        provider = TencentAudioProvider(
            secret_id="secret-id",
            secret_key="secret-key",
            tts_voice_type=101001,
            client=_client(handler),
        )
        output = tmp_path / "nested" / "prompt.wav"
        try:
            result = await provider.synthesize("查询话费", output)
        finally:
            await provider._client.aclose()

        assert result == output
        assert output.read_bytes() == b"RIFF-tts"
        assert captured[0]["Text"] == "查询话费"
        assert captured[0]["SampleRate"] == 8000
        assert captured[0]["Codec"] == "wav"
        assert captured[0]["VoiceType"] == 101001
