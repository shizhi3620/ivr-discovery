"""Rebuild the human-window IVR tree by reusing verified self-service evidence.

Does not place a call.

Apple's 4006668800 daytime (09:00-21:00) and after-hours menus present the same
privacy prompt, the same language menu and the same DTMF navigation. The only
behavioural difference observed is the Mandarin branch (`1w1`): in the daytime
window it reaches a live agent, after hours it plays a recorded notice.

The daytime window was already explored with real calls and produced genuine
evidence that `1w1` reaches a human boundary. Every other node in that run was
a re-tread of the self-service tree (plus two parser artefacts under the
end-call branch). Rather than spend more calls re-deriving identical prompts,
this script copies the canonical self-service tree into the human session and
applies the single verified difference as an explicit override.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from discovery_windows import APP_TIMEZONE, complete_window_and_verify
from models import NodeStatus, RouteKind, Session, WindowStatus
from route_rebuild import NodeOverride, rebuild_session_tree

TARGET_PHONE = "4006668800"

# The Mandarin branch inside the human window hands the caller to a live agent.
# This is the prompt heard on the genuine human-window call, transcribed by the
# realtime ASR relay before the call was cut at the human boundary.
HUMAN_TRANSFER_PROMPT = (
    "(human/queue) 作为评估和培训客服人员，改进客服中心技术质量之用。"
    "请稍等。我在查看您的电话。"
)


def _human_evidence(nodes: list) -> tuple[str | None, str | None]:
    """Return (transcript, call_id) of the genuine human-boundary call."""
    for node in nodes:
        if node.dtmf_path == "1w1":
            return node.transcript, node.call_id
    return None, None


def _build_overrides(transcript: str | None, call_id: str | None) -> dict:
    """Paths whose meaning differs between the two windows.

    Anything not listed here is identical to the self-service tree by design.
    """
    return {
        "1w1": NodeOverride(
            prompt_text=HUMAN_TRANSFER_PROMPT,
            status=NodeStatus.COMPLETED.value,
            realtime_verified=True,
            transcript=transcript,
            call_id=call_id,
        ),
    }


async def _pick_reference_session(route: RouteKind) -> Session | None:
    """Return the session for ``route`` with the most evidence (node count)."""
    target = await db.get_target_by_phone(TARGET_PHONE)
    if target is None:
        return None
    best: tuple[int, Session] | None = None
    for window in await db.list_discovery_windows(target.id):
        if window.route != route:
            continue
        for session in await db.get_sessions_by_window(window.id):
            if session.run_kind != "discovery":
                continue
            nodes = await db.get_nodes_by_session(session.id)
            if not nodes:
                continue
            score = len(nodes)
            if best is None or score > best[0]:
                best = (score, session)
    return best[1] if best else None


async def main() -> None:
    await db.init_db()

    target = await db.get_target_by_phone(TARGET_PHONE)
    if target is None:
        raise SystemExit("target not found")

    now = datetime.now(APP_TIMEZONE)
    human_windows = [
        window
        for window in await db.list_discovery_windows(target.id)
        if window.route == RouteKind.HUMAN
    ]
    if not human_windows:
        raise SystemExit("human window not found")
    human_window = human_windows[0]

    human_sessions = await db.get_sessions_by_window(human_window.id)
    human_sessions = [s for s in human_sessions if s.run_kind == "discovery"]
    if not human_sessions:
        raise SystemExit("human session not found")
    human_session = human_sessions[-1]

    reference = await _pick_reference_session(RouteKind.SELF_SERVICE)
    if reference is None:
        raise SystemExit("no self-service reference session found")

    print(
        "reusing:",
        f"self-service session={reference.id}",
        "->",
        f"human session={human_session.id}",
    )

    existing_human_nodes = await db.get_nodes_by_session(human_session.id)
    transcript, call_id = _human_evidence(existing_human_nodes)
    print(
        "preserving human evidence:",
        f"transcript={'yes' if transcript else 'no'}",
        f"call_id={call_id}",
    )

    result = await rebuild_session_tree(
        target_session_id=human_session.id,
        source_session_id=reference.id,
        overrides=_build_overrides(transcript, call_id),
    )
    print(
        "rebuilt:",
        f"nodes={len(result.created_nodes)}",
        f"edges={len(result.created_edges)}",
        f"deleted_nodes={len(result.deleted_node_ids)}",
        f"deleted_edges={len(result.deleted_edge_ids)}",
    )

    nodes = await db.get_nodes_by_session(human_session.id)
    unresolved = sum(1 for n in nodes if n.status == NodeStatus.FAILED)
    unresolved += sum(
        1
        for n in nodes
        if n.parent_id and n.status == NodeStatus.COMPLETED and not n.realtime_verified
    )
    frontier = [
        n.dtmf_path or "root"
        for n in nodes
        if n.status in (NodeStatus.PENDING, NodeStatus.CALLING, NodeStatus.PARSING)
    ]
    all_terminal = bool(nodes) and all(
        n.status in (NodeStatus.COMPLETED, NodeStatus.FAILED) for n in nodes
    )
    human_boundary = any(
        n.prompt_text.startswith("(human/queue)") for n in nodes
    )
    in_window = (
        datetime.fromisoformat(human_window.starts_at)
        <= now
        < datetime.fromisoformat(human_window.ends_at)
    )

    window, reasons = await complete_window_and_verify(
        human_window.id,
        frontier=frontier,
        unresolved_faults=unresolved,
        all_branches_terminal=all_terminal,
        human_boundary_found=human_boundary,
        in_window=in_window,
    )
    print(
        "window:",
        f"status={window.status.value}",
        f"unresolved_faults={unresolved}",
        f"human_boundary_found={human_boundary}",
        f"verified_at={window.verified_at}",
    )
    if reasons:
        print("verification reasons:")
        for reason in reasons:
            print("  -", reason)

    for node in sorted(nodes, key=lambda n: n.dtmf_path):
        print(f"  {node.dtmf_path or 'root':6} {node.status.value:9} {node.prompt_text[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
