"""Tests for the telephony Provider boundary (docs/adr/0004)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import providers
from audio.base import AudioCapabilities
from providers.base import (
    CallResult,
    ProviderCapabilities,
    STATUS_BUSY,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    TERMINAL_STATUSES,
)
from providers.bland_provider import BlandProvider
from providers.esl import EslClient, EslConfig


class FakeAudioProvider:
    name = "fake"
    capabilities = AudioCapabilities(transcription=True, synthesis=True)

    def __init__(
        self,
        *,
        configured: bool = True,
        transcript: str = "Press 1 for billing.",
    ):
        self.is_configured = configured
        self.transcript = transcript
        self.transcribed_paths: list[Path] = []
        self.synthesized: list[tuple[str, Path]] = []

    async def transcribe(self, audio_path: str | Path) -> str:
        path = Path(audio_path)
        self.transcribed_paths.append(path)
        return self.transcript

    async def synthesize(self, text: str, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF-tts")
        self.synthesized.append((text, path))
        return path


def fake_esl(captured: list[str], channel_id: str = "actual-call-id"):
    """Simulate ESL while exposing the real channel UUID resolution path."""
    state = {"marker": ""}

    def run(command: str) -> str:
        captured.append(command)
        if command.startswith("bgapi originate"):
            marker = command.split("ivr_discovery_call_id=", 1)[1].split(",", 1)[0]
            state["marker"] = marker
            return "+OK"
        if command == "show channels as json":
            return json.dumps(
                {
                    "rows": [
                        {
                            "uuid": channel_id,
                            "call_uuid": channel_id,
                            "direction": "outbound",
                            "cid_num": state["marker"],
                        }
                    ]
                }
            )
        if command.endswith(" ivr_discovery_call_id"):
            return state["marker"]
        if command.endswith(" callstate"):
            return "ACTIVE"
        if command.startswith("uuid_record "):
            path = Path(command.split(" start ", 1)[1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"RIFF")
            return "+OK"
        return "+OK"

    return run


class TestCallResult:
    def test_terminal_detection(self):
        assert CallResult("c", STATUS_COMPLETED).is_terminal()
        assert CallResult("c", STATUS_BUSY).is_terminal()
        assert not CallResult("c", STATUS_IN_PROGRESS).is_terminal()

    def test_legacy_dict_shape(self):
        """Keeps existing discovery/frontend code working during migration."""
        result = CallResult("c1", STATUS_COMPLETED, transcript="hi", cost=1.5)
        legacy = result.as_legacy_dict()
        assert legacy["call_id"] == "c1"
        assert legacy["status"] == STATUS_COMPLETED
        assert legacy["concatenated_transcript"] == "hi"
        assert legacy["price"] == 1.5


class TestProviderSelection:
    def setup_method(self):
        providers.reset_providers()

    def teardown_method(self):
        providers.reset_providers()

    def test_default_is_android_sim(self):
        """China mainland scenario defaults to the SIM gateway (ADR 0004)."""
        with patch.dict("os.environ", {}, clear=True):
            provider = providers.get_provider()
        assert provider.name == "android_sim"

    def test_env_override(self):
        with patch.dict("os.environ", {"TELEPHONY_PROVIDER": "bland"}, clear=True):
            provider = providers.get_provider()
        assert provider.name == "bland"

    def test_explicit_argument_beats_env(self):
        with patch.dict("os.environ", {"TELEPHONY_PROVIDER": "bland"}, clear=True):
            provider = providers.get_provider("android_sim")
        assert provider.name == "android_sim"

    def test_instances_are_cached(self):
        assert providers.get_provider("bland") is providers.get_provider("bland")

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown telephony provider"):
            providers.get_provider("twilio")


class TestBlandProvider:
    @pytest.mark.asyncio
    async def test_place_call_returns_call_id(self):
        provider = BlandProvider()
        with patch("providers.bland_provider.bland_client.place_call", new=AsyncMock(
            return_value={"call_id": "abc123"}
        )):
            call_id = await provider.place_call("+18002758777")
        assert call_id == "abc123"

    @pytest.mark.asyncio
    async def test_place_call_without_call_id_raises(self):
        provider = BlandProvider()
        with patch("providers.bland_provider.bland_client.place_call", new=AsyncMock(
            return_value={"error": "nope"}
        )):
            with pytest.raises(RuntimeError, match="no call_id"):
                await provider.place_call("+18002758777")

    @pytest.mark.asyncio
    async def test_voice_option_becomes_task(self):
        provider = BlandProvider()
        mock_place = AsyncMock(return_value={"call_id": "v1"})
        with patch("providers.bland_provider.bland_client.place_call", new=mock_place):
            await provider.place_call("+1800", voice_option="billing")
        _, kwargs = mock_place.call_args
        assert "billing" in kwargs["task"]
        assert kwargs["dtmf_sequence"] is None

    @pytest.mark.asyncio
    async def test_get_call_normalizes_fields(self):
        provider = BlandProvider()
        with patch("providers.bland_provider.bland_client.get_call", new=AsyncMock(
            return_value={
                "status": "completed",
                "concatenated_transcript": "Press 1 for billing",
                "price": 0.07,
            }
        )):
            result = await provider.get_call("c1")
        assert result.status == STATUS_COMPLETED
        assert result.transcript == "Press 1 for billing"
        assert result.cost == 0.07
        assert result.capabilities.transcript

    def test_capabilities_are_full(self):
        assert BlandProvider.capabilities == ProviderCapabilities(
            transcript=True, speech=True, dtmf=True
        )


class TestAndroidSimCapabilities:
    def test_advertises_no_transcript_without_audio_credentials(self):
        """Never dial if Tencent ASR is not configured."""
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider(
            audio_provider=FakeAudioProvider(configured=False)
        )
        assert provider.capabilities.transcript is False
        assert provider.capabilities.dtmf is True
        assert provider.capabilities.realtime_dtmf is True

    @pytest.mark.asyncio
    async def test_refuses_to_dial_without_audio_credentials(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider(
            audio_provider=FakeAudioProvider(configured=False)
        )
        with pytest.raises(Exception, match="requires a configured Audio Provider"):
            await provider.place_call("10010")

    @pytest.mark.asyncio
    async def test_place_call_originates_with_gsm_header_and_recording(self, tmp_path):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        config = GatewayConfig(
            domain="192.168.10.112",
            gateway_extension="gateway1",
            recording_dir=str(tmp_path / "recordings"),
        )
        provider = AndroidSimGatewayProvider(config, audio_provider=FakeAudioProvider())
        captured: list[str] = []

        with patch.object(provider, "_run_api", side_effect=fake_esl(captured)), patch(
            "providers.android_sim_provider.asyncio.create_task"
        ):
            call_id = await provider.place_call("10010")

        originate = next(c for c in captured if "originate" in c)
        assert "ivr_discovery_call_id=" in originate
        assert "sip_h_X-GSM-Destination=10010" in originate
        assert "user/gateway1@192.168.10.112" in originate
        assert call_id == "actual-call-id"
        recording = next(c for c in captured if c.startswith("uuid_record "))
        assert str(tmp_path / "recordings") in recording
        assert recording.endswith(".wav")
        assert provider.capabilities.transcript is True
        assert provider.capabilities.speech is True

    @pytest.mark.asyncio
    async def test_voice_option_is_synthesized_and_played(self, tmp_path):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        audio = FakeAudioProvider()
        provider = AndroidSimGatewayProvider(
            GatewayConfig(
                recording_dir=str(tmp_path / "recordings"),
                dtmf_initial_delay=0,
            ),
            audio_provider=audio,
        )
        captured: list[str] = []

        with patch.object(provider, "_run_api", side_effect=fake_esl(captured)), patch(
            "providers.android_sim_provider.asyncio.create_task"
        ) as create_task:
            call_id = await provider.place_call("10010", voice_option="查询话费")

        assert audio.synthesized[0][0] == "查询话费"
        create_task.assert_called_once()
        create_task.call_args.args[0].close()

        with patch.object(provider, "_run_api", side_effect=fake_esl(captured)):
            await provider._play_voice_after_answer(call_id, audio.synthesized[0][1])
        assert any("uuid_broadcast" in command for command in captured)

    @pytest.mark.asyncio
    async def test_wait_for_call_transcribes_recording(self, tmp_path):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        audio = FakeAudioProvider(transcript="按1查询话费。")
        provider = AndroidSimGatewayProvider(
            GatewayConfig(
                recording_dir=str(tmp_path / "recordings"),
                recording_finalize_timeout=0,
            ),
            audio_provider=audio,
        )
        recording = provider._recording_path("call-1")
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"RIFF")
        callback = AsyncMock()

        with patch.object(provider, "_run_api", return_value='{"rows":[]}'):
            result = await provider.wait_for_call("call-1", on_transcript=callback)

        assert result.status == STATUS_COMPLETED
        assert result.transcript == "按1查询话费。"
        assert audio.transcribed_paths == [recording]
        callback.assert_awaited_once_with("按1查询话费。")

    @pytest.mark.asyncio
    async def test_get_call_reports_completed_when_channel_gone(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider(
            audio_provider=FakeAudioProvider(configured=False)
        )
        with patch.object(provider, "_run_api", return_value='{"rows":[]}'):
            result = await provider.get_call("gone")
        assert result.status == STATUS_COMPLETED
        assert result.transcript == ""

    @pytest.mark.asyncio
    async def test_get_call_reports_in_progress_while_channel_exists(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider(
            audio_provider=FakeAudioProvider(configured=False)
        )
        payload = '{"rows":[{"uuid":"live-call"}]}'
        with patch.object(provider, "_run_api", return_value=payload):
            result = await provider.get_call("live-call")
        assert result.status == STATUS_IN_PROGRESS

    @pytest.mark.asyncio
    async def test_realtime_navigation_waits_for_dtmf_ready(self):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        provider = AndroidSimGatewayProvider(
            GatewayConfig(),
            audio_provider=FakeAudioProvider(),
        )
        commands: list[str] = []
        events = iter(
            [
                {"event_type": "dtmf_ready", "key": "1"},
                {
                    "event_type": "technical_unknown",
                    "reason": "no ASR text before timeout",
                },
            ]
        )

        def next_event(*args, **kwargs):
            event = next(events)
            if isinstance(event, BaseException):
                raise event
            return event

        with patch.object(
            provider,
            "_start_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_stop_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_next_realtime_event",
            new=AsyncMock(side_effect=next_event),
        ), patch.object(
            provider,
            "_run_api",
            side_effect=lambda command: commands.append(command) or "+OK",
        ), patch.object(
            provider,
            "stop_call",
            new=AsyncMock(),
        ):
            await provider._navigate_realtime("call-1", "1")

        assert any("uuid_send_dtmf call-1 1" in command for command in commands)
        assert provider._realtime_results["call-1"]["fault"] == ""

    @pytest.mark.asyncio
    async def test_realtime_navigation_stops_on_human_boundary(self):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        provider = AndroidSimGatewayProvider(
            GatewayConfig(),
            audio_provider=FakeAudioProvider(),
        )
        with patch.object(
            provider,
            "_start_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_stop_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_next_realtime_event",
            new=AsyncMock(return_value={"event_type": "human_boundary"}),
        ), patch.object(
            provider,
            "_run_api",
            return_value="+OK",
        ), patch.object(
            provider,
            "stop_call",
            new=AsyncMock(),
        ) as stop_call:
            await provider._navigate_realtime("call-1", "1")

        assert provider._realtime_results["call-1"]["fault"] == "human_boundary"
        stop_call.assert_awaited()

    @pytest.mark.asyncio
    async def test_timed_fallback_sends_second_key_for_known_1w2_path(self):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        provider = AndroidSimGatewayProvider(
            GatewayConfig(dtmf_fallback_delay_ms=1),
            audio_provider=FakeAudioProvider(),
        )
        commands: list[str] = []
        events = iter(
            [
                {"event_type": "dtmf_ready", "key": "1"},
                TimeoutError(),
                {"event_type": "unknown_boundary"},
            ]
        )

        def next_event(*args, **kwargs):
            event = next(events)
            if isinstance(event, BaseException):
                raise event
            return event

        with patch.object(
            provider,
            "_start_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_stop_realtime_stream",
            new=AsyncMock(),
        ), patch.object(
            provider,
            "_next_realtime_event",
            new=AsyncMock(side_effect=next_event),
        ), patch.object(
            provider,
            "_run_api",
            side_effect=lambda command: commands.append(command) or "+OK",
        ), patch.object(
            provider,
            "stop_call",
            new=AsyncMock(),
        ):
            await provider._navigate_realtime("call-fallback", "1w2")

        assert any("uuid_send_dtmf call-fallback 1" in c for c in commands)
        assert any("uuid_send_dtmf call-fallback 2" in c for c in commands)
        assert provider._realtime_results["call-fallback"]["fault"] == ""


class TestEslBodyExtraction:
    def test_strips_reply_headers(self):
        reply = "Content-Type: api/response\nContent-Length: 5\n\nhello"
        assert EslClient._extract_body(reply) == "hello"

    def test_passthrough_without_headers(self):
        assert EslClient._extract_body("+OK") == "+OK"

    def test_content_length_detection(self):
        head = b"Content-Type: api/response\nContent-Length: 12"
        assert EslClient._content_length(head) == 12

    def test_no_content_length(self):
        assert EslClient._content_length(b"+OK") is None

    def test_default_config_from_env(self):
        config = EslConfig.from_env({"FREESWITCH_ESL_PORT": "18021"})
        assert config.port == 18021
        assert config.host == "127.0.0.1"
