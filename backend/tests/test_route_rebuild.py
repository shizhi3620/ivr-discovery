"""Tests for rebuilding one route's tree from another route's evidence."""

from __future__ import annotations

import pytest

import database as db
from models import Edge, Node, NodeStatus, Session, SessionStatus
from route_rebuild import NodeOverride, rebuild_session_tree


async def _seed_source_session() -> Session:
    session = Session(
        id="source-session",
        phone_number="4006668800",
        status=SessionStatus.COMPLETED,
    )
    await db.create_session(session)
    root = Node(
        id="src-root",
        session_id=session.id,
        dtmf_path="",
        prompt_text="privacy menu",
        status=NodeStatus.COMPLETED,
        transcript="privacy transcript",
        call_id="call-root",
    )
    language = Node(
        id="src-lang",
        session_id=session.id,
        parent_id=root.id,
        dtmf_path="1",
        prompt_text="language menu",
        status=NodeStatus.COMPLETED,
        realtime_verified=True,
        transcript="language transcript",
        call_id="call-lang",
    )
    mandarin = Node(
        id="src-mandarin",
        session_id=session.id,
        parent_id=language.id,
        dtmf_path="1w1",
        prompt_text="after hours notice",
        status=NodeStatus.FAILED,
        realtime_verified=False,
    )
    english = Node(
        id="src-english",
        session_id=session.id,
        parent_id=language.id,
        dtmf_path="1w2",
        prompt_text="english recorded notice",
        status=NodeStatus.COMPLETED,
        realtime_verified=True,
    )
    for node in (root, language, mandarin, english):
        await db.create_node(node)
    for edge in (
        Edge(from_node_id=root.id, to_node_id=language.id, dtmf_key="1", label="agree"),
        Edge(from_node_id=language.id, to_node_id=mandarin.id, dtmf_key="1", label="mandarin"),
        Edge(from_node_id=language.id, to_node_id=english.id, dtmf_key="2", label="english"),
    ):
        await db.create_edge(edge)
    return session


async def _seed_target_session() -> Session:
    session = Session(
        id="target-session",
        phone_number="4006668800",
        status=SessionStatus.COMPLETED,
    )
    await db.create_session(session)
    stale = Node(
        id="stale-root",
        session_id=session.id,
        dtmf_path="",
        prompt_text="stale parser artefact",
        status=NodeStatus.COMPLETED,
    )
    await db.create_node(stale)
    return session


@pytest.mark.asyncio
async def test_rebuild_copies_tree_and_applies_override():
    source = await _seed_source_session()
    target = await _seed_target_session()

    result = await rebuild_session_tree(
        target_session_id=target.id,
        source_session_id=source.id,
        overrides={
            "1w1": NodeOverride(
                prompt_text="(human/queue) please hold",
                status=NodeStatus.COMPLETED.value,
                realtime_verified=True,
                transcript="real human call",
                call_id="human-call",
            )
        },
    )

    assert result.deleted_node_ids == ["stale-root"]
    nodes = await db.get_nodes_by_session(target.id)
    by_path = {node.dtmf_path: node for node in nodes}
    assert set(by_path) == {"", "1", "1w1", "1w2"}

    # Structural copy: parent links follow the source tree.
    assert by_path[""].parent_id is None
    assert by_path["1"].parent_id == by_path[""].id
    assert by_path["1w1"].parent_id == by_path["1"].id

    edges = await db.get_edges_by_session(target.id)
    assert len(edges) == 3

    # Overridden node carries the target window's genuine evidence.
    human = by_path["1w1"]
    assert human.status == NodeStatus.COMPLETED
    assert human.realtime_verified is True
    assert human.prompt_text == "(human/queue) please hold"
    assert human.transcript == "real human call"
    assert human.call_id == "human-call"

    # Un-overridden nodes are inherited but must not carry source call evidence.
    english = by_path["1w2"]
    assert english.status == NodeStatus.COMPLETED
    assert english.prompt_text == "english recorded notice"
    assert english.transcript is None
    assert english.call_id is None


@pytest.mark.asyncio
async def test_rebuild_rejects_unknown_override_path():
    source = await _seed_source_session()
    target = await _seed_target_session()

    with pytest.raises(ValueError, match="not present"):
        await rebuild_session_tree(
            target_session_id=target.id,
            source_session_id=source.id,
            overrides={"9w9": NodeOverride(prompt_text="nope")},
        )

    # The target tree is untouched when validation fails.
    nodes = await db.get_nodes_by_session(target.id)
    assert [node.id for node in nodes] == ["stale-root"]


@pytest.mark.asyncio
async def test_rebuild_rejects_empty_source():
    target = await _seed_target_session()
    empty = Session(id="empty", phone_number="4006668800")
    await db.create_session(empty)

    with pytest.raises(ValueError, match="no nodes"):
        await rebuild_session_tree(
            target_session_id=target.id,
            source_session_id=empty.id,
        )
