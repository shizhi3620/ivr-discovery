from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from fastapi import WebSocket

import database as db
import transcript_parser
from discovery_windows import (
    APP_TIMEZONE,
    authorize_run,
    complete_window_and_verify,
)
from models import Session, Node, Edge, NodeStatus, SessionStatus
from providers import TelephonyProvider, get_provider
from providers.base import STATUS_BUSY, STATUS_COMPLETED

logger = logging.getLogger(__name__)

# A single cellular modem/SIM can carry only one active voice call.
MAX_CONCURRENT_CALLS = max(1, int(os.getenv("MAX_CONCURRENT_CALLS", "1")))
MAX_DEPTH = max(1, int(os.getenv("DISCOVERY_MAX_DEPTH", "5")))
MIN_TRANSCRIPT_LENGTH = 20  # Retry if transcript is shorter than this
ROOT_CALL_MAX_DURATION = max(1, int(os.getenv("ROOT_CALL_MAX_DURATION", "60")))
BRANCH_CALL_MAX_DURATION = max(1, int(os.getenv("BRANCH_CALL_MAX_DURATION", "60")))
CALL_COOLDOWN_SECONDS = max(0.0, float(os.getenv("CALL_COOLDOWN_SECONDS", "0")))


async def send_json(ws: WebSocket, data: dict):
    try:
        await ws.send_json(data)
    except Exception:
        pass


import re

def _core_label(label: str) -> str:
    """Extract the core of a label by stripping parenthetical descriptions.

    'Package (track status, delivery issues)' -> 'package'
    'Mail (daily services, pickup, change of address)' -> 'mail'
    'Tools (pricing, hours, locations)' -> 'tools'
    """
    # Strip parenthetical content
    core = re.sub(r'\s*\(.*?\)', '', label).strip().lower()
    return core or label.strip().lower()


def options_fingerprint(options: list[dict]) -> frozenset[str]:
    """Create a fingerprint from a set of menu options for cycle detection.

    Normalizes labels: strips parenthetical descriptions, lowercases.
    """
    return frozenset(_core_label(opt["label"]) for opt in options)


def is_cycle(fingerprint: frozenset[str], seen_menus: set[frozenset[str]], threshold: float = 0.6) -> bool:
    """Check if a fingerprint is a fuzzy match to any previously seen menu.

    Uses Jaccard similarity — if >threshold of core labels overlap, it's a cycle.
    This handles cases where the model paraphrases the same IVR menu differently.
    """
    if fingerprint in seen_menus:
        return True

    for seen in seen_menus:
        if not seen or not fingerprint:
            continue
        intersection = len(fingerprint & seen)
        union = len(fingerprint | seen)
        if union > 0 and intersection / union >= threshold:
            return True

    return False


