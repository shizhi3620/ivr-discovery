from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from models import SCHEMA_SQL, Session, Node, Edge

DB_PATH = "ivr_discovery.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def create_session(session: Session):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, phone_number, status, total_cost, created_at) VALUES (?, ?, ?, ?, ?)",
            (session.id, session.phone_number, session.status.value, session.total_cost, session.created_at),
        )
        await db.commit()


async def update_session(session_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = [v.value if hasattr(v, "value") else v for v in kwargs.values()]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE sessions SET {sets} WHERE id = ?", (*vals, session_id))
        await db.commit()


async def get_latest_session() -> Session | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                return Session(**dict(row))
    return None


async def get_session(session_id: str) -> Session | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return Session(**dict(row))
    return None


async def create_node(node: Node):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO nodes (id, session_id, parent_id, dtmf_path, voice_option, prompt_text, status, call_id, cost, transcript, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node.id, node.session_id, node.parent_id, node.dtmf_path, node.voice_option, node.prompt_text, node.status.value, node.call_id, node.cost, node.transcript, node.created_at),
        )
        await db.commit()


async def update_node(node_id: str, **kwargs):
    if not kwargs:
        return
    processed = {}
    for k, v in kwargs.items():
        if hasattr(v, "value"):
            processed[k] = v.value
        else:
            processed[k] = v
    sets = ", ".join(f"{k} = ?" for k in processed)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE nodes SET {sets} WHERE id = ?", (*processed.values(), node_id))
        await db.commit()


async def get_node(node_id: str) -> Node | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return Node(**dict(row))
    return None


async def get_nodes_by_session(session_id: str) -> list[Node]:
    nodes = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM nodes WHERE session_id = ?", (session_id,)) as cursor:
            async for row in cursor:
                nodes.append(Node(**dict(row)))
    return nodes


async def create_edge(edge: Edge):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO edges (id, from_node_id, to_node_id, dtmf_key, label) VALUES (?, ?, ?, ?, ?)",
            (edge.id, edge.from_node_id, edge.to_node_id, edge.dtmf_key, edge.label),
        )
        await db.commit()


async def get_edges_by_session(session_id: str) -> list[Edge]:
    edges = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT e.* FROM edges e JOIN nodes n ON e.from_node_id = n.id WHERE n.session_id = ?",
            (session_id,),
        ) as cursor:
            async for row in cursor:
                edges.append(Edge(**dict(row)))
    return edges


async def get_optimization_report(session_id: str) -> tuple[dict, str] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT report_json, business_context FROM optimization_reports WHERE session_id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return json.loads(row["report_json"]), row["business_context"]


async def save_optimization_report(
    session_id: str,
    report: dict,
    business_context: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO optimization_reports
                (session_id, business_context, report_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                business_context = excluded.business_context,
                report_json = excluded.report_json,
                created_at = excluded.created_at
            """,
            (
                session_id,
                business_context,
                json.dumps(report, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def delete_subtree(node_id: str) -> tuple[list[str], list[str]]:
    """Delete all descendants of a node (not the node itself).

    Returns (deleted_node_ids, deleted_edge_ids).
    """
    deleted_node_ids: list[str] = []
    deleted_edge_ids: list[str] = []

    async with aiosqlite.connect(DB_PATH) as db:
        # BFS to collect all descendant node IDs
        queue = [node_id]
        descendant_ids: list[str] = []
        while queue:
            parent = queue.pop(0)
            async with db.execute("SELECT id FROM nodes WHERE parent_id = ?", (parent,)) as cursor:
                async for row in cursor:
                    child_id = row[0]
                    descendant_ids.append(child_id)
                    queue.append(child_id)

        if not descendant_ids:
            return [], []

        # Collect edge IDs that connect from the target node or from any descendant
        all_ids = [node_id] + descendant_ids
        placeholders = ",".join("?" * len(all_ids))
        async with db.execute(
            f"SELECT id FROM edges WHERE from_node_id IN ({placeholders})",
            all_ids,
        ) as cursor:
            async for row in cursor:
                deleted_edge_ids.append(row[0])

        # Delete edges
        if deleted_edge_ids:
            ph = ",".join("?" * len(deleted_edge_ids))
            await db.execute(f"DELETE FROM edges WHERE id IN ({ph})", deleted_edge_ids)

        # Delete descendant nodes
        ph = ",".join("?" * len(descendant_ids))
        await db.execute(f"DELETE FROM nodes WHERE id IN ({ph})", descendant_ids)
        deleted_node_ids = descendant_ids

        # Reset the target node to allow re-exploration
        await db.execute(
            "UPDATE nodes SET status = 'pending', prompt_text = '', transcript = NULL WHERE id = ?",
            (node_id,),
        )

        await db.commit()

    return deleted_node_ids, deleted_edge_ids
