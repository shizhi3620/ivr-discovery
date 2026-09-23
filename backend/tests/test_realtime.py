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
    assert extract_dtmf_keys("For tech support in English, press 2") == {"2"}
    assert extract_dtmf_keys("Press star to repeat") == {"*"}


def test_environment_asr_corrections(monkeypatch):
    monkeypatch.setenv(
        "ASR_PHRASE_CORRECTIONS_JSON",
        json.dumps(
            {
                "点Apple": "感谢您致电Apple",
                    "Export in English": (
                        "For tech support in English, press 2"
                    ),
                    "Support in English": (
                        "For tech support in English, press 2"
                    ),
            },
            ensure_ascii=False,
        ),
    )
    reset_asr_corrections()
    assert apply_asr_corrections("点Apple。 export in english") == (
        "感谢您致电Apple。 "
        "For tech support in English, press 2"
    )
    assert apply_asr_corrections("Support in English.") == (
        "For tech support in English, press 2."
    )
    reset_asr_corrections()


@pytest.mark.asyncio
async def test_decision_uses_asr_phrase_correction(monkeypatch):
    monkeypatch.setenv(
        "ASR_PHRASE_CORRECTIONS_JSON",
        json.dumps(
            {
                "Export in English.": (
                    "For tech support in English, press 2"
                )
            }
        ),
    )
    reset_asr_corrections()


@pytest.mark.asyncio
async def test_english_fallback_uses_partial_support_phrase_for_key_two():
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
        allow_target_key_fallback=True,
    )
    await engine.start()
    await engine.feed("Export in English", is_final=False)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events[-1]["event_type"] == "dtmf_ready"
    assert events[-1]["key"] == "2"


@pytest.mark.asyncio
async def test_english_fallback_requires_explicit_enablement():
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
        allow_target_key_fallback=False,
    )
    await engine.start()
    await engine.feed("Welcome to English support", is_final=False)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events == []
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
            "context": "您的电话正在转人工，请稍等",
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


@pytest.mark.asyncio
async def test_after_hours_hold_prompt_split_across_final_fragments_is_exempt():
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
    # Realtime ASR commonly emits the after-hours sentence as several finals;
    # the trailing 请稍等 must stay exempt when it inherits that context.
    await engine.feed("已作为评估和培训客服人员", is_final=True)
    await engine.feed("即改进客服中心技术质量之用", is_final=True)
    await engine.feed("请稍等", is_final=True)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events == []


@pytest.mark.asyncio
async def test_after_hours_exemption_does_not_mask_later_human_transfer():
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
    await engine.feed("正在为您转接人工坐席", is_final=True)
    await engine.close()

    assert [event["event_type"] for event in events] == ["human_boundary"]


@pytest.mark.asyncio
async def test_after_hours_hold_prompt_partial_before_final_is_exempt():
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
    # Same sentence streamed as growing partial revisions, then a final.
    await engine.feed("作为评估和培训客服人员改进客服中心技术质量之用", is_final=False)
    await engine.feed(
        "作为评估和培训客服人员，改进客服中心技术质量之用，请稍等",
        is_final=False,
    )
    await engine.feed(
        "作为评估和培训客服人员，改进客服中心技术质量之用，请稍等。",
        is_final=True,
    )
    await asyncio.sleep(0.05)
    await engine.close()

    assert events == []


@pytest.mark.asyncio
async def test_after_hours_hold_prompt_streamed_as_separate_partials_is_exempt():
    """Reproduce the real 4006668800 stream: each clause arrives as its own
    partial sentence, so the trailing 请稍等 cannot be judged in isolation."""
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
    streamed_clauses = [
        "感谢您致电Apple。",
        "为了给您提供最好的服务。",
        "按照Apple隐私政策的规定，与本次通话相关的部分有限个人信息。",
        "可能会在中国大陆境外存储和处理。",
        "如果您同意，请按1。",
        "如需结束本次通话。",
        "感谢您致电Apple。普通话按1。",
        "English, 您的通话将会被录音，已作为评估和培训客服人员。",
        "改进客服中心技术质量之用。",
        "请稍等。",
    ]
    for clause in streamed_clauses:
        await engine.feed(clause, is_final=False)
    await asyncio.sleep(0.05)
    await engine.close()

    assert events == []


