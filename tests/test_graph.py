def test_end_to_end_breakdown(copilot):
    r = copilot.ask("revenue by region FY2026")
    assert r["status"] == "answered"
    assert r["columns"] == ["region", "revenue"] and r["row_count"] == 4
    assert [e["node"] for e in r["trace"]][:4] == ["router", "planner.resolve", "planner.plan", "planner.retrieve"]
    assert r["critic"]["verdict"] == "pass"
    assert r["analysis"]["chart"]["type"] == "bar"
    assert "o.status = 'completed'" in r["sql"]


def test_retrieval_returns_structured_and_hybrid_hits(copilot):
    r = copilot.ask("gross margin % by product category FY26")
    ids = r["context_chunks"]
    assert "m:gross_margin_pct" in ids and "d:product_category" in ids and "g:table-fact-orders" in ids
    assert r["retrieval"]["backend"] in ("memory", "pg")


def test_clarification_round_trip(copilot):
    first = copilot.ask("what was the margin in EMEA")
    assert first["status"] == "clarify" and first["clarification"]["options"] == [
        "Gross margin (USD)",
        "Gross margin %",
    ]
    second = copilot.ask("Gross margin %", pending_question=first["pending_question"])
    assert second["status"] == "answered" and second["plan"]["metrics"] == ["gross_margin_pct"]
    assert second["plan"]["filters"][0]["value"] == "EMEA"
    assert second["pending_question"] == ""


def test_router_short_circuits(copilot):
    assert copilot.ask("hello")["status"] == "greeting"
    assert copilot.ask("what's the weather")["status"] == "refused"
    assert copilot.ask("what metrics can you answer")["status"] == "meta"


def test_time_series_orders_by_period(copilot):
    r = copilot.ask("monthly revenue trend in APAC this year")
    months = [row[0] for row in r["rows"]]
    assert months == sorted(months) and r["analysis"]["chart"]["type"] == "line"
    assert any("FY2027" in c for c in r["analysis"]["caveats"])


def test_runs_are_recorded(copilot):
    copilot.ask("orders by channel FY2026")
    runs = copilot.recent_runs(5)
    assert runs and runs[0]["question"] == "orders by channel FY2026" and runs[0]["status"] == "answered"
    assert copilot.run(runs[0]["id"])["trace"]
