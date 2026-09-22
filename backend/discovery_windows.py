"""Target discovery-window policy, budget gate and verification rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import database as db
from models import (
    DiscoveryWindow,
    RouteKind,
    Target,
    WindowStatus,
)

APP_TIMEZONE = ZoneInfo("Asia/Shanghai")
HUMAN_START = time(9, 0)
HUMAN_END = time(21, 0)
WINDOW_BUDGET_LIMITS = {
    RouteKind.HUMAN: 4,
    RouteKind.SELF_SERVICE: 8,
}


@dataclass(frozen=True)
class ResolvedWindow:
    route: RouteKind
    anchor_date: str
    starts_at: datetime
    ends_at: datetime


def resolve_window(now: datetime) -> ResolvedWindow:
    """Resolve the active route and its bounded Asia/Shanghai window."""
    local = _localize(now)
    current = local.time()
    if HUMAN_START <= current < HUMAN_END:
        start = datetime.combine(local.date(), HUMAN_START, APP_TIMEZONE)
        end = datetime.combine(local.date(), HUMAN_END, APP_TIMEZONE)
        return ResolvedWindow(RouteKind.HUMAN, local.date().isoformat(), start, end)

    if current >= HUMAN_END:
        anchor = local.date()
        start = datetime.combine(anchor, HUMAN_END, APP_TIMEZONE)
        end = datetime.combine(anchor + timedelta(days=1), HUMAN_START, APP_TIMEZONE)
        return ResolvedWindow(
            RouteKind.SELF_SERVICE,
            anchor.isoformat(),
            start,
            end,
        )

    anchor = local.date() - timedelta(days=1)
    start = datetime.combine(anchor, HUMAN_END, APP_TIMEZONE)
    end = datetime.combine(local.date(), HUMAN_START, APP_TIMEZONE)
    return ResolvedWindow(
        RouteKind.SELF_SERVICE,
        anchor.isoformat(),
        start,
        end,
    )


def _localize(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=APP_TIMEZONE)
    return now.astimezone(APP_TIMEZONE)


async def get_current_window(
    target: Target,
    *,
    now: datetime,
) -> DiscoveryWindow:
    resolved = resolve_window(now)
    return await db.get_or_create_discovery_window(
        target_id=target.id,
        route=resolved.route,
        anchor_date=resolved.anchor_date,
        starts_at=resolved.starts_at.isoformat(),
        ends_at=resolved.ends_at.isoformat(),
        budget_limit=WINDOW_BUDGET_LIMITS[resolved.route],
    )


async def authorize_run(
    *,
    target_id: str,
    window_id: str,
    now: datetime,
    override_reason: str = "",
) -> tuple[bool, str]:
    """Check whether a real call may start in the requested window."""
    window = await db.get_discovery_window(window_id)
    if window is None or window.target_id != target_id:
        return False, "discovery window not found"

    local = _localize(now)
    starts = datetime.fromisoformat(window.starts_at)
    ends = datetime.fromisoformat(window.ends_at)
    in_window = starts <= local < ends
    if not in_window and not override_reason.strip():
        return False, "current time is outside the selected discovery window"
    if not in_window:
        return True, f"manual override: {override_reason.strip()}"

    budget = await db.get_budget_summary(target_id, window_id)
    if budget["window_remaining"] <= 0:
        return False, "discovery window budget exhausted"
    if budget["target_remaining"] <= 0:
        return False, "target budget exhausted"
    return True, "in window"


async def complete_window_and_verify(
    window_id: str,
    *,
    frontier: list[str],
    unresolved_faults: int,
    all_branches_terminal: bool,
    human_boundary_found: bool,
    in_window: bool,
) -> tuple[DiscoveryWindow, list[str]]:
    """Mark a run complete and verify the window using strict gates."""
    await db.update_discovery_window(
        window_id,
        status=WindowStatus.COMPLETED,
        frontier=frontier,
        unresolved_faults=unresolved_faults,
        all_branches_terminal=all_branches_terminal,
        human_boundary_found=human_boundary_found,
        in_window=in_window,
        verified_at=None,
    )
    window = await db.get_discovery_window(window_id)
    if window is None:
        raise ValueError("Discovery window not found")

    reasons = verification_failures(window)
    if reasons:
        return window, reasons

    verified_at = datetime.now(APP_TIMEZONE).isoformat()
    await db.update_discovery_window(
        window_id,
        status=WindowStatus.VERIFIED,
        verified_at=verified_at,
    )
    verified = await db.get_discovery_window(window_id)
    if verified is None:
        raise ValueError("Discovery window not found")
    return verified, []


def verification_failures(window: DiscoveryWindow) -> list[str]:
    reasons: list[str] = []
    if window.status != WindowStatus.COMPLETED:
        reasons.append("window run is not completed")
    if not window.in_window:
        reasons.append("window was not executed at the correct local time")
    if window.unresolved_faults > 0:
        reasons.append("window has unresolved realtime ASR faults")
    if window.budget_used >= window.budget_limit:
        reasons.append("window call budget is exhausted")
    if window.frontier:
        reasons.append("window still has unexplored frontier")
    if not window.all_branches_terminal:
        reasons.append("not all branches have a terminal state")
    if window.route == RouteKind.SELF_SERVICE and window.human_boundary_found:
        reasons.append("self-service window reached a human boundary")
    return reasons


__all__ = [
    "APP_TIMEZONE",
    "WINDOW_BUDGET_LIMITS",
    "ResolvedWindow",
    "authorize_run",
    "complete_window_and_verify",
    "get_current_window",
    "resolve_window",
    "verification_failures",
]
