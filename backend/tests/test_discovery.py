"""Tests for discovery logic — fingerprinting, depth, cycle detection."""

from __future__ import annotations

from datetime import datetime

import pytest

import database as db
from discovery import (
    options_fingerprint,
    get_node_depth,
    is_cycle,
    run_discovery,
    _core_label,
)
from discovery_windows import APP_TIMEZONE, get_current_window
from models import Node, NodeStatus, Session, Target


class TestOptionsFingerprint:
    def test_basic(self):
        options = [
            {"dtmf_key": "1", "label": "Billing"},
            {"dtmf_key": "2", "label": "Support"},
        ]
        fp = options_fingerprint(options)
        assert fp == frozenset({"billing", "support"})

    def test_strips_parenthetical_descriptions(self):
        """Labels with parenthetical descriptions should fingerprint to core label."""
        fp1 = options_fingerprint([
            {"dtmf_key": "1", "label": "Package (track status, delivery issues)"},
            {"dtmf_key": "2", "label": "Mail (daily services, pickup, change of address)"},
        ])
        fp2 = options_fingerprint([
            {"dtmf_key": "1", "label": "Package (track, re-delivery, service requests)"},
            {"dtmf_key": "2", "label": "Mail (delivery, hold mail, service requests)"},
        ])
        assert fp1 == fp2
        assert fp1 == frozenset({"package", "mail"})

    def test_case_insensitive(self):
        fp1 = options_fingerprint([{"dtmf_key": "1", "label": "Billing"}])
        fp2 = options_fingerprint([{"dtmf_key": "1", "label": "billing"}])
        assert fp1 == fp2

    def test_order_independent(self):
        fp1 = options_fingerprint([
            {"dtmf_key": "1", "label": "Billing"},
            {"dtmf_key": "2", "label": "Support"},
        ])
        fp2 = options_fingerprint([
            {"dtmf_key": "2", "label": "Support"},
            {"dtmf_key": "1", "label": "Billing"},
        ])
        assert fp1 == fp2

    def test_strips_whitespace(self):
        fp1 = options_fingerprint([{"dtmf_key": "1", "label": "  Billing  "}])
        fp2 = options_fingerprint([{"dtmf_key": "1", "label": "Billing"}])
        assert fp1 == fp2

    def test_different_menus_differ(self):
        fp1 = options_fingerprint([{"dtmf_key": "1", "label": "Billing"}])
        fp2 = options_fingerprint([{"dtmf_key": "1", "label": "Support"}])
        assert fp1 != fp2

    def test_same_labels_different_keys(self):
        """Same labels with different DTMF keys should still match (fingerprint is label-based)."""
        fp1 = options_fingerprint([{"dtmf_key": "1", "label": "Billing"}])
        fp2 = options_fingerprint([{"dtmf_key": "5", "label": "Billing"}])
        assert fp1 == fp2

    def test_empty(self):
        fp = options_fingerprint([])
        assert fp == frozenset()


class TestGetNodeDepth:
    def test_root_is_zero(self):
        root = Node(id="root", session_id="s1")
        nodes_by_id = {"root": root}
        assert get_node_depth(root, nodes_by_id) == 0

    def test_child_is_one(self):
        root = Node(id="root", session_id="s1")
        child = Node(id="child", session_id="s1", parent_id="root")
        nodes_by_id = {"root": root, "child": child}
        assert get_node_depth(child, nodes_by_id) == 1

    def test_grandchild_is_two(self):
        root = Node(id="root", session_id="s1")
        child = Node(id="child", session_id="s1", parent_id="root")
        grandchild = Node(id="gc", session_id="s1", parent_id="child")
        nodes_by_id = {"root": root, "child": child, "gc": grandchild}
        assert get_node_depth(grandchild, nodes_by_id) == 2

    def test_missing_parent_stops(self):
        """If parent_id references a node not in the dict, stop counting."""
        node = Node(id="orphan", session_id="s1", parent_id="missing")
        nodes_by_id = {"orphan": node}
        assert get_node_depth(node, nodes_by_id) == 0


class TestCoreLabel:
    def test_strips_parenthetical(self):
        assert _core_label("Package (track status, delivery issues)") == "package"

    def test_multiple_parentheticals(self):
        assert _core_label("Mail (daily services, pickup) (extra)") == "mail"

    def test_no_parenthetical(self):
        assert _core_label("Billing") == "billing"

    def test_only_parenthetical(self):
        assert _core_label("(something)") == "(something)"


