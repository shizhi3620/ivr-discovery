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


def new_id() -> str:
    return str(uuid.uuid4())


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    phone_number: str = ""
    status: SessionStatus = SessionStatus.PENDING
    total_cost: float = 0.0
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
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Edge(BaseModel):
    id: str = Field(default_factory=new_id)
    from_node_id: str = ""
    to_node_id: Optional[str] = None
    dtmf_key: str = ""  # e.g. "1", "2", "*", "#"
    label: str = ""  # e.g. "Billing", "Support"


# SQLite schema
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    phone_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_cost REAL NOT NULL DEFAULT 0.0,
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
"""
