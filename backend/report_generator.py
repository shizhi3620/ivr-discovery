"""Generate bilingual IVR optimization reports from a discovered session."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import database as db
import transcript_parser
from ai import AIProvider, get_ai_provider
from discovery_windows import verification_failures
from models import SessionStatus, WindowStatus

logger = logging.getLogger(__name__)

REPORT_PROMPT = """You are an IVR experience and operations consultant.

Analyze the supplied IVR discovery session and produce a bilingual optimization
report in Chinese ("zh") and English ("en").

Rules:
1. Use only evidence in the supplied tree, transcripts and business_context.
2. Never invent menu options, business hours or outcomes.
3. If after-hours routing or another path was not actually observed, put it in
   time_routing or unknown_items and mark it as "not yet verified".
4. Preserve English IVR wording in English and Chinese IVR wording in Chinese.
5. Analyze time-based routing as separate routes. For each route, describe the
   caller journey, evidence source and confidence.
6. Recommendations must be concrete, prioritized and tied to observed nodes.
7. Return ONLY valid JSON using exactly this top-level structure:

{
  "zh": {
    "title": "IVR 体验优化报告",
    "executive_summary": "...",
    "time_routing": [
      {
        "window": "21:00 前",
        "behavior": "...",
        "evidence": "...",
        "confidence": "verified|not yet verified",
        "recommendation": "..."
      }
    ],
    "current_flow": [
      {
        "node_id": "...",
        "path": "...",
        "prompt": "...",
        "options": [{"key": "1", "label": "..."}],
        "observation": "..."
      }
    ],
    "issues": [
      {
        "severity": "high|medium|low",
        "node_id": "...",
        "finding": "...",
        "recommendation": "...",
        "expected_impact": "..."
      }
    ],
    "proposed_flow": "...",
    "metrics": ["..."],
    "validation_plan": ["..."],
    "unknown_items": ["..."]
  },
  "en": {
    "title": "IVR Experience Optimization Report",
    "executive_summary": "...",
    "time_routing": [],
    "current_flow": [],
    "issues": [],
    "proposed_flow": "...",
    "metrics": [],
    "validation_plan": [],
    "unknown_items": []
  }
}

