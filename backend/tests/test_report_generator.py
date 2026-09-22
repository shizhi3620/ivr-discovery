"""Tests for bilingual optimization report generation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import database as db
from models import Edge, Node, NodeStatus, Session
from report_generator import ReportNotReadyError, generate_optimization_report


def _report_payload() -> dict:
    section = {
        "title": "Report",
        "executive_summary": "Summary",
        "time_routing": [
            {
                "window": "before 21:00",
                "behavior": "human agent",
                "evidence": "business context",
                "confidence": "not yet verified",
                "recommendation": "verify",
            }
        ],
        "current_flow": [],
        "issues": [],
        "proposed_flow": "Proposed flow",
        "metrics": ["consent completion rate"],
        "validation_plan": ["run a controlled call"],
        "unknown_items": ["after-hours menu"],
    }
    return {"zh": section, "en": section}


@pytest.mark.asyncio
async def test_generates_and_caches_bilingual_report():
    session = Session(phone_number="4006668800", status="completed")
    await db.create_session(session)
    root = Node(
        session_id=session.id,
        prompt_text="隐私与录音告知",
        status=NodeStatus.COMPLETED,
        transcript="按1同意，按2结束。",
    )
    await db.create_node(root)
    child = Node(
        session_id=session.id,
        parent_id=root.id,
        dtmf_path="1",
        prompt_text="普通话按1",
        status=NodeStatus.COMPLETED,
    )
    await db.create_node(child)
    await db.create_edge(
        Edge(
            from_node_id=root.id,
            to_node_id=child.id,
            dtmf_key="1",
            label="同意",
        )
    )

    provider = AsyncMock()
    provider.capabilities.json_mode = True
    provider.complete.return_value = json.dumps(_report_payload(), ensure_ascii=False)

    report = await generate_optimization_report(
        session.id,
        business_context="21:00 前人工坐席，21:00 后 IVR 自助。",
        provider=provider,
    )

    assert report["zh"]["title"] == "Report"
    assert report["en"]["time_routing"][0]["window"] == "before 21:00"
    assert report["phone_number"] == "4006668800"
    assert "21:00" in report["business_context"]

    cached = await generate_optimization_report(
        session.id,
        provider=AsyncMock(side_effect=AssertionError("should not call AI")),
    )
    assert cached["zh"]["executive_summary"] == "Summary"


@pytest.mark.asyncio
async def test_rejects_report_before_discovery_completes():
    session = Session(phone_number="4006668800", status="running")
    await db.create_session(session)

    with pytest.raises(ReportNotReadyError):
        await generate_optimization_report(session.id)
