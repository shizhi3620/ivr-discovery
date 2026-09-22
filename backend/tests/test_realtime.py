"""Tests for realtime decision, audio conversion and Tencent URI signing."""

from __future__ import annotations

import asyncio
import json
from array import array
from urllib.parse import parse_qs, urlparse

import pytest

from realtime.audio import decode_to_mono_pcm, resample_pcm16_mono
from realtime.corrections import apply_asr_corrections, reset_asr_corrections
from realtime.decision import (
    RealtimeDecisionEngine,
    extract_dtmf_keys,
    has_human_boundary,
)
from realtime.real_call_probe import RealCallProbe
from realtime.tencent_asr import build_realtime_uri


def test_extract_dtmf_keys_chinese_and_english():
    assert extract_dtmf_keys("如果您同意，请按1；For English, press 2") == {"1", "2"}
    assert extract_dtmf_keys("普通话请按一") == {"1"}
    assert extract_dtmf_keys("For technical support in English, press two") == {"2"}
    assert extract_dtmf_keys("Press star to repeat") == {"*"}


def test_environment_asr_corrections(monkeypatch):
    monkeypatch.setenv(
        "ASR_PHRASE_CORRECTIONS_JSON",
        json.dumps(
            {
                "点Apple": "感谢致电Apple",
                    "Export in English": (
                        "For technical support in English, press two"
                    ),
                    "Support in English": (
                        "For technical support in English, press two"
                    ),
            },
            ensure_ascii=False,
        ),
    )
    reset_asr_corrections()
    assert apply_asr_corrections("点Apple。 export in english") == (
        "感谢致电Apple。 For technical support in English, press two"
    )
    assert apply_asr_corrections("Support in English.") == (
        "For technical support in English, press two."
    )
    reset_asr_corrections()


@pytest.mark.asyncio
async def test_decision_uses_asr_phrase_correction(monkeypatch):
    monkeypatch.setenv(
        "ASR_PHRASE_CORRECTIONS_JSON",
        json.dumps(
            {
                "Export in English.": (
                    "For technical support in English, press two."
                )
            }
        ),
    )
    reset_asr_corrections()
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="2",
        on_event=on_event,
        silence_ms=20,
        no_speech_timeout_ms=1000,
    )
    await engine.start()
    await engine.feed("Export in English.", is_final=True)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events[-1]["event_type"] == "dtmf_ready"
    assert events[-1]["key"] == "2"
    reset_asr_corrections()


@pytest.mark.asyncio
async def test_decision_emits_dtmf_ready_after_silence():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="1",
        on_event=on_event,
        silence_ms=20,
        no_speech_timeout_ms=1000,
    )
    await engine.start()
    await engine.feed("如果您同意，请按1", is_final=True)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events[-1]["event_type"] == "dtmf_ready"
    assert events[-1]["key"] == "1"


@pytest.mark.asyncio
async def test_decision_stops_on_human_boundary():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="1",
        on_event=on_event,
    )
    await engine.start()
    await engine.feed("您的电话正在转人工，请稍等", is_final=True)
    await engine.close()

    assert events == [
        {
            "channel_uuid": "channel",
            "exploration_call_id": "call",
            "event_type": "human_boundary",
            "text": "您的电话正在转人工，请稍等",
            "reason": "strong human-service keyword",
        }
    ]


@pytest.mark.asyncio
async def test_after_hours_hold_prompt_is_not_human_boundary():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="1",
        on_event=on_event,
        menu_completion_ms=1000,
        no_speech_timeout_ms=1000,
    )
    await engine.start()
    await engine.feed(
        "作为评估和培训客服人员，改进客服中心技术质量。请稍等。",
        is_final=True,
    )
    await asyncio.sleep(0.05)
    await engine.close()

    assert events == []


def test_hold_remains_human_risk_outside_complete_after_hours_sentence():
    assert has_human_boundary("请稍等") is True
    assert has_human_boundary("当前排队人数较多，请稍等") is True
    assert has_human_boundary(
        "作为评估和培训客服人员，改进客服中心技术质量。请稍等。"
    ) is False
    assert has_human_boundary(
        "作为评估和培训客服人员，改进客服中心技术质量。请稍等。正在转人工"
    ) is True


@pytest.mark.asyncio
async def test_partial_text_does_not_end_the_prompt():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="1",
        on_event=on_event,
        silence_ms=20,
        no_speech_timeout_ms=1000,
    )
    await engine.start()
    await engine.feed("Apple", is_final=False)
    await asyncio.sleep(0.05)
    assert events == []
    await engine.close()


@pytest.mark.asyncio
async def test_multiple_final_sentences_can_form_one_menu():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="channel",
        exploration_call_id="call",
        target_key="1",
        on_event=on_event,
        silence_ms=20,
        menu_completion_ms=200,
        no_speech_timeout_ms=1000,
    )
    await engine.start()
    await engine.feed("这是一段隐私说明。", is_final=True)
    await asyncio.sleep(0.05)
    assert events == []
    await engine.feed("如果您同意，请按1。", is_final=True)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events[-1]["event_type"] == "dtmf_ready"


def test_decode_l16be_stereo_selects_remote_channel():
    left = array("h", [1000, -1000])
    right = array("h", [2000, -2000])
    interleaved = array("h")
    for left_sample, right_sample in zip(left, right):
        interleaved.append(left_sample)
        interleaved.append(right_sample)
    interleaved.byteswap()

    decoded = decode_to_mono_pcm(
        interleaved.tobytes(),
        encoding="l16be",
        channels=2,
        remote_channel=1,
    )
    assert array("h", decoded).tolist() == [2000, -2000]


def test_resample_8k_to_16k_duplicates_samples():
    source = array("h", [100, -200, 300]).tobytes()
    resampled = resample_pcm16_mono(
        source,
        source_rate=8000,
        target_rate=16000,
    )
    assert array("h", resampled).tolist() == [100, 100, -200, -200, 300, 300]


def test_build_realtime_uri_contains_signed_params():
    uri = build_realtime_uri(
        app_id="1251925547",
        secret_id="secret-id",
        secret_key="secret-key",
        engine_model_type="8k_zh",
        voice_id="voice-id",
        timestamp=1700000000,
    )
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "wss"
    assert parsed.netloc == "asr.cloud.tencent.com"
    assert parsed.path == "/asr/v2/1251925547"
    assert params["engine_model_type"] == ["8k_zh"]
    assert params["voice_id"] == ["voice-id"]
    assert "signature" in params


@pytest.mark.asyncio
async def test_real_call_probe_selects_only_matching_terminal_event():
    probe = RealCallProbe(
        provider=object(),
        relay_base="ws://127.0.0.1:18031",
        target_key="1",
        encoding="s16le",
        mix_type="mono",
        gain=1.0,
        silence_ms=800,
        menu_completion_ms=8000,
        no_speech_timeout_ms=30000,
        observation_menu_completion_ms=12000,
        observation_no_speech_timeout_ms=15000,
        observation_timeout_ms=20000,
    )
    events: asyncio.Queue[dict] = asyncio.Queue()
    await events.put(
        {
            "channel_uuid": "other",
            "event_type": "dtmf_ready",
            "key": "9",
        }
    )
    await events.put(
        {
            "channel_uuid": "channel",
            "event_type": "partial",
            "text": "如果您同意",
        }
    )
    await events.put(
        {
            "channel_uuid": "channel",
            "event_type": "dtmf_ready",
            "key": "1",
        }
    )

    terminal = await probe._next_terminal_event(events, "channel")

    assert terminal["event_type"] == "dtmf_ready"
    assert terminal["key"] == "1"