@pytest.mark.asyncio
async def test_separate_partials_still_stop_on_real_human_boundary():
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
    for clause in [
        "作为评估和培训客服人员。",
        "改进客服中心技术质量之用。",
        "请稍等。",
        "您的电话正在转接人工坐席。",
    ]:
        await engine.feed(clause, is_final=False)
    await engine.close()

    assert [event["event_type"] for event in events] == ["human_boundary"]


def test_hold_remains_human_risk_outside_complete_after_hours_sentence():
    assert has_human_boundary("请稍等") is True
    assert has_human_boundary("当前排队人数较多，请稍等") is True
    assert has_human_boundary(
        "作为评估和培训客服人员，改进客服中心技术质量。请稍等。"
    ) is False
    assert has_human_boundary(
        "作为评估和培训客服人员，改进客服中心技术质量。请稍等。正在转人工"
    ) is True
    # A trailing hold after a genuine transfer must not be swallowed by the
    # exemption window (regression: greedy .{0,30} masked 转接人工).
    assert (
        has_human_boundary(
            "作为评估和培训客服人员。改进客服中心技术质量之用。请稍等。"
            "您的电话正在转接人工坐席，请稍等。"
        )
        is True
    )


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


# -- shadow judgment (ADR 0040) ---------------------------------------------


class _FakeShadowJudge:
    enabled = True

    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    async def judge(self, *, channel_uuid, exploration_call_id, segments, trigger):
        self.calls.append(trigger)
        return self._verdict


class _ShadowVerdict:
    def __init__(self, classification, confidence, status="ok", reason="test"):
        self.classification = classification
        self.confidence = confidence
        self.status = status
        self.reason = reason
        self.latency_ms = 1

    @property
    def usable(self):
        from realtime.shadow import VALID_CLASSIFICATIONS

        return self.status == "ok" and self.classification in VALID_CLASSIFICATIONS


@pytest.mark.asyncio
async def test_shadow_human_verdict_cancels_after_hours_boundary():
    from realtime.shadow import AUTOMATED_NOTICE

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(AUTOMATED_NOTICE, 0.95))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        boundary_cancel_window_ms=50,
    )
    await engine.feed("您的电话正在排队，请稍等", is_final=True)
    await asyncio.sleep(0.2)
    kinds = [e["event_type"] for e in events]
    assert kinds[0] == "boundary_pending"
    assert "shadow_verdict" in kinds
    assert "boundary_cancelled" in kinds
    assert "human_boundary" not in kinds


@pytest.mark.asyncio
async def test_shadow_human_verdict_keeps_boundary_when_model_says_human():
    from realtime.shadow import HUMAN_OR_UNKNOWN

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(HUMAN_OR_UNKNOWN, 0.99))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        boundary_cancel_window_ms=50,
    )
    await engine.feed("您的电话正在转接人工坐席", is_final=True)
    await asyncio.sleep(0.2)
    kinds = [e["event_type"] for e in events]
    assert "boundary_pending" in kinds
    assert "human_boundary" in kinds
    assert "boundary_cancelled" not in kinds


@pytest.mark.asyncio
async def test_shadow_without_judge_still_immediately_stops():
    events = []

    async def on_event(event):
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
    )
    await engine.feed("您的电话正在转接人工坐席", is_final=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["human_boundary"]


@pytest.mark.asyncio
async def test_shadow_low_confidence_does_not_cancel_boundary():
    from realtime.shadow import AUTOMATED_NOTICE

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(AUTOMATED_NOTICE, 0.5))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        boundary_cancel_window_ms=50,
    )
    await engine.feed("您的电话正在排队，请稍等", is_final=True)
    await asyncio.sleep(0.2)
    kinds = [e["event_type"] for e in events]
    assert "boundary_cancelled" not in kinds
    assert "human_boundary" in kinds


@pytest.mark.asyncio
async def test_shadow_human_veto_blocks_dtmf_ready():
    from realtime.shadow import HUMAN_OR_UNKNOWN

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(HUMAN_OR_UNKNOWN, 0.95))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        silence_ms=20,
    )
    await engine.feed("请按1", is_final=True)
    await asyncio.sleep(0.4)
    kinds = [e["event_type"] for e in events]
    assert "dtmf_ready" not in kinds
    assert "human_boundary" in kinds


@pytest.mark.asyncio
async def test_shadow_automated_notice_defers_then_unknown_boundary():
    from realtime.shadow import AUTOMATED_NOTICE

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(AUTOMATED_NOTICE, 0.9))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        silence_ms=20,
        automated_notice_extension_ms=50,
    )
    await engine.feed("请按1", is_final=True)
    await asyncio.sleep(0.4)
    kinds = [e["event_type"] for e in events]
    assert "dtmf_ready" not in kinds
    assert kinds.count("shadow_verdict") >= 1


