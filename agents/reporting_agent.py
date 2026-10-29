"""A second agent that collaborates with bi-copilot over A2A.

It discovers the copilot from its Agent Card, sends several questions as A2A `message/send`
tasks, and compiles the answers into a Markdown briefing. It has no database access of its own:
everything it knows about the numbers comes from the copilot's artifacts.

    python agents/reporting_agent.py --base http://127.0.0.1:8080 --out report.md
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date

import httpx

DEFAULT_QUESTIONS = [
    "revenue by region this fiscal year",
    "revenue by region year over year FY2026",
    "gross margin % by product category this fiscal year",
    "top 5 customers by revenue last quarter",
    "cancellation rate by channel",
    "orders this quarter vs last quarter",
]


def discover(client: httpx.Client, base: str) -> dict:
    card = client.get(f"{base}/.well-known/agent.json").json()
    if not any(s["id"] == "ask-data" for s in card.get("skills", [])):
        raise SystemExit("remote agent does not advertise the ask-data skill")
    return card


def send(client: httpx.Client, url: str, text: str, context_id: str) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "contextId": context_id,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    body = client.post(url, json=payload, timeout=120).json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def render(card: dict, results: list[tuple[str, dict]]) -> str:
    lines = [
        f"# Sales briefing - {date.today().isoformat()}",
        "",
        f"Compiled by reporting-agent from `{card['name']}` v{card['version']} over A2A.",
        "",
    ]
    for question, task in results:
        state = task["status"]["state"]
        answer = task["status"]["message"]["parts"][0]["text"]
        data = next((p["data"] for a in task.get("artifacts", []) for p in a["parts"] if p.get("kind") == "data"), {})
        lines += [f"## {question}", "", f"_{state}_ - {answer}", ""]
        cols, rows = data.get("columns") or [], data.get("rows") or []
        if cols and rows:
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "---|" * len(cols))
            for r in rows[:10]:
                lines.append("| " + " | ".join(_cell(v) for v in r) + " |")
            lines.append("")
        if data.get("sql"):
            lines += ["<details><summary>SQL</summary>", "", "```sql", data["sql"], "```", "", "</details>", ""]
    return "\n".join(lines)


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--out", default="report.md")
    ap.add_argument("--questions", nargs="*", default=DEFAULT_QUESTIONS)
    args = ap.parse_args()
    with httpx.Client() as client:
        card = discover(client, args.base)
        context_id = str(uuid.uuid4())
        results = []
        for q in args.questions:
            task = send(client, card["url"], q, context_id)
            print(f"[{task['status']['state']}] {q}", file=sys.stderr)
            results.append((q, task))
    report = render(card, results)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(json.dumps({"out": args.out, "tasks": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
