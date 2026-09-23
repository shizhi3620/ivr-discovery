from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from models import (
    SCHEMA_SQL,
    CallAttempt,
    DiscoveryWindow,
    Edge,
    Node,
    RouteKind,
    Session,
    Target,
)

DB_PATH = "ivr_discovery.db"


class BudgetExhaustedError(RuntimeError):
    """Raised when a counted real call would exceed a configured budget."""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await _ensure_column(db, "sessions", "target_id", "TEXT REFERENCES targets(id)")
        await _ensure_column(
            db,
            "sessions",
            "run_kind",
            "TEXT NOT NULL DEFAULT 'discovery'",
        )
        await _ensure_column(
            db,
            "sessions",
            "discovery_window_id",
            "TEXT REFERENCES discovery_windows(id)",
        )
        await _ensure_column(db, "sessions", "counted_calls", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "sessions", "planned_route", "TEXT")
        await _ensure_column(db, "sessions", "override_reason", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(db, "sessions", "started_at", "TEXT")
        await _ensure_column(db, "sessions", "ended_at", "TEXT")
        await _ensure_column(
            db,
            "nodes",
            "realtime_verified",
            "INTEGER NOT NULL DEFAULT 0",
        )
        await db.commit()


async def _ensure_column(db, table: str, column: str, definition: str) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def create_target(target: Target) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO targets
                (id, phone_number, business_context, required_routes,
                 total_budget_limit, total_budget_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target.id,
                target.phone_number,
                target.business_context,
                json.dumps([route.value for route in target.required_routes]),
                target.total_budget_limit,
                target.total_budget_used,
                target.created_at,
            ),
        )
        await db.commit()


async def get_target(target_id: str) -> Target | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM targets WHERE id = ?", (target_id,)) as cursor:
            row = await cursor.fetchone()
            return _target_from_row(row) if row else None


async def get_target_by_phone(phone_number: str) -> Target | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM targets WHERE phone_number = ? ORDER BY created_at DESC LIMIT 1",
            (phone_number,),
        ) as cursor:
            row = await cursor.fetchone()
            return _target_from_row(row) if row else None


async def get_or_create_target(
    *,
    phone_number: str,
    business_context: str = "",
    required_routes: list[RouteKind] | None = None,
    total_budget_limit: int = 12,
) -> Target:
    existing = await get_target_by_phone(phone_number)
    if existing is not None:
        return existing
    target = Target(
        phone_number=phone_number,
        business_context=business_context,
        required_routes=required_routes
        or [RouteKind.HUMAN, RouteKind.SELF_SERVICE],
        total_budget_limit=total_budget_limit,
    )
    await create_target(target)
    return target


def _target_from_row(row: aiosqlite.Row) -> Target:
    payload = dict(row)
    payload["required_routes"] = [
        RouteKind(route) for route in json.loads(payload["required_routes"])
    ]
    return Target(**payload)


async def create_discovery_window(window: DiscoveryWindow) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO discovery_windows
                (id, target_id, route, anchor_date, starts_at, ends_at,
                 budget_limit, budget_used, status, frontier, unresolved_faults,
                 all_branches_terminal, human_boundary_found, in_window,
                 verified_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _discovery_window_values(window),
        )
        await db.commit()


async def get_or_create_discovery_window(
    *,
    target_id: str,
    route: RouteKind,
    anchor_date: str,
    starts_at: str,
    ends_at: str,
    budget_limit: int,
) -> DiscoveryWindow:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM discovery_windows
            WHERE target_id = ? AND route = ? AND anchor_date = ?
            """,
            (target_id, route.value, anchor_date),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return _discovery_window_from_row(row)

        window = DiscoveryWindow(
            target_id=target_id,
            route=route,
            anchor_date=anchor_date,
            starts_at=starts_at,
            ends_at=ends_at,
            budget_limit=budget_limit,
        )
        await db.execute(
            """
            INSERT INTO discovery_windows
                (id, target_id, route, anchor_date, starts_at, ends_at,
                 budget_limit, budget_used, status, frontier, unresolved_faults,
                 all_branches_terminal, human_boundary_found, in_window,
                 verified_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _discovery_window_values(window),
        )
        await db.commit()
        return window


