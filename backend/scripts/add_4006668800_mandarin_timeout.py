"""Add the observed Mandarin no-input timeout path to the discovery tree.

Does not place a call. It records evidence that was already captured on real
calls to Apple 4006668800:

* ``1w1`` (Mandarin support menu) was reached inside the human window and, when
  no key was pressed, played the full timeout sequence before hanging up.
  That transcript survived in node ``1w1`` of session ``8aef3700``.
* A dedicated probe call widened the observation window so the *second*
  reminder and the goodbye line were captured end to end.

The timeout is a first-class, terminal branch of the Mandarin menu: press
nothing and the IVR replays the menu, plays two "press any key to continue"
reminders, then says goodbye and hangs up. It is modelled as a child node of
``1w1`` reached by the synthetic ``no-input`` edge, mirroring how the existing
``(human/queue)`` and ``(cycle)`` markers flag special nodes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from models import Edge, Node, NodeStatus

TARGET_PHONE = "4006668800"
# The frontend session the timeout branch should appear under.
SESSION_ID = "8aef3700-8508-4947-b2a2-85c50c7a9197"
# Parent menu: the Mandarin support menu.
PARENT_PATH = "1w1"
# Synthetic child path; ``no-input`` is never replayed as a DTMF key.
TIMEOUT_PATH = "1w1-timeout"
TIMEOUT_KEY = "no-input"
TIMEOUT_LABEL = "无按键超时 / No input timeout"
TIMEOUT_CALL_ID = "a53b4d63-2fa1-4aa5-9bdf-37b86deaac9e"

TIMEOUT_PROMPT = (
    "(timeout) 无按键超时 / No-input timeout: 菜单重播 → 两轮「按任意键继续」提醒 "
    "→ 感谢致电Apple，再见 → 挂断"
)

# Verbatim realtime-ASR transcript of the full timeout call, starting at the
# privacy prompt and ending at the goodbye line.
TIMEOUT_TRANSCRIPT = """感谢您致电Apple。
为了给您提供最好的服务。
按照Apple隐私政策的规定，与本次通话相关的部分有限个人信息。
可能会在中国大陆境外存储和处理。
如果您同意，请按1。
如需结束本次通话。
请按2。
感谢您致电Apple。普通话按1。
English, 您的通话将会被录音，已作为评估和培训客服人员。
即改进客服中心技术质量之用。
请稍等。
我在查看您的电话号码，如需咨询账单或资费相关问题。
请按1如需从Apple官网购买新产品或咨询已有在线订单。
按2、请账户或登录问题请按3、技术支持或产品维修请。
请按4、任何其他问题。
请按5。
我没有检测到按键输入，如需咨询账单或资费相关问题。
请按1如需从Apple官网购买新产品或咨询已有在线订单。
请按2、账户或登录问题，请按3、技术支持或产品维修。
请按4、任何其他问题。
请按5。
很抱歉，如果您还在线上，我听不见您的声音。
若要继续，请按任意键。
否则，我将需要结束这通话。
非常抱歉，仍然听不见任何声音。
若要继续，请按任意键。
否则，我将需要结束这通话。
感谢您致电Apple再。"""


async def main() -> None:
    await db.init_db()
    session = await db.get_session(SESSION_ID)
    if session is None:
        raise SystemExit(f"session {SESSION_ID} not found")

    nodes = await db.get_nodes_by_session(SESSION_ID)
    by_path = {node.dtmf_path: node for node in nodes}
    parent = by_path.get(PARENT_PATH)
    if parent is None:
        raise SystemExit(f"parent node {PARENT_PATH} not found")

    edges = await db.get_edges_by_session(SESSION_ID)
    existing_edge = next(
        (
            edge
            for edge in edges
            if edge.from_node_id == parent.id and edge.dtmf_key == TIMEOUT_KEY
        ),
        None,
    )

    child = by_path.get(TIMEOUT_PATH)
    if child is None:
        child = Node(
            session_id=SESSION_ID,
            parent_id=parent.id,
            dtmf_path=TIMEOUT_PATH,
            status=NodeStatus.COMPLETED,
            prompt_text=TIMEOUT_PROMPT,
            transcript=TIMEOUT_TRANSCRIPT,
            call_id=TIMEOUT_CALL_ID,
            realtime_verified=True,
        )
        await db.create_node(child)
        created = "created"
    else:
        await db.update_node(
            child.id,
            parent_id=parent.id,
            status=NodeStatus.COMPLETED,
            prompt_text=TIMEOUT_PROMPT,
            transcript=TIMEOUT_TRANSCRIPT,
            call_id=TIMEOUT_CALL_ID,
            realtime_verified=True,
        )
        created = "updated"

    if existing_edge is None:
        await db.create_edge(
            Edge(
                from_node_id=parent.id,
                to_node_id=child.id,
                dtmf_key=TIMEOUT_KEY,
                label=TIMEOUT_LABEL,
            )
        )
        edge_state = "created"
    else:
        edge_state = "kept"

    print(
        f"timeout node {created}: path={TIMEOUT_PATH} id={child.id}\n"
        f"edge {edge_state}: {PARENT_PATH} --{TIMEOUT_KEY}--> {TIMEOUT_PATH}"
    )


if __name__ == "__main__":
    asyncio.run(main())