class TestIsCycle:
    def test_exact_match(self):
        seen = {frozenset({"billing", "support"})}
        assert is_cycle(frozenset({"billing", "support"}), seen)

    def test_no_match(self):
        seen = {frozenset({"billing", "support"})}
        assert not is_cycle(frozenset({"shipping", "returns"}), seen)

    def test_fuzzy_match_usps_scenario(self):
        """Simulates the USPS bug: same menu parsed with different parenthetical descriptions."""
        seen = {frozenset({"package", "mail", "tools", "stamps", "alerts", "other"})}
        # The model parsed the same menu with only 4 options this time
        fp = frozenset({"package", "mail", "tools", "stamps"})
        # 4/6 overlap = 0.67 > 0.6 threshold
        assert is_cycle(fp, seen)

    def test_below_threshold(self):
        seen = {frozenset({"billing", "support", "shipping", "returns", "account"})}
        fp = frozenset({"billing", "complaints", "orders", "tracking", "help"})
        # Only 1/9 overlap = 0.11 < 0.6
        assert not is_cycle(fp, seen)

    def test_empty_seen(self):
        assert not is_cycle(frozenset({"billing"}), set())

    def test_empty_fingerprint(self):
        seen = {frozenset({"billing"})}
        assert not is_cycle(frozenset(), seen)


@pytest.mark.asyncio
async def test_run_discovery_resumes_existing_pending_node():
    from unittest.mock import AsyncMock, MagicMock
    from providers.base import CallResult, ProviderCapabilities, STATUS_COMPLETED

    session = Session(phone_number="4006668800")
    await db.create_session(session)
    pending = Node(
        session_id=session.id,
        dtmf_path="1",
        status=NodeStatus.PENDING,
    )
    await db.create_node(pending)

    provider = MagicMock()
    provider.name = "fake"
    provider.capabilities = ProviderCapabilities(
        transcript=True,
        speech=True,
        dtmf=True,
        realtime_dtmf=True,
    )
    provider.place_call = AsyncMock(return_value="call-resume")
    provider.wait_for_call = AsyncMock(
        return_value=CallResult(
            "call-resume",
            STATUS_COMPLETED,
            transcript="Resumed node returned a complete test transcript.",
        )
    )
    ws = MagicMock()
    ws.send_json = AsyncMock()

    await run_discovery(
        ws,
        session.phone_number,
        session,
        provider,
        resume=True,
    )

    nodes = await db.get_nodes_by_session(session.id)
    assert [node.id for node in nodes] == [pending.id]
    provider.place_call.assert_awaited_once()




