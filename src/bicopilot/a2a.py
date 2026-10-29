"""Agent-to-Agent (A2A) surface.

Other agents discover this copilot through an Agent Card at /.well-known/agent.json and talk to
it with JSON-RPC 2.0 `message/send` (A2A protocol shape: message -> task with status + artifacts).
`agents/reporting_agent.py` is a second agent that uses exactly this surface to compile a report.
"""

from __future__ import annotations

import uuid
from typing import Any

from .config import Settings


def agent_card(settings: Settings) -> dict[str, Any]:
    return {
        "name": settings.agent_name,
        "description": "Intelligent BI copilot: answers natural-language questions about sales data with governed SQL, "
        "returns the answer, the SQL, the rows and a chart spec.",
        "url": f"{settings.public_url.rstrip('/')}/a2a",
        "version": "0.1.0",
        "protocolVersion": "0.2",
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "ask-data",
                "name": "Ask the sales warehouse",
                "description": "Revenue, gross margin, units, orders, AOV, active customers, discount, cancellation and return rates "
                "by region/country/segment/industry/tier/customer/product/channel/rep and by fiscal year/quarter/month. "
                "Returns answer text plus a JSON part with sql, columns, rows, chart.",
                "tags": ["bi", "sql", "analytics", "rag"],
                "examples": [
                    "revenue by region FY2026",
                    "top 5 customers by gross margin last quarter",
                    "monthly revenue trend in APAC",
                ],
            }
        ],
    }


def extract_text(message: dict[str, Any]) -> str:
    parts = message.get("parts", [])
    texts = [p.get("text", "") for p in parts if p.get("kind", p.get("type")) == "text"]
    return "\n".join(t for t in texts if t).strip()


def task_from_result(
    result: dict[str, Any], task_id: str | None = None, context_id: str | None = None
) -> dict[str, Any]:
    state = {"answered": "completed", "clarify": "input-required", "error": "failed"}.get(result["status"], "completed")
    data = {
        "sql": result.get("sql"),
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "row_count": result.get("row_count", 0),
        "chart": (result.get("analysis") or {}).get("chart"),
        "plan": result.get("plan"),
        "clarification": result.get("clarification"),
        "run_id": result["id"],
    }
    return {
        "id": task_id or str(uuid.uuid4()),
        "contextId": context_id or str(uuid.uuid4()),
        "kind": "task",
        "status": {
            "state": state,
            "message": {
                "kind": "message",
                "role": "agent",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": result["answer"]}],
            },
        },
        "artifacts": [
            {
                "artifactId": str(uuid.uuid4()),
                "name": "answer",
                "parts": [{"kind": "text", "text": result["answer"]}, {"kind": "data", "data": data}],
            }
        ],
    }


def jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
