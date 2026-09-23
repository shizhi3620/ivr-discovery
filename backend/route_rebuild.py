"""Rebuild one discovery route by reusing another route's evidence-backed tree.

IVR menus are frequently shared across time windows. Apple's 4006668800 line is
the working example: the daytime (09:00-21:00) and after-hours menus present the
same prompts and DTMF navigation, and the two routes differ only in what happens
at the end of the Mandarin branch. Inside the human window that branch hands the
caller to a live agent; after hours it plays a recorded notice instead.

Re-discovering the identical prefix would burn real calls for no new evidence,
so this module reconstructs the target route's tree from a reference route and
applies an explicit, per-path override table. Overrides are the only place a
route may differ from the reference, which keeps the difference auditable.

This module never places a call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import database as db
from models import Edge, Node, NodeStatus


@dataclass(frozen=True)
class NodeOverride:
    """Per-path replacement values applied to a rebuilt node.

    ``None`` means "keep the reference value". Only the fields listed here can
    differ from the reference tree.
    """

    prompt_text: str | None = None
    status: str | None = None
    realtime_verified: bool | None = None
    # Genuine target-window call evidence (never borrowed from the reference).
    transcript: str | None = None
    call_id: str | None = None


@dataclass
class RebuildResult:
    target_session_id: str
    source_session_id: str
    created_nodes: list[Node] = field(default_factory=list)
    created_edges: list[Edge] = field(default_factory=list)
    deleted_node_ids: list[str] = field(default_factory=list)
    deleted_edge_ids: list[str] = field(default_factory=list)

    def node_by_path(self, dtmf_path: str) -> Node | None:
        return next(
            (node for node in self.created_nodes if node.dtmf_path == dtmf_path),
            None,
        )


def _children_by_parent(
    nodes: Iterable[Node],
    edges: Iterable[Edge],
) -> tuple[dict[str, list[tuple[Edge, Node]]], list[Node]]:
    """Return (parent_id -> [(edge, child)], roots), ordered deterministically."""
    by_id = {node.id: node for node in nodes}
    children: dict[str, list[tuple[Edge, Node]]] = {}
    child_ids: set[str] = set()
    for edge in edges:
        child = by_id.get(edge.to_node_id or "")
        if child is None:
            continue
        children.setdefault(edge.from_node_id, []).append((edge, child))
        child_ids.add(child.id)
    for entries in children.values():
        entries.sort(key=lambda item: (item[0].dtmf_key, item[1].dtmf_path))
    roots = [node for node in nodes if node.id not in child_ids]
    roots.sort(key=lambda node: (node.dtmf_path, node.created_at))
    return children, roots


def _coerce_status(value: str) -> NodeStatus:
    try:
        return NodeStatus(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unknown node status in override: {value!r}") from exc


async def rebuild_session_tree(
    *,
    target_session_id: str,
    source_session_id: str,
    overrides: Mapping[str, NodeOverride | Mapping[str, object]] | None = None,
) -> RebuildResult:
    """Replace ``target_session_id``'s tree with a copy of the source tree.

    The source tree is copied structurally (nodes, parent links, edges) and each
    node inherits ``prompt_text``/``status``/``realtime_verified`` from the
    reference. Per-path ``overrides`` then replace individual fields. Call
    evidence (``call_id``, ``transcript``, ``cost``) is deliberately *not*
    copied: those belong to the source window's calls and must never be
    presented as evidence for the rebuilt route.

    Raises ``ValueError`` if the source session has no nodes or a path override
    does not exist in the source tree.
    """
    normalised: dict[str, NodeOverride] = {}
    for path, override in (overrides or {}).items():
        if isinstance(override, NodeOverride):
            normalised[path] = override
        else:
            normalised[path] = NodeOverride(
                prompt_text=override.get("prompt_text"),  # type: ignore[arg-type]
                status=override.get("status"),  # type: ignore[arg-type]
                realtime_verified=override.get("realtime_verified"),  # type: ignore[arg-type]
                transcript=override.get("transcript"),  # type: ignore[arg-type]
                call_id=override.get("call_id"),  # type: ignore[arg-type]
            )

    source_nodes = await db.get_nodes_by_session(source_session_id)
    if not source_nodes:
        raise ValueError(f"source session {source_session_id} has no nodes")
    source_edges = await db.get_edges_by_session(source_session_id)

    source_paths = {node.dtmf_path for node in source_nodes}
    unknown = sorted(set(normalised) - source_paths)
    if unknown:
        raise ValueError(
            "override path(s) not present in source tree: " + ", ".join(unknown)
        )

    children, roots = _children_by_parent(source_nodes, source_edges)
    if not roots:
        raise ValueError(
            f"source session {source_session_id} has no reachable root node"
        )

    deleted_node_ids, deleted_edge_ids = await db.delete_all_nodes_by_session(
        target_session_id
    )

    result = RebuildResult(
        target_session_id=target_session_id,
        source_session_id=source_session_id,
        deleted_node_ids=deleted_node_ids,
        deleted_edge_ids=deleted_edge_ids,
    )

    id_map: dict[str, str] = {}

    async def copy_node(source: Node, parent_id: str | None) -> Node:
        override = normalised.get(source.dtmf_path)
        status = source.status
        realtime_verified = source.realtime_verified
        prompt_text = source.prompt_text
        if override is not None:
            if override.status is not None:
                status = _coerce_status(override.status)
            if override.realtime_verified is not None:
                realtime_verified = override.realtime_verified
            if override.prompt_text is not None:
                prompt_text = override.prompt_text
        node = Node(
            session_id=target_session_id,
            parent_id=parent_id,
            dtmf_path=source.dtmf_path,
            voice_option=source.voice_option,
            prompt_text=prompt_text,
            status=status,
            realtime_verified=realtime_verified,
            transcript=(
                override.transcript if override is not None else None
            ),
            call_id=override.call_id if override is not None else None,
        )
        await db.create_node(node)
        id_map[source.id] = node.id
        result.created_nodes.append(node)
        return node

    async def copy_subtree(source: Node, parent_id: str | None) -> Node:
        copied = await copy_node(source, parent_id)
        for edge, child in children.get(source.id, []):
            child_copy = await copy_subtree(child, copied.id)
            new_edge = Edge(
                from_node_id=copied.id,
                to_node_id=child_copy.id,
                dtmf_key=edge.dtmf_key,
                label=edge.label,
            )
            await db.create_edge(new_edge)
            result.created_edges.append(new_edge)
        return copied

    for root in roots:
        await copy_subtree(root, None)

    return result
