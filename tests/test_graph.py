def test_end_to_end_breakdown(copilot):
    r = copilot.ask("revenue by region 2017")
    assert r["status"] == "answered"
    assert r["columns"] == ["region", "revenue"] and r["row_count"] == 5
    assert [e["node"] for e in r["trace"]][:4] == ["router", "planner.resolve", "planner.plan", "planner.retrieve"]
    assert r["critic"]["verdict"] == "pass"
    assert r["analysis"]["chart"]["type"] == "bar"
    assert "oi.status = 'delivered'" in r["sql"]


def test_retrieval_returns_structured_and_hybrid_hits(copilot):
    copilot.cache.clear()
    r = copilot.ask("freight ratio by category group 2018")
    ids = r["context_chunks"]
    assert "m:freight_ratio" in ids and "d:category_group" in ids and "g:table-fact-order-items" in ids
    assert r["retrieval"]["backend"] in ("memory", "pg")


def test_clarification_round_trip(copilot):
    copilot.cache.clear()
    first = copilot.ask("how is delivery in SP")
    assert first["status"] == "clarify" and first["clarification"]["options"] == [
        "Average delivery days",
        "On-time delivery %",
    ]
    second = copilot.ask("On-time delivery %", pending_question=first["pending_question"])
    assert second["status"] == "answered" and second["plan"]["metrics"] == ["on_time_delivery_rate"]
    assert second["plan"]["filters"][0]["value"] == "SP"
    assert second["pending_question"] == ""


def test_router_short_circuits(copilot):
    assert copilot.ask("hello")["status"] == "greeting"
    assert copilot.ask("what's the weather")["status"] == "refused"
    assert copilot.ask("what metrics can you answer")["status"] == "meta"


def test_time_series_orders_by_period(copilot):
    r = copilot.ask("monthly revenue trend in the South this year")
    months = [row[0] for row in r["rows"]]
    assert months == sorted(months) and r["analysis"]["chart"]["type"] == "line"
    assert any("2018" in c for c in r["analysis"]["caveats"])


def test_runs_are_recorded(copilot):
    copilot.ask("orders by payment type 2017")
    runs = copilot.recent_runs(5)
    assert runs and runs[0]["question"] == "orders by payment type 2017" and runs[0]["status"] == "answered"
    assert copilot.run(runs[0]["id"])["trace"]
