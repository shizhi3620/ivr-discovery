"""Seed the already-audited manual calls for 4006668800 into the budget model.

This is idempotent: fixed audit session IDs and external call IDs prevent the
same real call from being counted twice.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from discovery_windows import APP_TIMEZONE, get_current_window, resolve_window
from models import RouteKind, Session, SessionStatus

TARGET_PHONE = "4006668800"
AUDITED_CALLS = [
    (
        "audit-4006668800-1",
        "6e55211b-9fd7-4f2a-84ed-2d49f5712f28",
        "2026-09-22T21:40:00+08:00",
        "manual root probe; WiFi disconnect produced silence",
    ),
    (
        "audit-4006668800-2",
        "15716386-42cf-40e0-bb63-c60435cd4c05",
        "2026-09-22T21:51:00+08:00",
        "manual root retry reached GSM; duplicate FreeSWITCH lost channel",
    ),
    (
        "audit-4006668800-3",
        "986a7525-7b5a-43ba-a492-44371a0857ad",
        "2026-09-22T21:59:00+08:00",
        "successful root verification",
    ),
    (
        "audit-4006668800-4",
        "528e177e-32f4-4c70-ba2b-bb0587007d78",
        "2026-09-22T22:06:00+08:00",
        "successful single-key realtime navigation",
    ),
]


async def main() -> None:
    await db.init_db()
    target = await db.get_or_create_target(phone_number=TARGET_PHONE)
    resolved = resolve_window(datetime.fromisoformat(AUDITED_CALLS[0][2]))
    window = await get_current_window(
        target,
        now=datetime.fromisoformat(AUDITED_CALLS[0][2]),
    )
    assert window.route == RouteKind.SELF_SERVICE
    assert window.anchor_date == resolved.anchor_date

    for session_id, external_call_id, occurred_at, note in AUDITED_CALLS:
        if await db.get_session(session_id) is None:
            await db.create_session(
                Session(
                    id=session_id,
                    target_id=target.id,
                    discovery_window_id=window.id,
                    phone_number=TARGET_PHONE,
                    status=SessionStatus.COMPLETED,
                    planned_route=window.route,
                    started_at=occurred_at,
                    ended_at=occurred_at,
                )
            )
        await db.record_call_attempt(
            target_id=target.id,
            discovery_window_id=window.id,
            session_id=session_id,
            external_call_id=external_call_id,
            counted=True,
            note=note,
        )

    summary = await db.get_budget_summary(target.id, window.id)
    print(
        "seeded",
        TARGET_PHONE,
        f"self-service={summary['window_used']}/{summary['window_limit']}",
        f"total={summary['target_used']}/{summary['target_limit']}",
    )


if __name__ == "__main__":
    asyncio.run(main())