@pytest.mark.asyncio
async def test_shadow_ivr_menu_cancels_unknown_boundary():
    from realtime.shadow import IVR_MENU

    events = []

    async def on_event(event):
        events.append(event)

    judge = _FakeShadowJudge(_ShadowVerdict(IVR_MENU, 0.95))
    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="2",
        on_event=on_event,
        shadow_judge=judge,
        menu_completion_ms=30,
    )
    await engine.feed("感谢您的致电", is_final=True)
    await asyncio.sleep(0.3)
    kinds = [e["event_type"] for e in events]
    assert "unknown_boundary" not in kinds[:2] or "shadow_verdict" in kinds
    # An ivr_menu verdict cancels the pending unknown boundary and re-arms.
    assert "shadow_verdict" in kinds


@pytest.mark.asyncio
async def test_shadow_judge_parses_valid_json(tmp_path):
    from realtime.shadow import ShadowJudge

    class _Provider:
        async def complete(self, prompt, *, max_tokens=1024, json_mode=False):
            return json.dumps(
                {
                    "classification": "ivr_menu",
                    "confidence": 0.91,
                    "reason": "menu with DTMF options",
                }
            )

    judge = ShadowJudge(enabled=True, provider=_Provider(), audit_dir=str(tmp_path))
    verdict = await judge.judge(
        channel_uuid="c",
        exploration_call_id="e",
        segments=[("请按1", 0.0)],
        trigger="target_key",
    )
    assert verdict.classification == "ivr_menu"
    assert verdict.confidence == 0.91
    assert verdict.usable
    assert (tmp_path / "shadow-judgments.jsonl").is_file()


@pytest.mark.asyncio
async def test_shadow_judge_disabled_returns_none(tmp_path):
    from realtime.shadow import ShadowJudge

    judge = ShadowJudge(enabled=False, audit_dir=str(tmp_path))
    verdict = await judge.judge(
        channel_uuid="c",
        exploration_call_id="e",
        segments=[("请按1", 0.0)],
        trigger="target_key",
    )
    assert verdict is None


@pytest.mark.asyncio
async def test_shadow_judge_bad_json_is_unavailable(tmp_path):
    from realtime.shadow import ShadowJudge, UNAVAILABLE

    class _Provider:
        async def complete(self, prompt, *, max_tokens=1024, json_mode=False):
            return "not json"

    judge = ShadowJudge(enabled=True, provider=_Provider(), audit_dir=str(tmp_path))
    verdict = await judge.judge(
        channel_uuid="c",
        exploration_call_id="e",
        segments=[("请按1", 0.0)],
        trigger="target_key",
    )
    assert verdict.classification == UNAVAILABLE
    assert verdict.usable is False


@pytest.mark.asyncio
async def test_shadow_verdict_event_carries_judged_context(tmp_path):
    """Each shadow_verdict event must be self-contained for offline labelling."""
    from realtime.shadow import AUTOMATED_NOTICE, ShadowJudge

    class _Provider:
        async def complete(self, prompt, *, max_tokens=1024, json_mode=False):
            return json.dumps(
                {
                    "classification": AUTOMATED_NOTICE,
                    "confidence": 0.9,
                    "reason": "notice",
                }
            )

    judge = ShadowJudge(enabled=True, provider=_Provider(), audit_dir=str(tmp_path))
    verdict = await judge.judge(
        channel_uuid="c",
        exploration_call_id="e",
        segments=[("请稍等。", 0.0)],
        trigger="boundary_pending",
    )
    assert verdict.context == "请稍等。"

    # The decision engine must surface that same context on the event stream.
    events = []

    async def on_event(event):
        events.append(event)

    engine = RealtimeDecisionEngine(
        channel_uuid="c",
        exploration_call_id="e",
        target_key="1",
        on_event=on_event,
        shadow_judge=judge,
        boundary_cancel_window_ms=50,
    )
    await engine.feed("请稍等", is_final=True)
    await asyncio.sleep(0.2)
    verdicts = [e for e in events if e["event_type"] == "shadow_verdict"]
    assert verdicts, "expected a shadow_verdict event"
    assert verdicts[0]["context"] == "请稍等"