async def explore_node(
    ws: WebSocket,
    session: Session,
    node: Node,
    provider: TelephonyProvider | None = None,
) -> list[dict]:
    """Explore a single IVR node: place call, wait, parse transcript.

    Returns list of {"dtmf_key": str, "label": str} for discovered options.

    Telephony execution is delegated to `provider`; this function only owns the
    discovery-level retry policy and transcript parsing.
    """
    provider = provider or get_provider()

    # Update node to calling
    await db.update_node(node.id, status=NodeStatus.CALLING)
    await send_json(ws, {
        "type": "node_updated",
        "node_id": node.id,
        "status": "calling",
    })

    try:
        # Determine call type: DTMF navigation, voice navigation, or root.
        # The provider resolves these into its own vendor-specific call setup.
        dtmf_seq = node.dtmf_path or None
        voice_option = node.voice_option or None

        # Root calls get longer to hear full menu; branch calls are shorter
        is_root = not node.parent_id
        max_dur = ROOT_CALL_MAX_DURATION if is_root else BRANCH_CALL_MAX_DURATION

        # Stream live transcript to frontend as it arrives
        async def on_transcript(text: str):
            await send_json(ws, {
                "type": "live_transcript",
                "node_id": node.id,
                "text": text,
            })

        # Discovery is transcript-driven. A provider without a configured ASR
        # backend cannot feed the parser, so fail fast *without dialing* rather
        # than spending a real cellular call that could produce nothing.
        if not provider.capabilities.transcript:
            message = (
                f"Provider '{provider.name}' has no transcript capability; "
                "configure the Audio Provider before starting discovery"
            )
            logger.warning("Node %s: %s", node.id[:8], message)
            await db.update_node(node.id, status=NodeStatus.FAILED, prompt_text=message)
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "failed",
                "prompt_text": message,
            })
            return []

        if node.dtmf_path and not provider.capabilities.realtime_dtmf:
            message = (
                "Realtime DTMF navigation is required for branch calls; "
                "the configured provider only supports timed DTMF"
            )
            await db.update_node(
                node.id,
                status=NodeStatus.FAILED,
                prompt_text=message,
            )
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "failed",
                "prompt_text": message,
            })
            return []

        budget_error = await _budget_gate_error(session)
        if budget_error:
            await db.update_node(
                node.id,
                status=NodeStatus.FAILED,
                prompt_text=budget_error,
            )
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "failed",
                "prompt_text": budget_error,
            })
            return []

        # Retry loop: retry once if transcript is too short (silence/dropped call)
        concatenated = ""
        call_status = "unknown"
        cost = 0.0
        max_attempts = 3  # Retry on busy/short transcript

        for attempt in range(max_attempts):
            call_id = await provider.place_call(
                session.phone_number,
                dtmf_sequence=dtmf_seq,
                voice_option=voice_option,
                max_duration=max_dur,
            )
            try:
                await _record_exploration_call(session, call_id)
            except db.BudgetExhaustedError as exc:
                await provider.stop_call(call_id)
                message = str(exc)
                await db.update_node(
                    node.id,
                    status=NodeStatus.FAILED,
                    prompt_text=message,
                )
                await send_json(ws, {
                    "type": "node_updated",
                    "node_id": node.id,
                    "status": "failed",
                    "prompt_text": message,
                })
                return []

            await db.update_node(node.id, call_id=call_id)
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "calling",
                "call_id": call_id,
            })

            call_result = await provider.wait_for_call(call_id, on_transcript=on_transcript)
            call_status = call_result.status
            concatenated = call_result.transcript
            cost += call_result.cost

            if call_result.realtime_fault:
                message = f"Realtime navigation failed: {call_result.realtime_fault}"
                await db.update_node(
                    node.id,
                    status=NodeStatus.FAILED,
                    prompt_text=message,
                    transcript=concatenated,
                    cost=cost,
                    realtime_verified=False,
                )
                await send_json(ws, {
                    "type": "node_updated",
                    "node_id": node.id,
                    "status": "failed",
                    "prompt_text": message,
                    "cost": cost,
                })
                return []

            if CALL_COOLDOWN_SECONDS:
                await asyncio.sleep(CALL_COOLDOWN_SECONDS)

            # Retry on busy (line occupied by another call) or short transcript
            if call_status == STATUS_BUSY and attempt < max_attempts - 1:
                wait = 5 + attempt * 5  # 5s, 10s backoff
                logger.info(f"Node {node.id[:8]}... line busy, retrying in {wait}s (attempt {attempt + 1})")
                await asyncio.sleep(wait)
                continue

            if len(concatenated.strip()) >= MIN_TRANSCRIPT_LENGTH:
                break
            if attempt < max_attempts - 1:
                logger.info(f"Node {node.id[:8]}... short transcript ({len(concatenated)} chars), retrying")

        if call_status != STATUS_COMPLETED:
            await db.update_node(
                node.id,
                status=NodeStatus.FAILED,
                prompt_text=f"Call status: {call_status}",
                cost=cost,
            )
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "failed",
                "cost": cost,
            })
            return []

        # Call succeeded — parse transcript
        await db.update_node(
            node.id,
            status=NodeStatus.PARSING,
            transcript=concatenated,
            cost=cost,
            realtime_verified=bool(node.dtmf_path),
        )
        await send_json(ws, {
            "type": "node_updated",
            "node_id": node.id,
            "status": "parsing",
            "cost": cost,
        })

        # Parse transcript with the configured AI Provider
        parsed = await transcript_parser.parse_transcript(
            concatenated,
            dtmf_path=node.dtmf_path,
        )
        if parsed.get("parse_error"):
            message = f"Transcript parsing failed: {parsed['parse_error']}"
            await db.update_node(
                node.id,
                status=NodeStatus.FAILED,
                prompt_text=message,
                transcript=concatenated,
                cost=cost,
                realtime_verified=bool(node.dtmf_path),
            )
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "failed",
                "prompt_text": message,
                "cost": cost,
            })
            return []
        prompt_text = parsed.get("prompt_text", concatenated[:200])
        options = parsed.get("options", [])

        if parsed.get("human_transfer"):
            boundary_prompt = f"(human/queue) {prompt_text or 'Human service boundary'}"
            await db.update_node(
                node.id,
                status=NodeStatus.COMPLETED,
                prompt_text=boundary_prompt,
            )
            await send_json(ws, {
                "type": "node_updated",
                "node_id": node.id,
                "status": "completed",
                "prompt_text": boundary_prompt,
            })
            return []

        # Mark node as completed
        await db.update_node(node.id, status=NodeStatus.COMPLETED, prompt_text=prompt_text)
        await send_json(ws, {
            "type": "node_updated",
            "node_id": node.id,
            "status": "completed",
            "prompt_text": prompt_text,
        })

        return options

    except Exception as e:
        logger.exception(f"Error exploring node {node.id}")
        await db.update_node(node.id, status=NodeStatus.FAILED, prompt_text=str(e))
        await send_json(ws, {
            "type": "node_updated",
            "node_id": node.id,
            "status": "failed",
        })
        return []


