"""Portable single-session export/import for sharing discovery results.

A bundle is a self-contained JSON snapshot of one session: its target,
discovery windows, session row, nodes, edges, cached optimization reports
and call attempts. Importing a bundle on another machine makes the session
show up in a local app instance (tree + cached report) without placing calls.

This module is intentionally stdlib-only (sqlite3 + json) so the companion
CLI scripts in scripts/ run the same inside or outside the project venv.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BUNDLE_VERSION = 1

# Parent-before-child insert order for foreign-key relationships.
_TABLE_ORDER = (
    "targets",
    "discovery_windows",
    "sessions",
    "nodes",
    "edges",
    "call_attempts",
    "budget_increases",
    "optimization_reports",
    "target_optimization_reports",
)

REQUIRED_TABLES = {"sessions", "nodes", "edges"}


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def export_bundle(db_path: str | Path, session_id: str) -> dict:
    """Build a JSON-serializable snapshot of one session."""
    with _connect(db_path) as conn:
        session_rows = _rows(
            conn, "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if not session_rows:
            raise ValueError(f"session {session_id!r} not found in {db_path}")
        session = session_rows[0]

        target = None
        windows: list[dict] = []
        target_report = None
        budget_increases: list[dict] = []
        if session.get("target_id"):
            target = _rows(
                conn, "SELECT * FROM targets WHERE id = ?", (session["target_id"],)
            )
            target = target[0] if target else None
            if target:
                windows = _rows(
                    conn,
                    "SELECT * FROM discovery_windows WHERE target_id = ?",
                    (target["id"],),
                )
                budget_increases = _rows(
                    conn,
                    "SELECT * FROM budget_increases WHERE target_id = ?",
                    (target["id"],),
                )
                target_report = _rows(
                    conn,
                    "SELECT * FROM target_optimization_reports WHERE target_id = ?",
                    (target["id"],),
                )
                target_report = target_report[0] if target_report else None

        nodes = _rows(conn, "SELECT * FROM nodes WHERE session_id = ?", (session_id,))
        node_ids = [node["id"] for node in nodes]
        edges: list[dict] = []
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            edges = _rows(
                conn,
                f"SELECT * FROM edges WHERE from_node_id IN ({placeholders})",
                tuple(node_ids),
            )
        call_attempts = _rows(
            conn, "SELECT * FROM call_attempts WHERE session_id = ?", (session_id,)
        )
        session_report = _rows(
            conn,
            "SELECT * FROM optimization_reports WHERE session_id = ?",
            (session_id,),
        )
        session_report = session_report[0] if session_report else None

    # report_json is stored as TEXT; embed the parsed object in the bundle.
    for report in (session_report, target_report):
        if report and isinstance(report.get("report_json"), str):
            report["report_json"] = json.loads(report["report_json"])

    return {
        "bundle_version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "target": target,
        "discovery_windows": windows,
        "nodes": nodes,
        "edges": edges,
        "call_attempts": call_attempts,
        "budget_increases": budget_increases,
        "optimization_report": session_report,
        "target_optimization_report": target_report,
    }


def import_bundle(db_path: str | Path, bundle: dict) -> dict:
    """Insert a bundle into a local database.

    Existing rows with the same primary key are left untouched, so importing
    is idempotent and never overwrites local data. The target database must
    already have the app schema (start the backend once to initialise it).
    """
    if not isinstance(bundle, dict) or "session" not in bundle:
        raise ValueError("not a valid session bundle (missing 'session')")

    rows_by_table = {
        "targets": [bundle["target"]] if bundle.get("target") else [],
        "discovery_windows": bundle.get("discovery_windows") or [],
        "sessions": [bundle["session"]],
        "nodes": bundle.get("nodes") or [],
        "edges": bundle.get("edges") or [],
        "call_attempts": bundle.get("call_attempts") or [],
        "budget_increases": bundle.get("budget_increases") or [],
        "optimization_reports": (
            [bundle["optimization_report"]] if bundle.get("optimization_report") else []
        ),
        "target_optimization_reports": (
            [bundle["target_optimization_report"]]
            if bundle.get("target_optimization_report")
            else []
        ),
    }

    summary: dict[str, int] = {}
    with _connect(db_path) as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = REQUIRED_TABLES - existing_tables
        if missing:
            raise ValueError(
                f"{db_path} is missing tables {sorted(missing)}; start the backend "
                "once to initialise the database before importing a bundle"
            )

        for table in _TABLE_ORDER:
            incoming = [row for row in rows_by_table.get(table, []) if row]
            if not incoming:
                summary[table] = 0
                continue
            columns = _table_columns(conn, table)
            inserted = 0
            for row in incoming:
                payload = {k: v for k, v in row.items() if k in columns}
                if "report_json" in payload and not isinstance(payload["report_json"], str):
                    payload["report_json"] = json.dumps(
                        payload["report_json"], ensure_ascii=False
                    )
                col_list = ", ".join(payload)
                placeholders = ", ".join("?" * len(payload))
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                    tuple(payload.values()),
                )
                inserted += cursor.rowcount
            summary[table] = inserted
        conn.commit()

    summary["session_id"] = bundle["session"]["id"]
    return summary
