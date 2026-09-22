"""Tests for the AI Provider boundary and its vendor adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import ai
from ai.anthropic_provider import AnthropicProvider
from ai.base import AICapabilities
from ai.deepseek_provider import DeepSeekProvider


class TestAIProviderSelection:
    def setup_method(self):
        ai.reset_ai_providers()

    def teardown_method(self):
        ai.reset_ai_providers()

    def test_default_is_deepseek(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = ai.get_ai_provider()
        assert provider.name == "deepseek"

    def test_env_override(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}, clear=True):
            provider = ai.get_ai_provider()
        assert provider.name == "anthropic"

    def test_explicit_argument_beats_env(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}, clear=True):
            provider = ai.get_ai_provider("deepseek")
        assert provider.name == "deepseek"

    def test_instances_are_cached(self):
        assert ai.get_ai_provider("deepseek") is ai.get_ai_provider("deepseek")

    def test_claude_alias_is_supported(self):
        assert ai.get_ai_provider("claude").name == "anthropic"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown AI provider"):
            ai.get_ai_provider("openai")


class TestDeepSeekProvider:
    def test_capabilities(self):
        assert DeepSeekProvider(api_key="k").capabilities == AICapabilities(json_mode=True)

    def test_configuration_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = DeepSeekProvider(api_key="k")
        assert provider.model == "deepseek-flash"
        assert provider.endpoint == "https://api.deepseek.com/chat/completions"

    @pytest.mark.asyncio
    async def test_complete_sends_openai_compatible_payload(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "hello"}}]},
        )
        client = AsyncMock()
        client.post.return_value = response
        provider = DeepSeekProvider(api_key="secret", client=client)

        text = await provider.complete("prompt", json_mode=True)

        assert text == "hello"
        _, kwargs = client.post.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer secret"}
        assert kwargs["json"]["model"] == "deepseek-flash"
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "prompt"}]
        client.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_requires_api_key(self):
        provider = DeepSeekProvider(api_key="")
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            await provider.complete("prompt")

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(500, request=request, text="server error")
        client = AsyncMock()
        client.post.return_value = response
        provider = DeepSeekProvider(api_key="secret", client=client)

        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete("prompt")

    @pytest.mark.asyncio
    async def test_malformed_response_raises(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(200, request=request, json={"choices": []})
        client = AsyncMock()
        client.post.return_value = response
        provider = DeepSeekProvider(api_key="secret", client=client)

        with pytest.raises(RuntimeError, match="message content"):
            await provider.complete("prompt")


class TestAnthropicProvider:
    def test_capabilities(self):
        assert AnthropicProvider(api_key="k").capabilities == AICapabilities(json_mode=False)

    @pytest.mark.asyncio
    async def test_complete_extracts_text(self):
        client = MagicMock()
        client.messages.create = AsyncMock()
        block = MagicMock()
        block.text = "  result  "
        response = MagicMock()
        response.content = [block]
        client.messages.create.return_value = response
        provider = AnthropicProvider(api_key="k", model="model-x", client=client)

        text = await provider.complete("prompt", max_tokens=123)

        assert text == "result"
        _, kwargs = client.messages.create.call_args
        assert kwargs == {
            "model": "model-x",
            "max_tokens": 123,
            "messages": [{"role": "user", "content": "prompt"}],
        }

    @pytest.mark.asyncio
    async def test_json_mode_is_ignored_gracefully(self):
        client = MagicMock()
        client.messages.create = AsyncMock()
        block = MagicMock()
        block.text = "{}"
        response = MagicMock()
        response.content = [block]
        client.messages.create.return_value = response
        provider = AnthropicProvider(api_key="k", client=client)

        assert await provider.complete("prompt", json_mode=True) == "{}"

    @pytest.mark.asyncio
    async def test_malformed_response_raises(self):
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=MagicMock(content=[]))
        provider = AnthropicProvider(api_key="k", client=client)

        with pytest.raises(RuntimeError, match="text content"):
            await provider.complete("prompt")