async def _budget_gate_error(session: Session) -> str:
    if not session.target_id or not session.discovery_window_id:
        return ""
    allowed, reason = await authorize_run(
        target_id=session.target_id,
        window_id=session.discovery_window_id,
        now=datetime.now(APP_TIMEZONE),
        override_reason=session.override_reason,
    )
    return "" if allowed else f"Call blocked by discovery gate: {reason}"


async def _record_exploration_call(session: Session, call_id: str) -> None:
    if not session.target_id or not session.discovery_window_id:
        return
    await db.record_call_attempt(
        target_id=session.target_id,
        discovery_window_id=session.discovery_window_id,
        session_id=session.id,
        external_call_id=call_id,
        note="originated by discovery run",
    )


async def finalize_window_for_session(session: Session) -> None:
    if not session.discovery_window_id or not session.target_id:
        return
    window = await db.get_discovery_window(session.discovery_window_id)
    if window is None:
        return
    nodes = await db.get_nodes_by_session(session.id)
    frontier = [
        node.dtmf_path or "root"
        for node in nodes
        if node.status in (
            NodeStatus.PENDING,
            NodeStatus.CALLING,
            NodeStatus.PARSING,
        )
    ]
    unresolved_faults = sum(
        1 for node in nodes if node.status == NodeStatus.FAILED
    )
    unresolved_faults += sum(
        1
        for node in nodes
        if node.parent_id
        and node.status == NodeStatus.COMPLETED
        and not node.realtime_verified
    )
    all_branches_terminal = bool(nodes) and all(
        node.status in (NodeStatus.COMPLETED, NodeStatus.FAILED)
        for node in nodes
    )
    human_boundary_found = any(
        node.prompt_text.startswith("(human/queue)")
        for node in nodes
    )
    now = datetime.now(APP_TIMEZONE)
    in_window = (
        not session.override_reason
        and datetime.fromisoformat(window.starts_at) <= now
        < datetime.fromisoformat(window.ends_at)
    )
    await complete_window_and_verify(
        window.id,
        frontier=frontier,
        unresolved_faults=unresolved_faults,
        all_branches_terminal=all_branches_terminal,
        human_boundary_found=human_boundary_found,
        in_window=in_window,
    )


def get_node_depth(node: Node, nodes_by_id: dict[str, Node]) -> int:
    """Calculate depth of a node by walking parent chain."""
    depth = 0
    current = node
    while current.parent_id and current.parent_id in nodes_by_id:
        depth += 1
        current = nodes_by_id[current.parent_id]
    return depth


