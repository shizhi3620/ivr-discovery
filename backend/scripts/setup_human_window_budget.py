"""Create today's human discovery window and raise its budget.

Does not place a call. Creates the 09:00-21:00 human window for the current
Asia/Shanghai day, then records an audited budget increase so a real discovery
run can start inside the window.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from discovery_windows import APP_TIMEZONE, get_current_window, resolve_window

TARGET_PHONE = "4006668800"
NEW_TARGET_LIMIT = 33
NEW_WINDOW_LIMIT = 8
OPERATOR = "jim"
REASON = "探索人工窗口(09:00-21:00)完整IVR流程，补齐人工路由缺失数据"


async def main() -> None:
    await db.init_db()
    target = await db.get_target_by_phone(TARGET_PHONE)
    if target is None:
        raise SystemExit("target not found")

    now = datetime.now(APP_TIMEZONE)
    resolved = resolve_window(now)
    if resolved.route.value != "human":
        raise SystemExit(
            f"refusing: current route is {resolved.route.value}, expected human"
        )

    window = await get_current_window(target, now=now)
    print(
        "window:",
        window.id,
        window.route.value,
        window.anchor_date,
        f"budget={window.budget_used}/{window.budget_limit}",
        f"status={window.status.value}",
    )

    summary = await db.get_budget_summary(target.id, window.id)
    print("before:", summary)

    budget = await db.increase_budget(
        target_id=target.id,
        discovery_window_id=window.id,
        new_target_limit=max(NEW_TARGET_LIMIT, summary["target_limit"]),
        new_window_limit=max(NEW_WINDOW_LIMIT, summary["window_limit"]),
        operator=OPERATOR,
        reason=REASON,
    )
    print("after:", budget)


if __name__ == "__main__":
    asyncio.run(main())
