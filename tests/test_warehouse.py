"""Warehouse bootstrap: fresh load, idempotent restart, and upgrade from an older schema.

The deployed database (Neon) was first built for the synthetic warehouse this project started with. The
Olist schema reuses table names (dim_date, dim_customer, dim_product, fact_orders) with different columns,
so a plain CREATE TABLE IF NOT EXISTS would silently keep the old shape. ensure_warehouse() versions the
warehouse and rebuilds it, leaving the copilot_* logs alone.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from bicopilot.db import WAREHOUSE_VERSION, ensure_warehouse, warehouse_ready, warehouse_version


def _engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'w.db'}")


def test_fresh_load_then_noop(tmp_path):
    eng = _engine(tmp_path)
    counts = ensure_warehouse(eng)
    assert counts and counts["fact_orders"] == 99_441 and counts["fact_order_items"] == 112_650
    assert warehouse_version(eng) == WAREHOUSE_VERSION and warehouse_ready(eng)
    assert ensure_warehouse(eng) is None  # second start: nothing to do


def test_upgrade_from_legacy_schema_keeps_logs(tmp_path):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        # the old synthetic shape: same names, different columns, plus tables that no longer exist
        conn.execute(text("CREATE TABLE dim_date (date_key TEXT PRIMARY KEY, fiscal_year INTEGER, fiscal_period TEXT)"))
        conn.execute(text("CREATE TABLE dim_customer (customer_key INTEGER PRIMARY KEY, region TEXT, segment TEXT)"))
        conn.execute(text("CREATE TABLE dim_product (product_key INTEGER PRIMARY KEY, category TEXT)"))
        conn.execute(text("CREATE TABLE dim_sales_rep (rep_key INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE fact_orders (order_id TEXT PRIMARY KEY, revenue REAL, status TEXT)"))
        conn.execute(text("CREATE TABLE fact_targets (region TEXT, target REAL)"))
        conn.execute(text("INSERT INTO fact_orders VALUES ('o1', 10.0, 'closed')"))
        # a log table from the previous deployment must survive
        conn.execute(text("CREATE TABLE copilot_runs (id INTEGER PRIMARY KEY, question TEXT)"))
        conn.execute(text("INSERT INTO copilot_runs VALUES (1, 'revenue by region FY2026')"))

    assert warehouse_version(eng) is None
    counts = ensure_warehouse(eng)
    assert counts and counts["fact_orders"] == 99_441
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(fact_orders)"))}
        assert {"delivery_days", "payment_value", "review_score"} <= cols and "revenue" not in cols
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "dim_sales_rep" not in tables and "fact_targets" not in tables
        assert conn.execute(text("SELECT question FROM copilot_runs")).scalar_one() == "revenue by region FY2026"
    assert warehouse_version(eng) == WAREHOUSE_VERSION


def test_api_starts_in_background_and_reports_readiness(tmp_path):
    """Without an injected copilot the app binds first and builds the copilot in a thread: /healthz says
    'starting' (503) until the warehouse is loaded, then 200; data endpoints answer 503 meanwhile."""
    import time

    from fastapi.testclient import TestClient

    from bicopilot.api import create_app
    from bicopilot.config import Settings

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'bg.db'}",
        llm_provider="fake",
        embedding_provider="hash",
        cache_url="memory",
        as_of_date="2018-08-31",
    )
    with TestClient(create_app(settings)) as client:
        first = client.get("/healthz")
        seen_starting = first.status_code == 503
        if seen_starting:
            assert first.json()["status"] == "starting" and first.headers.get("retry-after") == "5"
            r = client.post("/chat", json={"question": "revenue by state 2017", "session_id": "bg"})
            assert r.status_code in (503, 200)
        deadline = time.time() + 120
        while client.get("/healthz").status_code != 200:
            assert time.time() < deadline, "copilot did not become ready"
            time.sleep(0.5)
        health = client.get("/healthz").json()
        assert health["ok"] is True and health["as_of"] == "2018-08-31"
        r = client.post("/chat", json={"question": "revenue by state 2017", "session_id": "bg"}).json()
        assert r["status"] == "answered" and r["row_count"] > 0