async def create_children(
    ws: WebSocket,
    session: Session,
    parent: Node,
    options: list[dict],
) -> list[Node]:
    """Create child nodes and edges for discovered options."""
    children = []
    for opt in options:
        dtmf_key = opt["dtmf_key"]
        label = opt["label"]
        is_voice = dtmf_key.startswith("say")

        # Build navigation path
        if is_voice:
            dtmf_path = ""
            voice_option = label
        else:
            if parent.dtmf_path:
                dtmf_path = f"{parent.dtmf_path}w{dtmf_key}"
            else:
                dtmf_path = dtmf_key
            voice_option = ""

        child = Node(
            session_id=session.id,
            parent_id=parent.id,
            dtmf_path=dtmf_path,
            voice_option=voice_option,
            status=NodeStatus.PENDING,
        )
        await db.create_node(child)
        await send_json(ws, {"type": "node_added", "node": child.model_dump()})

        edge = Edge(
            from_node_id=parent.id,
            to_node_id=child.id,
            dtmf_key=dtmf_key,
            label=label,
        )
        await db.create_edge(edge)
        await send_json(ws, {"type": "edge_added", "edge": edge.model_dump()})

        children.append(child)

    return children


async def send_session_status(ws: WebSocket, session: Session):
    """Send current session status to frontend."""
    nodes = await db.get_nodes_by_session(session.id)
    total_cost = sum(n.cost for n in nodes)

    await db.update_session(session.id, total_cost=total_cost)
    payload = await session_status_payload(
        session,
        status=SessionStatus.RUNNING,
        nodes=nodes,
        total_cost=total_cost,
    )
    await send_json(ws, {
        "type": "session_status",
        "session": payload,
    })


async def session_status_payload(
    session: Session,
    *,
    status: SessionStatus,
    nodes: list[Node] | None = None,
    total_cost: float | None = None,
) -> dict:
    nodes = nodes if nodes is not None else await db.get_nodes_by_session(session.id)
    total_cost = (
        total_cost
        if total_cost is not None
        else sum(node.cost for node in nodes)
    )
    payload = {
        "id": session.id,
        "phone_number": session.phone_number,
        "status": status.value,
        "total_cost": total_cost,
        "total_nodes": len(nodes),
        "completed_nodes": sum(
            1 for node in nodes if node.status == NodeStatus.COMPLETED
        ),
        "failed_nodes": sum(
            1 for node in nodes if node.status == NodeStatus.FAILED
        ),
        "target_id": session.target_id,
        "discovery_window_id": session.discovery_window_id,
        "planned_route": (
            session.planned_route.value if session.planned_route else None
        ),
    }
    if session.target_id and session.discovery_window_id:
        payload["budget"] = await db.get_budget_summary(
            session.target_id,
            session.discovery_window_id,
        )
    return payload


