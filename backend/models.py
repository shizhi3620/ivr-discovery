from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    PENDING = "pending"
    CALLING = "calling"
    PARSING = "parsing"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RouteKind(str, Enum):
    HUMAN = "human"
    SELF_SERVICE = "self-service"


class WindowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    VERIFIED = "verified"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


def new_id() -> str:
    return str(uuid.uuid4())


class Target(BaseModel):
    id: str = Field(default_factory=new_id)
    phone_number: str
    business_context: str = ""
    required_routes: list[RouteKind] = Field(
        default_factory=lambda: [RouteKind.HUMAN, RouteKind.SELF_SERVICE]
    )
    total_budget_limit: int = 12
    total_budget_used: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DiscoveryWindow(BaseModel):
    id: str = Field(default_factory=new_id)
    target_id: str
    route: RouteKind
    anchor_date: str
    starts_at: str
    ends_at: str
    budget_limit: int
    budget_used: int = 0
    status: WindowStatus = WindowStatus.PENDING
    frontier: list[str] = Field(default_factory=list)
    unresolved_faults: int = 0
    all_branches_terminal: bool = False
    human_boundary_found: bool = False
    in_window: bool = False
    verified_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    target_id: Optional[str] = None
    discovery_window_id: Optional[str] = None
    run_kind: str = "discovery"
    phone_number: str = ""
    status: SessionStatus = SessionStatus.PENDING
    total_cost: float = 0.0
    counted_calls: int = 0
    planned_route: Optional[RouteKind] = None
    override_reason: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CallAttempt(BaseModel):
    id: str = Field(default_factory=new_id)
    target_id: str
    discovery_window_id: str
    session_id: str
    external_call_id: str
    counted: bool = True
    note: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Node(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str = ""
    parent_id: Optional[str] = None
    dtmf_path: str = ""  # e.g. "1w3" means press 1, wait, press 3
    voice_option: str = ""  # for voice-based IVRs: the phrase to say (e.g. "schedule a pickup")
    prompt_text: str = ""
    status: NodeStatus = NodeStatus.PENDING
    call_id: Optional[str] = None
    cost: float = 0.0
    transcript: Optional[str] = None  # normalized transcript from the call provider
    realtime_verified: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Edge(BaseModel):
    id: str = Field(default_factory=new_id)
    from_node_id: str = ""
    to_node_id: Optional[str] = None
    dtmf_key: str = ""  # e.g. "1", "2", "*", "#"
    label: str = ""  # e.g. "Billing", "Support"


# SQLite schema
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS targets (
    id TEXT PRIMARY KEY,
    phone_number TEXT NOT NULL,
    business_context TEXT NOT NULL DEFAULT '',
    required_routes TEXT NOT NULL,
    total_budget_limit INTEGER NOT NULL DEFAULT 12,
    total_budget_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_targets_phone_number
    ON targets(phone_number, created_at DESC);

CREATE TABLE IF NOT EXISTS discovery_windows (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES targets(id),
    route TEXT NOT NULL,
    anchor_date TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    budget_limit INTEGER NOT NULL,
    budget_used INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    frontier TEXT NOT NULL DEFAULT '[]',
    unresolved_faults INTEGER NOT NULL DEFAULT 0,
    all_branches_terminal INTEGER NOT NULL DEFAULT 0,
    human_boundary_found INTEGER NOT NULL DEFAULT 0,
    in_window INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(target_id, route, anchor_date)
);

CREATE INDEX IF NOT EXISTS idx_discovery_windows_target
    ON discovery_windows(target_id, route, anchor_date DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    target_id TEXT REFERENCES targets(id),
    discovery_window_id TEXT REFERENCES discovery_windows(id),
    run_kind TEXT NOT NULL DEFAULT 'discovery',
    phone_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_cost REAL NOT NULL DEFAULT 0.0,
    counted_calls INTEGER NOT NULL DEFAULT 0,
    planned_route TEXT,
    override_reason TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_attempts (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES targets(id),
    discovery_window_id TEXT NOT NULL REFERENCES discovery_windows(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    external_call_id TEXT NOT NULL,
    counted INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(session_id, external_call_id)
);

CREATE TABLE IF NOT EXISTS budget_increases (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES targets(id),
    discovery_window_id TEXT REFERENCES discovery_windows(id),
    old_target_limit INTEGER NOT NULL,
    new_target_limit INTEGER NOT NULL,
    old_window_limit INTEGER,
    new_window_limit INTEGER,
    operator TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    parent_id TEXT REFERENCES nodes(id),
    dtmf_path TEXT NOT NULL DEFAULT '',
    voice_option TEXT NOT NULL DEFAULT '',
    prompt_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    call_id TEXT,
    cost REAL NOT NULL DEFAULT 0.0,
    transcript TEXT,
    realtime_verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    from_node_id TEXT NOT NULL REFERENCES nodes(id),
    to_node_id TEXT REFERENCES nodes(id),
    dtmf_key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS optimization_reports (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    business_context TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_optimization_reports (
    target_id TEXT PRIMARY KEY REFERENCES targets(id),
    business_context TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