async def get_discovery_window(window_id: str) -> DiscoveryWindow | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM discovery_windows WHERE id = ?",
            (window_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return _discovery_window_from_row(row) if row else None


async def list_discovery_windows(target_id: str) -> list[DiscoveryWindow]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM discovery_windows
            WHERE target_id = ?
            ORDER BY starts_at DESC
            """,
            (target_id,),
        ) as cursor:
            return [
                _discovery_window_from_row(row)
                async for row in cursor
            ]


async def update_discovery_window(window_id: str, **kwargs) -> None:
    if not kwargs:
        return
    processed = {}
    for key, value in kwargs.items():
        if hasattr(value, "value"):
            processed[key] = value.value
        elif isinstance(value, (list, dict)):
            processed[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            processed[key] = int(value)
        else:
            processed[key] = value
    processed["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{key} = ?" for key in processed)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE discovery_windows SET {sets} WHERE id = ?",
            (*processed.values(), window_id),
        )
        await db.commit()


def _discovery_window_values(window: DiscoveryWindow) -> tuple:
    return (
        window.id,
        window.target_id,
        window.route.value,
        window.anchor_date,
        window.starts_at,
        window.ends_at,
        window.budget_limit,
        window.budget_used,
        window.status.value,
        json.dumps(window.frontier, ensure_ascii=False),
        window.unresolved_faults,
        int(window.all_branches_terminal),
        int(window.human_boundary_found),
        int(window.in_window),
        window.verified_at,
        window.created_at,
        window.updated_at,
    )


def _discovery_window_from_row(row: aiosqlite.Row) -> DiscoveryWindow:
    payload = dict(row)
    payload["frontier"] = json.loads(payload["frontier"] or "[]")
    payload["all_branches_terminal"] = bool(payload["all_branches_terminal"])
    payload["human_boundary_found"] = bool(payload["human_boundary_found"])
    payload["in_window"] = bool(payload["in_window"])
    return DiscoveryWindow(**payload)


async def create_session(session: Session):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO sessions
                (id, target_id, discovery_window_id, run_kind, phone_number,
                 status, total_cost, counted_calls, planned_route,
                 override_reason, started_at, ended_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.target_id,
                session.discovery_window_id,
                session.run_kind,
                session.phone_number,
                session.status.value,
                session.total_cost,
                session.counted_calls,
                session.planned_route.value if session.planned_route else None,
                session.override_reason,
                session.started_at,
                session.ended_at,
                session.created_at,
            ),
        )
        await db.commit()


async def get_sessions_by_target(target_id: str) -> list[Session]:
    sessions: list[Session] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE target_id = ? ORDER BY created_at",
            (target_id,),
        ) as cursor:
            async for row in cursor:
                sessions.append(Session(**dict(row)))
    return sessions


async def get_sessions_by_window(window_id: str) -> list[Session]:
    sessions: list[Session] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE discovery_window_id = ? ORDER BY created_at",
            (window_id,),
        ) as cursor:
            async for row in cursor:
                sessions.append(Session(**dict(row)))
    return sessions


async def get_latest_session_for_window(window_id: str) -> Session | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM sessions
            WHERE discovery_window_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (window_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return Session(**dict(row)) if row else None


async def get_resumable_session_for_window(window_id: str) -> Session | None:
    """Return an unfinished discovery run that still owns pending work."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT DISTINCT s.*
            FROM sessions s
            JOIN nodes n ON n.session_id = s.id
            WHERE s.discovery_window_id = ?
              AND s.run_kind = 'discovery'
              AND n.status IN ('pending', 'calling', 'parsing')
            ORDER BY s.created_at DESC
            LIMIT 1
            """,
            (window_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return Session(**dict(row)) if row else None


async def session_has_pending_work(session_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1 FROM nodes
            WHERE session_id = ? AND status IN ('pending', 'calling', 'parsing')
            LIMIT 1
            """,
            (session_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def record_call_attempt(
    *,
    target_id: str,
    discovery_window_id: str,
    session_id: str,
    external_call_id: str,
    counted: bool = True,
    note: str = "",
) -> bool:
    """Persist one external call attempt and update cumulative budgets once.

    Returns True when a new attempt was inserted and False for an idempotent
    duplicate. Counted attempts enforce both the window and target limits.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT 1 FROM call_attempts
            WHERE session_id = ? AND external_call_id = ?
            """,
            (session_id, external_call_id),
        ) as cursor:
            if await cursor.fetchone():
                await db.rollback()
                return False

        if counted:
            async with db.execute(
                """
                SELECT w.budget_limit, w.budget_used,
                       t.total_budget_limit, t.total_budget_used
                FROM discovery_windows w
                JOIN targets t ON t.id = w.target_id
                WHERE w.id = ? AND t.id = ?
                """,
                (discovery_window_id, target_id),
            ) as cursor:
                budget = await cursor.fetchone()
            if budget is None:
                await db.rollback()
                raise ValueError("Discovery window or target not found")
            window_limit, window_used, total_limit, total_used = budget
            if window_used >= window_limit:
                await db.rollback()
                raise BudgetExhaustedError("Discovery window budget exhausted")
            if total_used >= total_limit:
                await db.rollback()
                raise BudgetExhaustedError("Target budget exhausted")

        attempt = CallAttempt(
            target_id=target_id,
            discovery_window_id=discovery_window_id,
            session_id=session_id,
            external_call_id=external_call_id,
            counted=counted,
            note=note,
        )
        await db.execute(
            """
            INSERT INTO call_attempts
                (id, target_id, discovery_window_id, session_id,
                 external_call_id, counted, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.id,
                attempt.target_id,
                attempt.discovery_window_id,
                attempt.session_id,
                attempt.external_call_id,
                int(attempt.counted),
                attempt.note,
                attempt.created_at,
            ),
        )
        if counted:
            await db.execute(
                """
                UPDATE discovery_windows
                SET budget_used = budget_used + 1, updated_at = ?
                WHERE id = ?
                """,
                (attempt.created_at, discovery_window_id),
            )
            await db.execute(
                """
                UPDATE targets
                SET total_budget_used = total_budget_used + 1
                WHERE id = ?
                """,
                (target_id,),
            )
            await db.execute(
                """
                UPDATE sessions
                SET counted_calls = counted_calls + 1
                WHERE id = ?
                """,
                (session_id,),
            )
        await db.commit()
        return True


async def get_budget_summary(target_id: str, window_id: str | None = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT total_budget_limit AS target_limit,
                   total_budget_used AS target_used
            FROM targets WHERE id = ?
            """,
            (target_id,),
        ) as cursor:
            target = await cursor.fetchone()
        if target is None:
            raise ValueError("Target not found")

        payload = {
            "target_limit": target["target_limit"],
            "target_used": target["target_used"],
            "target_remaining": target["target_limit"] - target["target_used"],
        }
        if window_id:
            async with db.execute(
                """
                SELECT budget_limit, budget_used FROM discovery_windows
                WHERE id = ? AND target_id = ?
                """,
                (window_id, target_id),
            ) as cursor:
                window = await cursor.fetchone()
            if window is None:
                raise ValueError("Discovery window not found")
            payload.update(
                {
                    "window_limit": window["budget_limit"],
                    "window_used": window["budget_used"],
                    "window_remaining": window["budget_limit"] - window["budget_used"],
                }
            )
        return payload


async def increase_budget(
    *,
    target_id: str,
    discovery_window_id: str | None,
    new_target_limit: int,
    new_window_limit: int | None,
    operator: str,
    reason: str,
) -> dict:
    """Increase cumulative limits with a mandatory audit record."""
    if not operator.strip() or not reason.strip():
        raise ValueError("operator and reason are required for budget increase")
    if new_target_limit < 1:
        raise ValueError("new target limit must be positive")
    if new_window_limit is not None and new_window_limit < 1:
        raise ValueError("new window limit must be positive")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT total_budget_limit FROM targets WHERE id = ?",
            (target_id,),
        ) as cursor:
            target = await cursor.fetchone()
        if target is None:
            await db.rollback()
            raise ValueError("Target not found")
        old_target_limit = target["total_budget_limit"]
        if new_target_limit < old_target_limit:
            await db.rollback()
            raise ValueError("new target limit cannot be lower than current limit")

        old_window_limit = None
        if discovery_window_id is not None:
            async with db.execute(
                """
                SELECT budget_limit FROM discovery_windows
                WHERE id = ? AND target_id = ?
                """,
                (discovery_window_id, target_id),
            ) as cursor:
                window = await cursor.fetchone()
            if window is None:
                await db.rollback()
                raise ValueError("Discovery window not found")
            old_window_limit = window["budget_limit"]
            if new_window_limit is None:
                await db.rollback()
                raise ValueError("new window limit is required")
            if new_window_limit < old_window_limit:
                await db.rollback()
                raise ValueError(
                    "new window limit cannot be lower than current limit"
                )

        target_increased = new_target_limit > old_target_limit
        window_increased = (
            new_window_limit is not None
            and old_window_limit is not None
            and new_window_limit > old_window_limit
        )
        if not target_increased and not window_increased:
            await db.rollback()
            raise ValueError("at least one budget limit must increase")

        created_at = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE targets SET total_budget_limit = ? WHERE id = ?",
            (new_target_limit, target_id),
        )
        if discovery_window_id is not None:
            await db.execute(
                """
                UPDATE discovery_windows
                SET budget_limit = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_window_limit, created_at, discovery_window_id),
            )
        await db.execute(
            """
            INSERT INTO budget_increases
                (id, target_id, discovery_window_id, old_target_limit,
                 new_target_limit, old_window_limit, new_window_limit,
                 operator, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                target_id,
                discovery_window_id,
                old_target_limit,
                new_target_limit,
                old_window_limit,
                new_window_limit,
                operator.strip(),
                reason.strip(),
                created_at,
            ),
        )
        await db.commit()
        return await get_budget_summary(target_id, discovery_window_id)


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
            """
            INSERT INTO nodes
                (id, session_id, parent_id, dtmf_path, voice_option,
                 prompt_text, status, call_id, cost, transcript,
                 realtime_verified, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id,
                node.session_id,
                node.parent_id,
                node.dtmf_path,
                node.voice_option,
                node.prompt_text,
                node.status.value,
                node.call_id,
                node.cost,
                node.transcript,
                int(node.realtime_verified),
                node.created_at,
            ),
        )
        await db.commit()


async def update_node(node_id: str, **kwargs):
    if not kwargs:
        return
    processed = {}
    for k, v in kwargs.items():
        if hasattr(v, "value"):
            processed[k] = v.value
        elif isinstance(v, bool):
            processed[k] = int(v)
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


async def get_target_optimization_report(target_id: str) -> tuple[dict, str] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT report_json, business_context
            FROM target_optimization_reports
            WHERE target_id = ?
            """,
            (target_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return json.loads(row["report_json"]), row["business_context"]


async def save_target_optimization_report(
    target_id: str,
    report: dict,
    business_context: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO target_optimization_reports
                (target_id, business_context, report_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                business_context = excluded.business_context,
                report_json = excluded.report_json,
                created_at = excluded.created_at
            """,
            (
                target_id,
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
            """
            UPDATE nodes
            SET status = 'pending', prompt_text = '', transcript = NULL,
                realtime_verified = 0
            WHERE id = ?
            """,
            (node_id,),
        )

        await db.commit()

    return deleted_node_ids, deleted_edge_ids