async def run_discovery(
    ws: WebSocket,
    phone_number: str,
    session: Session,
    provider: TelephonyProvider | None = None,
    *,
    resume: bool = False,
):
    """Run concurrent discovery of the IVR tree using a worker pool.

    Uses fingerprint-based cycle detection: each set of parsed options is
    fingerprinted (frozenset of normalized labels). If we've already seen
    that exact menu elsewhere in the tree, we skip it — the IVR looped
    back to a previously-explored menu.
    """
    provider = provider or get_provider()
    await db.update_session(
        session.id,
        status=SessionStatus.RUNNING,
        started_at=datetime.now(APP_TIMEZONE).isoformat(),
    )

    queue: asyncio.PriorityQueue[tuple[int, str, Node]] = asyncio.PriorityQueue()
    nodes_by_id: dict[str, Node] = {}

    # Cycle detection: track fingerprints of all menus we've already explored
    seen_menus: set[frozenset[str]] = set()

    resumed = False
    if resume:
        existing_nodes = await db.get_nodes_by_session(session.id)
        if existing_nodes:
            resumed = True
            nodes_by_id = {node.id: node for node in existing_nodes}
            edges = await db.get_edges_by_session(session.id)
            edges_by_node: dict[str, list[dict]] = {}
            for edge in edges:
                edges_by_node.setdefault(edge.from_node_id, []).append(
                    {"dtmf_key": edge.dtmf_key, "label": edge.label}
                )
            for node in existing_nodes:
                if node.status == NodeStatus.COMPLETED:
                    options = edges_by_node.get(node.id, [])
                    if options:
                        seen_menus.add(options_fingerprint(options))
                if node.status in (NodeStatus.CALLING, NodeStatus.PARSING):
                    await db.update_node(node.id, status=NodeStatus.PENDING)
                    node.status = NodeStatus.PENDING
                if node.status == NodeStatus.PENDING:
                    depth = get_node_depth(node, nodes_by_id)
                    await queue.put((depth, node.id, node))
                    await send_json(
                        ws,
                        {"type": "node_added", "node": node.model_dump()},
                    )

    if not resumed:
        # Create and enqueue root node
        root = Node(session_id=session.id, dtmf_path="", status=NodeStatus.PENDING)
        await db.create_node(root)
        nodes_by_id[root.id] = root
        await send_json(ws, {"type": "node_added", "node": root.model_dump()})
        await queue.put((0, root.id, root))  # (depth, id for tiebreak, node)

    async def worker(worker_id: int):
        while True:
            depth, _, node = await queue.get()
            try:
                if depth >= MAX_DEPTH:
                    logger.info(f"[W{worker_id}] Skipping {node.id[:8]}... — max depth {depth}")
                    await db.update_node(node.id, status=NodeStatus.COMPLETED, prompt_text="Max depth reached")
                    await send_json(ws, {
                        "type": "node_updated",
                        "node_id": node.id,
                        "status": "completed",
                        "prompt_text": "Max depth reached",
                    })
                    continue

                logger.info(f"[W{worker_id}] Exploring {node.id[:8]}... (depth={depth}, path={node.dtmf_path or 'root'})")
                options = await explore_node(ws, session, node, provider)
                await send_session_status(ws, session)

                if not options:
                    logger.info(f"[W{worker_id}] {node.id[:8]}... is a leaf (no options)")
                    continue

                # Cycle detection: fuzzy match against all previously seen menus
                fp = options_fingerprint(options)
                if is_cycle(fp, seen_menus):
                    logger.info(f"[W{worker_id}] {node.id[:8]}... CYCLE DETECTED — same menu already explored, skipping children")
                    # Update prompt to indicate cycle
                    await db.update_node(node.id, prompt_text=f"(cycle) {node.prompt_text or ''}")
                    await send_json(ws, {
                        "type": "node_updated",
                        "node_id": node.id,
                        "prompt_text": f"(cycle) {node.prompt_text or ''}",
                    })
                    continue

                seen_menus.add(fp)
                logger.info(f"[W{worker_id}] {node.id[:8]}... found {len(options)} NEW options: {[o['label'] for o in options]}")

                children = await create_children(ws, session, node, options)
                child_depth = depth + 1
                for c in children:
                    nodes_by_id[c.id] = c
                    await queue.put((child_depth, c.id, c))
                await send_session_status(ws, session)

            except Exception:
                logger.exception(f"[W{worker_id}] Error processing {node.id[:8]}...")
            finally:
                queue.task_done()

    # Start worker pool
    workers = [asyncio.create_task(worker(i)) for i in range(MAX_CONCURRENT_CALLS)]

    try:
        # Wait until all queued work is complete
        await queue.join()
        final_status = SessionStatus.COMPLETED
    except asyncio.CancelledError:
        logger.info("Discovery cancelled")
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await _stop_inflight_nodes(session, provider)
        final_status = SessionStatus.FAILED
    except Exception:
        logger.exception("Discovery failed unexpectedly")
        final_status = SessionStatus.FAILED
    finally:
        # Cancel idle workers (they're blocked on queue.get())
        for w in workers:
            w.cancel()

    # Final session status
    await db.update_session(
        session.id,
        status=final_status,
        ended_at=datetime.now(APP_TIMEZONE).isoformat(),
    )
    nodes = await db.get_nodes_by_session(session.id)
    total_cost = sum(n.cost for n in nodes)
    await db.update_session(session.id, total_cost=total_cost)
    await finalize_window_for_session(session)
    await send_json(ws, {
        "type": "session_status",
        "session": await session_status_payload(
            session,
            status=final_status,
            nodes=nodes,
            total_cost=total_cost,
        ),
    })
    logger.info(f"Discovery {final_status.value}: {len(nodes)} nodes, ${total_cost:.4f} total cost, {len(seen_menus)} unique menus")