class TestExploreNodeProviderBoundary:
    """explore_node must delegate telephony to the injected Provider."""

    @pytest.mark.asyncio
    async def test_fails_fast_without_transcript_capability(self):
        """A provider with no ASR must not place a real call."""
        from unittest.mock import AsyncMock, MagicMock
        from discovery import explore_node
        from models import Node, Session
        from providers.base import CallResult, ProviderCapabilities, STATUS_COMPLETED

        provider = MagicMock()
        provider.name = "fake_no_asr"
        provider.capabilities = ProviderCapabilities(transcript=False, dtmf=True)
        provider.place_call = AsyncMock()

        ws = MagicMock()
        ws.send_json = AsyncMock()
        session = Session(phone_number="10010")
        await db.create_session(session)
        node = Node(session_id=session.id)
        await db.create_node(node)

        options = await explore_node(ws, session, node, provider)

        assert options == []
        provider.place_call.assert_not_awaited()

        stored = await db.get_node(node.id)
        assert stored.status == NodeStatus.FAILED
        assert "no transcript capability" in stored.prompt_text

    @pytest.mark.asyncio
    async def test_uses_provider_result_and_parses_transcript(self):
        """With a transcript-capable provider, the parsed options flow through."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from discovery import explore_node
        from models import Node, Session
        from providers.base import CallResult, ProviderCapabilities, STATUS_COMPLETED

        provider = MagicMock()
        provider.name = "fake_asr"
        provider.capabilities = ProviderCapabilities(transcript=True, speech=True, dtmf=True)
        provider.place_call = AsyncMock(return_value="call-1")
        provider.wait_for_call = AsyncMock(
            return_value=CallResult(
                "call-1",
                STATUS_COMPLETED,
                transcript="Press 1 for billing, press 2 for support.",
                cost=0.02,
            )
        )

        ws = MagicMock()
        ws.send_json = AsyncMock()
        session = Session(phone_number="+18002758777")
        await db.create_session(session)
        node = Node(session_id=session.id)
        await db.create_node(node)

        parsed = {
            "prompt_text": "Main menu",
            "options": [{"dtmf_key": "1", "label": "Billing"}],
        }
        with patch("discovery.transcript_parser.parse_transcript", new=AsyncMock(return_value=parsed)):
            options = await explore_node(ws, session, node, provider)

        assert options == parsed["options"]
        provider.place_call.assert_awaited_once()
        stored = await db.get_node(node.id)
        assert stored.status == NodeStatus.COMPLETED
        assert stored.cost == 0.02
        assert stored.transcript == "Press 1 for billing, press 2 for support."

    @pytest.mark.asyncio
    async def test_human_transfer_stops_branch_expansion(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from discovery import explore_node
        from models import Node, Session
        from providers.base import CallResult, ProviderCapabilities, STATUS_COMPLETED

        provider = MagicMock()
        provider.name = "fake_asr"
        provider.capabilities = ProviderCapabilities(transcript=True, speech=True, dtmf=True)
        provider.place_call = AsyncMock(return_value="call-1")
        provider.wait_for_call = AsyncMock(
            return_value=CallResult(
                "call-1",
                STATUS_COMPLETED,
                transcript="Please hold while we connect you to an agent.",
            )
        )

        ws = MagicMock()
        ws.send_json = AsyncMock()
        session = Session(phone_number="4006668800")
        await db.create_session(session)
        node = Node(session_id=session.id)
        await db.create_node(node)

        parsed = {
            "prompt_text": "Please hold while we connect you to an agent.",
            "human_transfer": True,
            "options": [{"dtmf_key": "1", "label": "This must be ignored"}],
        }
        with patch(
            "discovery.transcript_parser.parse_transcript",
            new=AsyncMock(return_value=parsed),
        ):
            options = await explore_node(ws, session, node, provider)

        assert options == []
        stored = await db.get_node(node.id)
        assert stored.status == NodeStatus.COMPLETED
        assert stored.prompt_text.startswith("(human/queue)")

    @pytest.mark.asyncio
    async def test_budget_gate_blocks_before_provider_call(self):
        from unittest.mock import AsyncMock, MagicMock
        from discovery import explore_node
        from providers.base import ProviderCapabilities

        target = Target(phone_number="4006668800")
        await db.create_target(target)
        window = await get_current_window(
            target,
            now=datetime.now(APP_TIMEZONE),
        )
        session = Session(
            target_id=target.id,
            discovery_window_id=window.id,
            phone_number=target.phone_number,
        )
        await db.create_session(session)
        await db.update_discovery_window(window.id, budget_limit=1)
        await db.record_call_attempt(
            target_id=target.id,
            discovery_window_id=window.id,
            session_id=session.id,
            external_call_id="already-used",
        )
        node = Node(session_id=session.id)
        await db.create_node(node)

        provider = MagicMock()
        provider.name = "fake"
        provider.capabilities = ProviderCapabilities(
            transcript=True,
            speech=True,
            dtmf=True,
        )
        provider.place_call = AsyncMock()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        options = await explore_node(ws, session, node, provider)

        assert options == []
        provider.place_call.assert_not_awaited()
        stored = await db.get_node(node.id)
        assert stored.status == NodeStatus.FAILED
        assert "budget exhausted" in stored.prompt_text

    @pytest.mark.asyncio
    async def test_branch_call_requires_realtime_dtmf_capability(self):
        from unittest.mock import AsyncMock, MagicMock
        from discovery import explore_node
        from providers.base import ProviderCapabilities

        session = Session(phone_number="4006668800")
        await db.create_session(session)
        node = Node(
            session_id=session.id,
            parent_id="parent",
            dtmf_path="1",
            status=NodeStatus.PENDING,
        )
        await db.create_node(node)
        provider = MagicMock()
        provider.name = "fake"
        provider.capabilities = ProviderCapabilities(
            transcript=True,
            speech=True,
            dtmf=True,
            realtime_dtmf=False,
        )
        provider.place_call = AsyncMock()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        options = await explore_node(ws, session, node, provider)

        assert options == []
        provider.place_call.assert_not_awaited()
        stored = await db.get_node(node.id)
        assert stored.status == NodeStatus.FAILED
        assert "Realtime DTMF" in stored.prompt_text

    @pytest.mark.asyncio
    async def test_successful_origination_records_counted_call(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from discovery import explore_node
        from providers.base import CallResult, ProviderCapabilities, STATUS_COMPLETED

        target = Target(phone_number="4006668800")
        await db.create_target(target)
        window = await get_current_window(
            target,
            now=datetime.now(APP_TIMEZONE),
        )
        session = Session(
            target_id=target.id,
            discovery_window_id=window.id,
            phone_number=target.phone_number,
        )
        await db.create_session(session)
        node = Node(session_id=session.id)
        await db.create_node(node)

        provider = MagicMock()
        provider.name = "fake"
        provider.capabilities = ProviderCapabilities(
            transcript=True,
            speech=True,
            dtmf=True,
        )
        provider.place_call = AsyncMock(return_value="call-1")
        provider.wait_for_call = AsyncMock(
            return_value=CallResult(
                "call-1",
                STATUS_COMPLETED,
                transcript="Press 1 for billing, press 2 for support.",
            )
        )
        ws = MagicMock()
        ws.send_json = AsyncMock()
        parsed = {
            "prompt_text": "Main menu",
            "options": [{"dtmf_key": "1", "label": "Billing"}],
        }
        with patch(
            "discovery.transcript_parser.parse_transcript",
            new=AsyncMock(return_value=parsed),
        ):
            await explore_node(ws, session, node, provider)

        summary = await db.get_budget_summary(target.id, window.id)
        assert summary["window_used"] == 1
        assert summary["target_used"] == 1
