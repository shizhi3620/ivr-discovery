"""Tests for target/window budgets and verification gates."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import database as db
from discovery_windows import (
    authorize_run,
    complete_window_and_verify,
    get_current_window,
    resolve_window,
)
from models import (
    DiscoveryWindow,
    Node,
    NodeStatus,
    RouteKind,
    Session,
    SessionStatus,
    Target,
    WindowStatus,
)

TZ = ZoneInfo("Asia/Shanghai")


def test_resolve_human_window_boundaries():
    nine = resolve_window(datetime(2026, 9, 22, 9, 0, tzinfo=TZ))
    assert nine.route == RouteKind.HUMAN
    assert nine.starts_at.hour == 9
    assert nine.ends_at.hour == 21

    almost = resolve_window(datetime(2026, 9, 22, 20, 59, tzinfo=TZ))
    assert almost.route == RouteKind.HUMAN


def test_resolve_self_service_window_before_and_after_midnight():
    evening = resolve_window(datetime(2026, 9, 22, 21, 0, tzinfo=TZ))
    assert evening.route == RouteKind.SELF_SERVICE
    assert evening.anchor_date == "2026-09-22"
    assert evening.starts_at.hour == 21
    assert evening.ends_at.date().isoformat() == "2026-09-23"
    assert evening.ends_at.hour == 9

    morning = resolve_window(datetime(2026, 9, 23, 8, 59, tzinfo=TZ))
    assert morning.route == RouteKind.SELF_SERVICE
    assert morning.anchor_date == "2026-09-22"
    assert morning.starts_at.date().isoformat() == "2026-09-22"
    assert morning.ends_at.date().isoformat() == "2026-09-23"


@pytest.mark.asyncio
async def test_target_and_window_are_reused_by_anchor():
    target = await db.get_or_create_target(phone_number="4006668800")
    now = datetime(2026, 9, 22, 22, 0, tzinfo=TZ)

    first = await get_current_window(target, now=now)
    second = await get_current_window(target, now=now)

    assert first.id == second.id
    assert first.route == RouteKind.SELF_SERVICE
    assert first.budget_limit == 8


@pytest.mark.asyncio
async def test_resumable_session_skips_probes_and_completed_runs():
    target = await db.get_or_create_target(phone_number="4006668800")
    window = await get_current_window(
        target,
        now=datetime(2026, 9, 22, 22, 0, tzinfo=TZ),
    )
    completed = Session(
        target_id=target.id,
        discovery_window_id=window.id,
        run_kind="discovery",
        phone_number=target.phone_number,
    )
    await db.create_session(completed)
    await db.create_node(
        Node(session_id=completed.id, status=NodeStatus.COMPLETED)
    )
    probe = Session(
        target_id=target.id,
        discovery_window_id=window.id,
        run_kind="probe",
        phone_number=target.phone_number,
    )
    await db.create_session(probe)
    await db.create_node(
        Node(session_id=probe.id, status=NodeStatus.PENDING)
    )
    resumable = Session(
        target_id=target.id,
        discovery_window_id=window.id,
        run_kind="discovery",
        phone_number=target.phone_number,
    )
    await db.create_session(resumable)
    await db.create_node(
        Node(
            session_id=resumable.id,
            dtmf_path="1",
            status=NodeStatus.PENDING,
        )
    )

    found = await db.get_resumable_session_for_window(window.id)

    assert found is not None
    assert found.id == resumable.id


@pytest.mark.asyncio
async def test_counted_call_is_idempotent_and_updates_both_budgets():
    target = Target(phone_number="4006668800", total_budget_limit=12)
    await db.create_target(target)
    window = DiscoveryWindow(
        target_id=target.id,
        route=RouteKind.SELF_SERVICE,
        anchor_date="2026-09-22",
        starts_at="2026-09-22T21:00:00+08:00",
        ends_at="2026-09-23T09:00:00+08:00",
        budget_limit=8,
    )
    await db.create_discovery_window(window)
    session = Session(
        target_id=target.id,
        discovery_window_id=window.id,
        phone_number=target.phone_number,
        status=SessionStatus.RUNNING,
    )
    await db.create_session(session)

    first = await db.record_call_attempt(
        target_id=target.id,
        discovery_window_id=window.id,
        session_id=session.id,
        external_call_id="call-1",
    )
    duplicate = await db.record_call_attempt(
        target_id=target.id,
        discovery_window_id=window.id,
        session_id=session.id,
        external_call_id="call-1",
    )

    assert first is True
    assert duplicate is False
    summary = await db.get_budget_summary(target.id, window.id)
    assert summary["target_used"] == 1
    assert summary["window_used"] == 1
    stored_session = await db.get_session(session.id)
    assert stored_session.counted_calls == 1


@pytest.mark.asyncio
async def test_counted_call_refuses_exhausted_window():
    target = Target(phone_number="4006668800", total_budget_limit=12)
    await db.create_target(target)
    window = DiscoveryWindow(
        target_id=target.id,
        route=RouteKind.HUMAN,
        anchor_date="2026-09-22",
        starts_at="2026-09-22T09:00:00+08:00",
        ends_at="2026-09-22T21:00:00+08:00",
        budget_limit=1,
    )
    await db.create_discovery_window(window)
    session = Session(
        target_id=target.id,
        discovery_window_id=window.id,
        phone_number=target.phone_number,
    )
    await db.create_session(session)

    await db.record_call_attempt(
        target_id=target.id,
        discovery_window_id=window.id,
        session_id=session.id,
        external_call_id="call-1",
    )
    with pytest.raises(db.BudgetExhaustedError):
        await db.record_call_attempt(
            target_id=target.id,
            discovery_window_id=window.id,
            session_id=session.id,
            external_call_id="call-2",
        )


@pytest.mark.asyncio
async def test_budget_increase_requires_monotonic_audited_values():
    target = Target(phone_number="4006668800", total_budget_limit=12)
    await db.create_target(target)
    window = DiscoveryWindow(
        target_id=target.id,
        route=RouteKind.SELF_SERVICE,
        anchor_date="2026-09-22",
        starts_at="2026-09-22T21:00:00+08:00",
        ends_at="2026-09-23T09:00:00+08:00",
        budget_limit=8,
    )
    await db.create_discovery_window(window)

    budget = await db.increase_budget(
        target_id=target.id,
        discovery_window_id=window.id,
        new_target_limit=14,
        new_window_limit=10,
        operator="jim",
        reason="two calls were consumed by the timed-DTMF regression",
    )
    assert budget["target_limit"] == 14
    assert budget["window_limit"] == 10

    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            """
            SELECT old_target_limit, new_target_limit,
                   old_window_limit, new_window_limit, operator
            FROM budget_increases
            """
        ) as cursor:
            rows = await cursor.fetchall()
    assert rows == [(12, 14, 8, 10, "jim")]

    with pytest.raises(ValueError, match="at least one"):
        await db.increase_budget(
            target_id=target.id,
            discovery_window_id=window.id,
            new_target_limit=14,
            new_window_limit=10,
            operator="jim",
            reason="not an increase",
        )

    budget = await db.increase_budget(
        target_id=target.id,
        discovery_window_id=window.id,
        new_target_limit=14,
        new_window_limit=11,
        operator="jim",
        reason="window-only increase",
    )
    assert budget["target_limit"] == 14
    assert budget["window_limit"] == 11


@pytest.mark.asyncio
async def test_authorize_run_enforces_time_and_override():
    target = Target(phone_number="4006668800")
    await db.create_target(target)
    window = DiscoveryWindow(
        target_id=target.id,
        route=RouteKind.SELF_SERVICE,
        anchor_date="2026-09-22",
        starts_at="2026-09-22T21:00:00+08:00",
        ends_at="2026-09-23T09:00:00+08:00",
        budget_limit=8,
    )
    await db.create_discovery_window(window)

    allowed, reason = await authorize_run(
        target_id=target.id,
        window_id=window.id,
        now=datetime(2026, 9, 22, 15, 0, tzinfo=TZ),
    )
    assert allowed is False
    assert "outside" in reason

    allowed, reason = await authorize_run(
        target_id=target.id,
        window_id=window.id,
        now=datetime(2026, 9, 22, 15, 0, tzinfo=TZ),
        override_reason="authorized maintenance test",
    )
    assert allowed is True
    assert "manual override" in reason


@pytest.mark.asyncio
async def test_window_verification_requires_all_gates():
    target = Target(phone_number="4006668800")
    await db.create_target(target)
    window = DiscoveryWindow(
        target_id=target.id,
        route=RouteKind.SELF_SERVICE,
        anchor_date="2026-09-22",
        starts_at="2026-09-22T21:00:00+08:00",
        ends_at="2026-09-23T09:00:00+08:00",
        budget_limit=8,
    )
    await db.create_discovery_window(window)

    verified, reasons = await complete_window_and_verify(
        window.id,
        frontier=["node-2"],
        unresolved_faults=1,
        all_branches_terminal=False,
        human_boundary_found=True,
        in_window=False,
    )
    assert verified.status == WindowStatus.COMPLETED
    assert len(reasons) >= 5

    verified, reasons = await complete_window_and_verify(
        window.id,
        frontier=[],
        unresolved_faults=0,
        all_branches_terminal=True,
        human_boundary_found=False,
        in_window=True,
    )
    assert reasons == []
    assert verified.status == WindowStatus.VERIFIED
    assert verified.verified_at is not None