async def _stop_inflight_nodes(
    session: Session,
    provider: TelephonyProvider,
) -> None:
    nodes = await db.get_nodes_by_session(session.id)
    for node in nodes:
        if node.status not in (NodeStatus.CALLING, NodeStatus.PARSING):
            continue
        if node.call_id:
            try:
                await provider.stop_call(node.call_id)
            except Exception:
                logger.warning("Failed to stop cancelled call %s", node.call_id)
        await db.update_node(
            node.id,
            status=NodeStatus.FAILED,
            prompt_text="Run cancelled; call stopped",
        )


async def rediscover_subtree(
    ws: WebSocket,
    node_id: str,
    provider: TelephonyProvider | None = None,
):
    """Delete a node's children and re-explore it as a mini discovery."""
    provider = provider or get_provider()
    node = await db.get_node(node_id)
    if not node:
        await send_json(ws, {"type": "error", "message": "Node not found"})
        return

    session = await db.get_session(node.session_id)
    if not session:
        await send_json(ws, {"type": "error", "message": "Session not found"})
        return

    # Delete all descendants and their edges
    deleted_nodes, deleted_edges = await db.delete_subtree(node_id)
    await send_json(ws, {
        "type": "subtree_cleared",
        "node_id": node_id,
        "deleted_node_ids": deleted_nodes,
        "deleted_edge_ids": deleted_edges,
    })

    logger.info(f"Cleared subtree of {node_id[:8]}...: {len(deleted_nodes)} nodes, {len(deleted_edges)} edges")

    # Re-explore just this node using the same logic as run_discovery but single-node
    await db.update_session(session.id, status=SessionStatus.RUNNING)
    await send_json(ws, {
        "type": "node_updated",
        "node_id": node_id,
        "status": "pending",
        "prompt_text": "",
    })

    # Collect existing nodes for depth calculation
    all_nodes = await db.get_nodes_by_session(session.id)
    nodes_by_id = {n.id: n for n in all_nodes}

    # Rebuild seen_menus from existing completed nodes (excluding the target subtree)
    seen_menus: set[frozenset[str]] = set()

    # Re-fetch the reset node
    node = await db.get_node(node_id)

    start_depth = get_node_depth(node, nodes_by_id)
    queue: asyncio.PriorityQueue[tuple[int, str, Node]] = asyncio.PriorityQueue()
    await queue.put((start_depth, node.id, node))

    async def worker(worker_id: int):
        while True:
            depth, _, n = await queue.get()
            try:
                if depth >= MAX_DEPTH:
                    await db.update_node(n.id, status=NodeStatus.COMPLETED, prompt_text="Max depth reached")
                    await send_json(ws, {
                        "type": "node_updated",
                        "node_id": n.id,
                        "status": "completed",
                        "prompt_text": "Max depth reached",
                    })
                    continue

                options = await explore_node(ws, session, n, provider)
                await send_session_status(ws, session)

                if not options:
                    continue

                fp = options_fingerprint(options)
                if is_cycle(fp, seen_menus):
                    await db.update_node(n.id, prompt_text=f"(cycle) {n.prompt_text or ''}")
                    await send_json(ws, {
                        "type": "node_updated",
                        "node_id": n.id,
                        "prompt_text": f"(cycle) {n.prompt_text or ''}",
                    })
                    continue

                seen_menus.add(fp)
                children = await create_children(ws, session, n, options)
                child_depth = depth + 1
                for c in children:
                    nodes_by_id[c.id] = c
                    await queue.put((child_depth, c.id, c))
                await send_session_status(ws, session)

            except Exception:
                logger.exception(f"[W{worker_id}] Error in rediscovery")
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker(i)) for i in range(MAX_CONCURRENT_CALLS)]
    await queue.join()
    for w in workers:
        w.cancel()

    # Update session status
    await db.update_session(session.id, status=SessionStatus.COMPLETED)
    await finalize_window_for_session(session)
    await send_session_status(ws, session)
    logger.info(f"Rediscovery of {node_id[:8]}... complete")
