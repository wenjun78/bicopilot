"""Viewer masking, the SQL console, exports, the fast path and the "how it was calculated" detail."""

from __future__ import annotations

from bicopilot.access import Policy, rewrite_sql


def test_rewrite_sql_accepts_sqlalchemy_dialect_names():
    """SQLAlchemy reports 'postgresql'; sqlglot wants 'postgres'. The live service hit this on every viewer question."""
    policy = Policy(role="viewer", masked={"dim_customer.customer_unique_id", "fact_reviews.comment_message"})
    sql = "SELECT c.customer_unique_id, c.customer_state FROM dim_customer AS c LIMIT 5"
    for dialect in ("postgresql", "sqlite", "postgres"):
        out, hits = rewrite_sql(sql, policy, dialect)
        assert hits == ["customer_unique_id"] and "'***'" in out and "customer_state" in out


def test_viewer_answers_are_masked_and_carry_no_workings(copilot):
    copilot.cache.clear()
    r = copilot.ask("late delivery rate by region 2018", session_id="viewer-1", role="viewer")
    assert r["status"] == "answered" and r["role"] == "viewer"
    assert r["trace"] == [] and r["tool_steps"] == [] and r["retrieval"] is None
    a = copilot.ask("late delivery rate by region 2018", session_id="analyst-1", role="analyst")
    assert a["trace"] and a["retrieval"] is not None


def test_fast_path_skips_exploration_and_model_write_when_the_layer_is_confident(copilot):
    copilot.cache.clear()
    r = copilot.ask("top 5 categories by revenue last quarter", session_id="fast")
    by_node = {e["node"]: e for e in r["trace"]}
    assert by_node["planner.resolve"]["fast_path"] is True and by_node["planner.resolve"]["unexplained"] == []
    assert by_node["sql.explore"].get("skipped") == "confident resolution"
    assert "confident" in by_node["sql.write"]["rationale"]
    assert r["status"] == "answered" and r["row_count"] == 5

    r2 = copilot.ask("how many orders did customers from SP place in 2017", session_id="slow")
    resolve = next(e for e in r2["trace"] if e["node"] == "planner.resolve")
    assert resolve["fast_path"] is False and "place" in resolve["unexplained"]


def test_method_detail_explains_the_calculation(copilot):
    copilot.cache.clear()
    r = copilot.ask("late delivery rate", session_id="method")
    m = r["method"]
    assert m["metrics"][0]["id"] == "late_delivery_rate" and "NULLIF" in m["metrics"][0]["formula"]
    assert m["period"] == "all dates in the data" and m["grouping"] == []
    assert m["sentence"].startswith("Share of delivered orders")
    r2 = copilot.ask("revenue by region 2017", session_id="method2")
    assert r2["method"]["period"].startswith("2017") and r2["method"]["grouping"] == ["region"]


def test_console_runs_guarded_sql_and_masks_for_viewers(client):
    body = {"sql": "SELECT customer_unique_id, customer_state FROM dim_customer LIMIT 3", "role": "analyst"}
    r = client.post("/sql", json=body).json()
    assert r["row_count"] == 3 and r["columns"] == ["customer_unique_id", "customer_state"]
    assert len(r["rows"][0][0]) == 32
    v = client.post("/sql", json={**body, "role": "viewer"}).json()
    assert v["rows"][0][0] == "***" and "customer_unique_id" in v["masked_columns"]
    bad = client.post("/sql", json={"sql": "DELETE FROM dim_customer", "role": "analyst"})
    assert bad.status_code == 400


def test_export_and_dictionary(client):
    r = client.post(
        "/export",
        json={"columns": ["region", "revenue"], "rows": [["South", 1.5], ["North", 2]], "format": "csv", "title": "t"},
    )
    assert r.status_code == 200 and "region,revenue" in r.text and "South" in r.text
    d = client.get("/dictionary", params={"role": "viewer"}).json()
    names = {t["name"] for t in d["tables"]}
    assert {"fact_orders", "dim_customer"} <= names
    cust = next(t for t in d["tables"] if t["name"] == "dim_customer")
    col = next(c for c in cust["columns"] if c["name"] == "customer_unique_id")
    assert col["masked_for_viewer"] is True and d["relationships"]