Discovery data:
"""


class ReportNotReadyError(RuntimeError):
    """Raised when a discovery session is still running."""


async def generate_optimization_report(
    session_id: str,
    *,
    business_context: str = "",
    force: bool = False,
    provider: AIProvider | None = None,
) -> dict:
    """Generate and persist a bilingual optimization report."""
    session = await db.get_session(session_id)
    if not session:
        raise ValueError("Session not found")

    if session.target_id:
        return await generate_target_optimization_report(
            session.target_id,
            business_context=business_context,
            force=force,
            provider=provider,
        )

    if session.status != SessionStatus.COMPLETED:
        raise ReportNotReadyError(
            "The IVR discovery session must be completed before generating a report"
        )

    if not force:
        cached = await db.get_optimization_report(session_id)
        if cached:
            return cached[0]

    nodes = await db.get_nodes_by_session(session_id)
    edges = await db.get_edges_by_session(session_id)
    context = _build_discovery_context(
        phone_number=session.phone_number,
        nodes=nodes,
        edges=edges,
        business_context=business_context,
    )

    provider = provider or get_ai_provider()
    raw = await provider.complete(
        REPORT_PROMPT + json.dumps(context, ensure_ascii=False),
        # DeepSeek reasoning models may consume most tokens in reasoning_content.
        max_tokens=24000,
        json_mode=False,
    )
    report = _normalize_report(transcript_parser._extract_json_object(raw))
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["phone_number"] = session.phone_number
    report["business_context"] = business_context

    await db.save_optimization_report(
        session_id,
        report,
        business_context=business_context,
    )
    return report


async def generate_target_optimization_report(
    target_id: str,
    *,
    business_context: str = "",
    force: bool = False,
    provider: AIProvider | None = None,
) -> dict:
    """Generate a Target report only after every required route is verified."""
    target = await db.get_target(target_id)
    if target is None:
        raise ValueError("Target not found")

    windows = await db.list_discovery_windows(target.id)
    verified_routes = {
        window.route
        for window in windows
        if window.status == WindowStatus.VERIFIED
    }
    missing_routes = [
        route.value
        for route in target.required_routes
        if route not in verified_routes
    ]
    if missing_routes and not force:
        raise ReportNotReadyError(
            "The IVR target has unverified discovery windows: "
            + ", ".join(missing_routes)
        )

    if not force:
        cached = await db.get_target_optimization_report(target.id)
        if cached and not cached[0].get("draft"):
            return cached[0]

    sessions = await db.get_sessions_by_target(target.id)
    nodes = []
    edges = []
    for session in sessions:
        nodes.extend(await db.get_nodes_by_session(session.id))
        edges.extend(await db.get_edges_by_session(session.id))

    context = _build_discovery_context(
        phone_number=target.phone_number,
        nodes=nodes,
        edges=edges,
        business_context=business_context or target.business_context,
        target_id=target.id,
        required_routes=[route.value for route in target.required_routes],
        windows=windows,
        draft=bool(missing_routes),
    )

    provider = provider or get_ai_provider()
    raw = await provider.complete(
        REPORT_PROMPT + json.dumps(context, ensure_ascii=False),
        max_tokens=24000,
        json_mode=False,
    )
    report = _normalize_report(transcript_parser._extract_json_object(raw))
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["phone_number"] = target.phone_number
    report["target_id"] = target.id
    report["business_context"] = business_context or target.business_context
    report["draft"] = bool(missing_routes)
    report["missing_routes"] = missing_routes

    await db.save_target_optimization_report(
        target.id,
        report,
        business_context=business_context or target.business_context,
    )
    return report


def _build_discovery_context(
    *,
    phone_number: str,
    nodes: list,
    edges: list,
    business_context: str,
    target_id: str | None = None,
    required_routes: list[str] | None = None,
    windows: list | None = None,
    draft: bool = False,
) -> dict:
    edges_by_node: dict[str, list[dict]] = {}
    for edge in edges:
        edges_by_node.setdefault(edge.from_node_id, []).append(
            {"dtmf_key": edge.dtmf_key, "label": edge.label}
        )

    node_payload = []
    transcript_chars = 0
    for node in sorted(nodes, key=lambda item: item.created_at):
        transcript = (node.transcript or "").strip()
        remaining = max(0, 24000 - transcript_chars)
        transcript = transcript[: min(1500, remaining)]
        transcript_chars += len(transcript)
        node_payload.append(
            {
                "node_id": node.id,
                "parent_id": node.parent_id,
                "dtmf_path": node.dtmf_path,
                "voice_option": node.voice_option,
                "status": node.status.value,
                "prompt_text": node.prompt_text,
                "options": edges_by_node.get(node.id, []),
                "transcript": transcript,
            }
        )

    context = {
        "phone_number": phone_number,
        "business_context": business_context,
        "node_count": len(nodes),
        "nodes": node_payload,
    }
    if target_id is not None:
        context.update(
            {
                "target_id": target_id,
                "required_routes": required_routes or [],
                "draft": draft,
                "windows": [
                    {
                        "id": window.id,
                        "route": window.route.value,
                        "starts_at": window.starts_at,
                        "ends_at": window.ends_at,
                        "status": window.status.value,
                        "budget_used": window.budget_used,
                        "budget_limit": window.budget_limit,
                        "verification_failures": verification_failures(window),
                    }
                    for window in (windows or [])
                ],
            }
        )
    return context


def _normalize_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValueError("AI report must be a JSON object")

    normalized = {}
    for language in ("zh", "en"):
        section = report.get(language)
        if not isinstance(section, dict):
            raise ValueError(f"AI report is missing the {language!r} section")
        normalized[language] = {
            "title": str(section.get("title") or (
                "IVR 体验优化报告" if language == "zh" else "IVR Optimization Report"
            )),
            "executive_summary": str(section.get("executive_summary") or ""),
            "time_routing": _list_of_dicts(section.get("time_routing")),
            "current_flow": _list_of_dicts(section.get("current_flow")),
            "issues": _list_of_dicts(section.get("issues")),
            "proposed_flow": str(section.get("proposed_flow") or ""),
            "metrics": _list_of_strings(section.get("metrics")),
            "validation_plan": _list_of_strings(section.get("validation_plan")),
            "unknown_items": _list_of_strings(section.get("unknown_items")),
        }
    return normalized


def _list_of_dicts(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_of_strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


__all__ = [
    "generate_optimization_report",
    "generate_target_optimization_report",
    "ReportNotReadyError",
    "REPORT_PROMPT",
]
