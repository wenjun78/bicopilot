"""Evaluation harness: execution accuracy of generated SQL against hand-written gold SQL.

For each question the copilot runs end to end (router -> planner -> SQL agent -> analyst -> critic).
Scoring:
- status accuracy: did the copilot answer / clarify / refuse as expected?
- execution accuracy: for answered questions, do the generated and gold result sets match
  (same columns ignoring order, same rows after rounding, order-sensitive when the gold has ORDER BY + LIMIT)?
Writes eval/report.md and prints a summary. Exit code 1 if execution accuracy < --min.

    python eval/run.py                 # fake LLM (deterministic compiler), SQLite
    LLM_PROVIDER=openai python eval/run.py --min 0.85
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from bicopilot.config import Settings
from bicopilot.db import run_query
from bicopilot.service import Copilot

HERE = Path(__file__).resolve().parent


def normalise(columns: list[str], rows: list[list], ordered: bool) -> tuple[list[str], list[tuple]]:
    cols = [c.lower() for c in columns]
    out = []
    for r in rows:
        out.append(tuple(round(v, 2) if isinstance(v, float) else v for v in r))
    if not ordered:
        out.sort(key=lambda t: tuple("" if v is None else str(v) for v in t))
    return cols, out


def compare(gen_cols, gen_rows, gold_cols, gold_rows, ordered: bool) -> tuple[bool, str]:
    gc, gr = normalise(gen_cols, gen_rows, ordered)
    kc, kr = normalise(gold_cols, gold_rows, ordered)
    # the gold names the columns the question asks for; extra generated columns (e.g. change_pct) are fine
    if not set(kc) <= set(gc):
        return False, f"missing columns {sorted(set(kc) - set(gc))}"
    idx = [gc.index(c) for c in kc]
    gr_proj = [tuple(r[i] for i in idx) for r in gr]
    if not ordered:
        gr_proj.sort(key=lambda t: tuple("" if v is None else str(v) for v in t))
    if len(gr_proj) != len(kr):
        return False, f"row count {len(gr_proj)} vs gold {len(kr)}"
    for a, b in zip(gr_proj, kr):
        if a != b:
            return False, f"row mismatch {a} vs {b}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.0, help="fail if execution accuracy is below this")
    ap.add_argument("--only", type=int, nargs="*", help="question ids to run")
    args = ap.parse_args()
    settings = Settings()
    copilot = Copilot(settings)
    items = [
        json.loads(line) for line in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if args.only:
        items = [i for i in items if i["id"] in args.only]
    results = []
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for item in items:
        t0 = time.perf_counter()
        r = copilot.ask(item["question"])
        ms = int((time.perf_counter() - t0) * 1000)
        status_ok = r["status"] == item["expected_status"]
        exec_ok, detail = None, ""
        if item["gold_sql"]:
            if r["status"] == "answered":
                gold = run_query(copilot.engine, item["gold_sql"], settings.max_rows, settings.query_timeout_s)
                ordered = (
                    " LIMIT " in item["gold_sql"].upper()
                    and "ORDER BY" in item["gold_sql"].upper()
                    and "fiscal_period" not in item["gold_sql"]
                )
                exec_ok, detail = compare(r["columns"], r["rows"], gold.columns, gold.rows, ordered)
            else:
                exec_ok, detail = False, f"status {r['status']}: {r['answer'][:80]}"
        results.append(
            {
                **item,
                "status": r["status"],
                "status_ok": status_ok,
                "exec_ok": exec_ok,
                "detail": detail,
                "sql": r["sql"],
                "attempts": len(r["attempts"]),
                "ms": ms,
                "critic": (r.get("critic") or {}).get("verdict"),
            }
        )
        for tag in item["tags"]:
            if exec_ok is not None:
                by_tag[tag].append(exec_ok)
        mark = "PASS" if (exec_ok if exec_ok is not None else status_ok) else "FAIL"
        print(
            f"[{mark}] #{item['id']:02d} {item['question']}  ({r['status']}, {ms}ms){'  - ' + detail if detail else ''}"
        )

    scored = [x for x in results if x["exec_ok"] is not None]
    exec_acc = sum(1 for x in scored if x["exec_ok"]) / len(scored) if scored else 0.0
    status_acc = sum(1 for x in results if x["status_ok"]) / len(results)
    repaired = sum(1 for x in results if x["attempts"] > 1)
    lines = [
        "# Evaluation report",
        "",
        f"- LLM: `{copilot.llm.name}` - dialect: `{copilot.engine.dialect.name}` - vector backend: `{copilot.retriever.backend}` - as-of: {copilot.as_of}",
        f"- Questions: {len(results)} ({len(scored)} with gold SQL)",
        f"- **Execution accuracy: {exec_acc:.1%}** - status accuracy: {status_acc:.1%} - needed a repair loop: {repaired}",
        "",
        "## By tag",
        "",
        "| tag | n | execution accuracy |",
        "|---|---|---|",
    ]
    for tag, vals in sorted(by_tag.items()):
        lines.append(f"| {tag} | {len(vals)} | {sum(vals) / len(vals):.0%} |")
    lines += [
        "",
        "## Questions",
        "",
        "| # | question | expected | got | exec | ms | detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in results:
        ex = "" if x["exec_ok"] is None else ("yes" if x["exec_ok"] else "NO")
        lines.append(
            f"| {x['id']} | {x['question']} | {x['expected_status']} | {x['status']} | {ex} | {x['ms']} | {x['detail'][:60]} |"
        )
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"\nexecution accuracy {exec_acc:.1%} on {len(scored)} | status accuracy {status_acc:.1%} on {len(results)} | report -> eval/report.md"
    )
    return 0 if exec_acc >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
