"""Tests for transcript_parser — deduplication, filtering, and AI response parsing."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import transcript_parser
from transcript_parser import (
    _deduplicate_options,
    _normalize_label,
    parse_transcript,
)


# --- Unit tests for helper functions (no mocking needed) ---


class TestNormalizeLabel:
    def test_strips_whitespace(self):
        assert _normalize_label("  Billing  ") == "billing"

    def test_lowercases(self):
        assert _normalize_label("Technical Support") == "technical support"

    def test_empty(self):
        assert _normalize_label("") == ""


class TestDeduplicateOptions:
    def test_removes_navigation_options(self):
        options = [
            {"dtmf_key": "1", "label": "Billing"},
            {"dtmf_key": "9", "label": "Repeat"},
            {"dtmf_key": "0", "label": "Main Menu"},
        ]
        result = _deduplicate_options(options)
        assert len(result) == 1
        assert result[0]["label"] == "Billing"

    def test_prefers_dtmf_over_voice(self):
        options = [
            {"dtmf_key": "say1", "label": "Billing"},
            {"dtmf_key": "1", "label": "Billing"},
        ]
        result = _deduplicate_options(options)
        assert len(result) == 1
        assert result[0]["dtmf_key"] == "1"

    def test_keeps_voice_if_no_dtmf(self):
        options = [
            {"dtmf_key": "say1", "label": "Track a package"},
        ]
        result = _deduplicate_options(options)
        assert len(result) == 1
        assert result[0]["dtmf_key"] == "say1"

    def test_case_insensitive_dedup(self):
        options = [
            {"dtmf_key": "1", "label": "billing"},
            {"dtmf_key": "say1", "label": "Billing"},
        ]
        result = _deduplicate_options(options)
        assert len(result) == 1
        assert result[0]["dtmf_key"] == "1"

    def test_empty_list(self):
        assert _deduplicate_options([]) == []

    def test_all_navigation(self):
        options = [
            {"dtmf_key": "9", "label": "Repeat"},
            {"dtmf_key": "0", "label": "Go back"},
            {"dtmf_key": "*", "label": "Start over"},
        ]
        result = _deduplicate_options(options)
        assert len(result) == 0


# --- Integration tests with a mocked AI Provider ---


def make_ai_provider(content: dict | str) -> AsyncMock:
    """Build a mock AI Provider returning JSON text."""
    provider = AsyncMock()
    provider.capabilities.json_mode = True
    provider.complete.return_value = (
        content if isinstance(content, str) else json.dumps(content)
    )
    return provider


@pytest.mark.asyncio
class TestParseTranscript:
    async def test_empty_transcript(self):
        result = await parse_transcript("")
        assert result == {"prompt_text": "", "options": []}

    async def test_short_transcript(self):
        result = await parse_transcript("hi")
        assert result == {"prompt_text": "", "options": []}

    async def test_parses_dtmf_menu(self):
        provider = make_ai_provider({
            "prompt_text": "Welcome to USPS",
            "human_transfer": False,
            "options": [
                {"dtmf_key": "1", "label": "Track a package"},
                {"dtmf_key": "2", "label": "Buy stamps"},
                {"dtmf_key": "3", "label": "Schedule pickup"},
            ],
        })
        result = await parse_transcript(
            "user: Press 1 for tracking, 2 for stamps, 3 for pickup",
            provider=provider,
        )
        assert result["prompt_text"] == "Welcome to USPS"
        assert len(result["options"]) == 3
        assert result["options"][0]["dtmf_key"] == "1"
        assert result["human_transfer"] is False

    async def test_human_transfer_detected(self):
        provider = make_ai_provider({
            "prompt_text": "Connecting you to a representative",
            "human_transfer": True,
            "options": [],
        })
        result = await parse_transcript(
            "user: Please hold while I connect you to a representative",
            provider=provider,
        )
        assert result["human_transfer"] is True
        assert result["options"] == []

    async def test_filters_navigation_options(self):
        provider = make_ai_provider({
            "prompt_text": "Main menu",
            "human_transfer": False,
            "options": [
                {"dtmf_key": "1", "label": "Billing"},
                {"dtmf_key": "9", "label": "Repeat"},
                {"dtmf_key": "*", "label": "Main Menu"},
            ],
        })
        result = await parse_transcript(
            "user: Press 1 for billing, 9 to repeat, star for main menu",
            provider=provider,
        )
        assert len(result["options"]) == 1
        assert result["options"][0]["label"] == "Billing"

    async def test_handles_markdown_code_block(self):
        content = {
            "prompt_text": "Welcome",
            "human_transfer": False,
            "options": [{"dtmf_key": "1", "label": "Help"}],
        }
        provider = make_ai_provider(f"```json\n{json.dumps(content)}\n```")
        result = await parse_transcript("user: Press 1 for help", provider=provider)
        assert len(result["options"]) == 1

    async def test_handles_invalid_json(self):
        provider = make_ai_provider("this is not json")
        result = await parse_transcript(
            "user: some transcript text here that is long enough",
            provider=provider,
        )
        assert result["options"] == []
        assert len(result["prompt_text"]) > 0  # Falls back to transcript[:200]

    async def test_handles_api_error(self):
        provider = AsyncMock()
        provider.capabilities.json_mode = True
        provider.complete.side_effect = Exception("API down")

        result = await parse_transcript("user: Press 1 for billing", provider=provider)
        assert result == {"prompt_text": "", "options": []}
