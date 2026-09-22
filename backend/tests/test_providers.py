"""Tests for the telephony Provider boundary (docs/adr/0004)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import providers
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
    def test_advertises_no_transcript(self):
        """The SIM gateway carries audio only; ASR is a separate stage."""
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider()
        assert provider.capabilities.transcript is False
        assert provider.capabilities.dtmf is True

    @pytest.mark.asyncio
    async def test_speech_task_is_rejected(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider()
        with pytest.raises(NotImplementedError):
            await provider.place_call("10010", voice_option="billing")

    @pytest.mark.asyncio
    async def test_place_call_originates_with_gsm_header(self):
        from providers.android_sim_provider import (
            AndroidSimGatewayProvider,
            GatewayConfig,
        )

        config = GatewayConfig(
            domain="192.168.10.112", gateway_extension="gateway1"
        )
        provider = AndroidSimGatewayProvider(config)
        captured: list[str] = []

        def fake_run(command: str) -> str:
            captured.append(command)
            return "+OK"

        with patch.object(provider, "_run_api", side_effect=fake_run), patch(
            "providers.android_sim_provider.asyncio.create_task"
        ):
            call_id = await provider.place_call("10010")

        originate = next(c for c in captured if "originate" in c)
        assert "sip_h_X-GSM-Destination=10010" in originate
        assert "user/gateway1@192.168.10.112" in originate
        assert call_id in originate

    @pytest.mark.asyncio
    async def test_get_call_reports_completed_when_channel_gone(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider()
        with patch.object(provider, "_run_api", return_value='{"rows":[]}'):
            result = await provider.get_call("gone")
        assert result.status == STATUS_COMPLETED
        assert result.transcript == ""

    @pytest.mark.asyncio
    async def test_get_call_reports_in_progress_while_channel_exists(self):
        from providers.android_sim_provider import AndroidSimGatewayProvider

        provider = AndroidSimGatewayProvider()
        payload = '{"rows":[{"uuid":"live-call"}]}'
        with patch.object(provider, "_run_api", return_value=payload):
            result = await provider.get_call("live-call")
        assert result.status == STATUS_IN_PROGRESS


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
