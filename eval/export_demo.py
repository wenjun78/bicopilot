"""Pre-compute copilot outputs for the static demo (GitHub Pages has no backend).

Runs the eval questions, the UI suggestions and every clarification follow-up through the real
pipeline and writes web/public/demo/results.json. The React app serves these when no API is
configured, labelled as pre-computed. Rows are capped so the bundle stays small.

    python eval/export_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bicopilot.config import ROOT, Settings
from bicopilot.service import Copilot

HERE = Path(__file__).resolve().parent
OUT = ROOT / "web" / "public" / "demo" / "results.json"
MAX_ROWS = 100
SUGGESTIONS = [
    "revenue by region FY2026",
    "top 5 customers by gross margin last quarter",
    "monthly revenue trend in APAC this year",
    "gross margin % by product category FY26",
    "revenue by region year over year FY2026",
    "what was the margin in EMEA",
    "orders this quarter vs last quarter",
    "bottom 3 products by revenue FY26 Q2",
    "average order value by segment calendar 2025",
    "which customers bought the most units in Q4",
    "cancellation rate by channel",
    "active customers by quarter FY2026",
]


def slim(r: dict) -> dict:
    keep = (
        "question", "intent", "status", "answer", "clarification", "pending_question", "plan", "sql", "columns",
        "row_count", "truncated", "analysis", "critic", "attempts", "retrieval", "context_chunks", "trace", "total_ms",
        "as_of", "llm",
    )  # fmt: skip
    out = {k: r.get(k) for k in keep}
    out["rows"] = r.get("rows", [])[:MAX_ROWS]
    out["id"] = "demo"
    return out


def main() -> None:
    copilot = Copilot(Settings())
    questions = [
        json.loads(line)["question"]
        for line in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen: dict[str, dict] = {}
    follow_ups: dict[str, dict] = {}
    for q in SUGGESTIONS + questions:
        key = q.strip().lower()
        if key in seen:
            continue
        r = copilot.ask(q)
        seen[key] = slim(r)
        if r["status"] == "clarify":
            for option in r["clarification"]["options"]:
                f = copilot.ask(option, pending_question=r["pending_question"])
                follow_ups[f"{r['pending_question'].strip().lower()}||{option.strip().lower()}"] = slim(f)
    payload = {
        "as_of": copilot.as_of,
        "llm": copilot.llm.name,
        "generated_by": "eval/export_demo.py",
        "suggestions": SUGGESTIONS,
        "questions": list(seen.values()),
        "follow_ups": follow_ups,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    print(f"{len(seen)} questions + {len(follow_ups)} follow-ups -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
