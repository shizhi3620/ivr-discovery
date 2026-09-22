import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import database as db
from discovery import run_discovery, rediscover_subtree
from models import Session, SessionStatus
from report_generator import ReportNotReadyError, generate_optimization_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="IVR Tree Discovery")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.init_db()
    logger.info("Database initialized")


async def send_json(ws: WebSocket, data: dict):
    try:
        await ws.send_json(data)
    except Exception:
        pass


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    await send_json(websocket, {"type": "connected", "session_id": session_id})

    discovery_task = None
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "start_discovery":
                phone_number = data.get("phone_number", "").strip()
                if not phone_number:
                    await send_json(websocket, {"type": "error", "message": "Phone number required"})
                    continue

                try:
                    # Use a unique session ID per discovery run
                    import uuid
                    run_id = str(uuid.uuid4())
                    session = Session(id=run_id, phone_number=phone_number, status=SessionStatus.RUNNING)
                    await db.create_session(session)
                    await send_json(websocket, {
                        "type": "session_status",
                        "session": {
                            "id": session.id,
                            "phone_number": phone_number,
                            "status": "running",
                            "total_cost": 0.0,
                            "total_nodes": 0,
                            "completed_nodes": 0,
                            "failed_nodes": 0,
                        },
                    })

                    # Cancel any previous discovery
                    if discovery_task and not discovery_task.done():
                        discovery_task.cancel()

                    discovery_task = asyncio.create_task(run_discovery(websocket, phone_number, session))

                except Exception as e:
                    logger.exception("Error starting discovery")
                    await send_json(websocket, {"type": "error", "message": str(e)})

            elif msg_type == "rediscover_subtree":
                node_id = data.get("node_id", "")
                if not node_id:
                    await send_json(websocket, {"type": "error", "message": "node_id required"})
                    continue

                # Cancel any running discovery first
                if discovery_task and not discovery_task.done():
                    discovery_task.cancel()
                    try:
                        await discovery_task
                    except (asyncio.CancelledError, Exception):
                        pass

                discovery_task = asyncio.create_task(rediscover_subtree(websocket, node_id))

            elif msg_type == "cancel":
                if discovery_task and not discovery_task.done():
                    discovery_task.cancel()
                    logger.info("Discovery cancelled by user")
                    await send_json(websocket, {
                        "type": "session_status",
                        "session": {
                            "id": "",
                            "phone_number": "",
                            "status": "failed",
                            "total_cost": 0.0,
                            "total_nodes": 0,
                            "completed_nodes": 0,
                            "failed_nodes": 0,
                        },
                    })

            elif msg_type == "ping":
                await send_json(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
        if discovery_task and not discovery_task.done():
            discovery_task.cancel()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/recover-stuck")
async def recover_stuck_nodes():
    """Find nodes stuck in calling/parsing, fetch their final state from the provider, and update them."""
    import transcript_parser
    from models import NodeStatus
    from providers import get_provider

    recovered = 0
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cursor = await conn.execute(
            "SELECT id, call_id, session_id FROM nodes WHERE status IN ('calling', 'parsing') AND call_id IS NOT NULL"
        )
        stuck = await cursor.fetchall()

    provider = get_provider()
    for node_id, call_id, session_id in stuck:
        try:
            call_result = await provider.get_call(call_id)
            call_status = call_result.status
            transcript = call_result.transcript
            cost = call_result.cost

            if call_status in ("completed", "failed", "busy", "no-answer", "canceled", "error"):
                if transcript and len(transcript.strip()) > 20:
                    parsed = await transcript_parser.parse_transcript(transcript)
                    prompt = parsed.get("prompt_text", transcript[:200])
                else:
                    prompt = f"Call {call_status}" if call_status != "completed" else "No transcript"

                await db.update_node(
                    node_id,
                    status=NodeStatus.COMPLETED if call_status == "completed" else NodeStatus.FAILED,
                    prompt_text=prompt,
                    transcript=transcript,
                    cost=cost,
                )
                recovered += 1
        except Exception as e:
            logger.warning(f"Failed to recover node {node_id}: {e}")

    return {"recovered": recovered, "total_stuck": len(stuck)}


@app.get("/api/sessions/latest")
async def get_latest_session():
    """Return the most recent session with all its nodes and edges."""
    session = await db.get_latest_session()
    if not session:
        return {"session": None, "nodes": [], "edges": []}

    nodes = await db.get_nodes_by_session(session.id)
    edges = await db.get_edges_by_session(session.id)
    return {
        "session": {
            "id": session.id,
            "phone_number": session.phone_number,
            "status": session.status.value if hasattr(session.status, 'value') else session.status,
            "total_cost": session.total_cost,
            "total_nodes": len(nodes),
            "completed_nodes": sum(1 for n in nodes if n.status == "completed"),
            "failed_nodes": sum(1 for n in nodes if n.status == "failed"),
        },
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
    }


@app.get("/api/sessions/{session_id}")
async def get_session_by_id(session_id: str):
    """Return a session with all its nodes and edges."""
    session = await db.get_session(session_id)
    if not session:
        return {"session": None, "nodes": [], "edges": []}

    nodes = await db.get_nodes_by_session(session.id)
    edges = await db.get_edges_by_session(session.id)
    return {
        "session": {
            "id": session.id,
            "phone_number": session.phone_number,
            "status": session.status.value if hasattr(session.status, 'value') else session.status,
            "total_cost": session.total_cost,
            "total_nodes": len(nodes),
            "completed_nodes": sum(1 for n in nodes if n.status == "completed"),
            "failed_nodes": sum(1 for n in nodes if n.status == "failed"),
        },
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
    }


@app.get("/api/nodes/{node_id}")
async def get_node(node_id: str):
    node = await db.get_node(node_id)
    if not node:
        return {"error": "Not found"}
    return node.model_dump()


@app.get("/api/sessions/{session_id}/optimization-report")
async def get_optimization_report(session_id: str):
    """Return the cached bilingual optimization report, if one exists."""
    cached = await db.get_optimization_report(session_id)
    if not cached:
        return {"report": None, "business_context": ""}
    report, business_context = cached
    return {"report": report, "business_context": business_context}


@app.post("/api/sessions/{session_id}/optimization-report")
async def create_optimization_report(session_id: str, payload: dict | None = None):
    """Generate and persist a bilingual optimization report."""
    payload = payload or {}
    business_context = str(payload.get("business_context") or "")
    force = bool(payload.get("force", False))
    try:
        report = await generate_optimization_report(
            session_id,
            business_context=business_context,
            force=force,
        )
    except ReportNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to generate optimization report")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"report": report, "business_context": business_context}


# Serve frontend static files in production (built by Dockerfile)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        file_path = STATIC_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html")
