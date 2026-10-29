"""Smoke-test a deployed copilot: health, three questions, a clarification round trip, the agent card.

python scripts/smoke_live.py https://bi-copilot.onrender.com
"""

from __future__ import annotations

import sys
import time

import httpx

QUESTIONS = [
    "revenue by region FY2026",
    "top 5 customers by gross margin last quarter",
    "orders this quarter vs last quarter",
]


def main(base: str) -> int:
    base = base.rstrip("/")
    with httpx.Client(timeout=120) as c:
        t0 = time.time()
        for _ in range(24):  # free tier wakes in <= ~90 s
            try:
                h = c.get(f"{base}/healthz").json()
                break
            except Exception:
                time.sleep(5)
        else:
            print("health check never answered")
            return 1
        print(f"health {h} ({time.time() - t0:.0f}s)")
        ok = True
        for q in QUESTIONS:
            t0 = time.time()
            r = c.post(f"{base}/chat", json={"question": q, "session_id": "smoke"})
            body = r.json()
            line = f"[{r.status_code}] {q} -> {body.get('status')} llm={body.get('llm')} rows={body.get('row_count')} {time.time() - t0:.1f}s"
            if body.get("fallback_error"):
                line += f"  FALLBACK: {body['fallback_error']}"
                ok = False
            print(line)
            if r.status_code != 200 or body.get("status") != "answered":
                ok = False
        first = c.post(f"{base}/chat", json={"question": "what was the margin in EMEA", "session_id": "smoke"}).json()
        second = c.post(
            f"{base}/chat",
            json={
                "question": "Gross margin %",
                "pending_question": first.get("pending_question", ""),
                "session_id": "smoke",
            },
        ).json()
        print(
            f"clarify: {first.get('status')} -> {second.get('status')} metrics={(second.get('plan') or {}).get('metrics')}"
        )
        ok = ok and first.get("status") == "clarify" and second.get("status") == "answered"
        card = c.get(f"{base}/.well-known/agent.json").json()
        print(f"agent card: {card.get('name')} -> {card.get('url')}")
        print("OK" if ok else "PROBLEMS (see above)")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"))
