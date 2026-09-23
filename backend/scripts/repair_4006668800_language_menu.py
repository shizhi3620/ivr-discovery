"""Repair the known Apple language-menu node using verified call evidence.

This does not place a call. It corrects the file-ASR text for the successful
realtime branch-1 navigation and creates the two language children.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from models import Edge, Node, NodeStatus, RouteKind, WindowStatus

TARGET_PHONE = "4006668800"
CORRECTED_TRANSCRIPT = """感谢您致电Apple。
为了给您提供最好的服务。
按照Apple隐私政策的规定，与本次通话相关的部分有限个人信息。
可能会在中国大陆境外存储和处理。
如果您同意，请按1。
如需结束本次通话，请按2。
感谢您致电Apple。普通话按1。
For technical support in English, press two.
您的通话将会被录音。"""
LANGUAGE_PROMPT = (
    "感谢您致电Apple。普通话按1。"
    "For technical support in English, press two。"
    "您的通话将会被录音。"
)


async def main() -> None:
    await db.init_db()
    target = await db.get_target_by_phone(TARGET_PHONE)
    if target is None:
        raise SystemExit("target not found")
    windows = [
        window
        for window in await db.list_discovery_windows(target.id)
        if window.route == RouteKind.SELF_SERVICE
    ]
    if not windows:
        raise SystemExit("self-service window not found")
    window = windows[0]
    sessions = await db.get_sessions_by_window(window.id)
    if not sessions:
        raise SystemExit("run not found")
    session = sessions[-1]
    nodes = await db.get_nodes_by_session(session.id)
    by_path = {node.dtmf_path: node for node in nodes}
    branch_one = by_path.get("1")
    branch_two = by_path.get("2")
    if branch_one is None or branch_two is None:
        raise SystemExit("branch nodes not found")

    await db.update_node(
        branch_one.id,
        status=NodeStatus.COMPLETED,
        prompt_text=LANGUAGE_PROMPT,
        transcript=CORRECTED_TRANSCRIPT,
        realtime_verified=True,
    )
    await db.update_node(
        branch_two.id,
        status=NodeStatus.PENDING,
        prompt_text="",
        transcript=None,
        call_id=None,
        cost=0.0,
        realtime_verified=False,
    )

    existing_edges = await db.get_edges_by_session(session.id)
    existing_keys = {
        edge.dtmf_key
        for edge in existing_edges
        if edge.from_node_id == branch_one.id
    }
    children = {
        "1": ("普通话", "1w1"),
        "2": ("For technical support in English", "1w2"),
    }
    for key, (label, path) in children.items():
        if key in existing_keys:
            continue
        child = await db.get_nodes_by_session(session.id)
        existing_child = next(
            (node for node in child if node.dtmf_path == path),
            None,
        )
        if existing_child is None:
            existing_child = Node(
                session_id=session.id,
                parent_id=branch_one.id,
                dtmf_path=path,
                status=NodeStatus.PENDING,
            )
            await db.create_node(existing_child)
        await db.create_edge(
            Edge(
                from_node_id=branch_one.id,
                to_node_id=existing_child.id,
                dtmf_key=key,
                label=label,
            )
        )

    await db.update_discovery_window(
        window.id,
        status=WindowStatus.COMPLETED,
        verified_at=None,
        unresolved_faults=1,
        frontier=["2", "1w1", "1w2"],
    )
    print(
        "repaired",
        f"session={session.id}",
        f"branch_one={branch_one.id}",
        f"branch_two={branch_two.id}",
    )


if __name__ == "__main__":
    asyncio.run(main())
