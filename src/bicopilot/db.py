"""Warehouse access: schema creation, CSV load, read-only query execution with timeout + row cap.

The warehouse is a plain SQLAlchemy engine. SQLite for the zero-infra demo and tests,
PostgreSQL (with pgvector) for the deployed version. The SQL guard (sqlguard.py) runs before
anything reaches `run_query`; this module still refuses anything that is not a single SELECT.
"""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from .config import ROOT, Settings

CSV_DIR = ROOT / "data" / "csv"
SCHEMA_SQL = ROOT / "data" / "schema.sql"
TABLE_ORDER = ["dim_date", "dim_customer", "dim_product", "dim_sales_rep", "fact_orders", "fact_targets"]


class QueryError(Exception):
    """Database rejected or timed out on the SQL. Message is safe to feed back to the repair node."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    sql: str = ""
    notes: list[str] = field(default_factory=list)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]


def make_engine(settings: Settings) -> Engine:
    url = settings.database_url
    if url.startswith("sqlite"):
        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

        return engine
    return create_engine(url, pool_pre_ping=True)


def init_schema(engine: Engine) -> None:
    statements = [s.strip() for s in SCHEMA_SQL.read_text(encoding="utf-8").split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def load_csvs(engine: Engine, csv_dir: Path = CSV_DIR, replace: bool = True) -> dict[str, int]:
    counts = {}
    with engine.begin() as conn:
        for table in TABLE_ORDER:
            path = csv_dir / f"{table}.csv"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing - run `python data/generate.py` first")
            with path.open(encoding="utf-8") as f:
                rows = [{k: _coerce(v) for k, v in r.items()} for r in csv.DictReader(f)]
            if replace:
                conn.execute(text(f"DELETE FROM {table}"))
            if rows:
                cols = list(rows[0].keys())
                placeholders = ", ".join(f":{c}" for c in cols)
                conn.execute(text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"), rows)
            counts[table] = len(rows)
    return counts


def warehouse_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM fact_orders")).scalar_one()
        return int(n) > 0
    except SQLAlchemyError:
        return False


def max_order_date(engine: Engine) -> str:
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(order_date) FROM fact_orders")).scalar_one()
    return str(value)[:10]


def run_query(engine: Engine, sql: str, max_rows: int, timeout_s: int) -> QueryResult:
    """Execute a guarded SELECT. Row cap is enforced here even if the guard added a LIMIT."""
    head = sql.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise QueryError("Only SELECT statements may be executed")
    started = time.perf_counter()
    conn = engine.connect()
    timer = None
    if engine.dialect.name == "sqlite":
        raw = conn.connection.dbapi_connection
        timer = threading.Timer(timeout_s, raw.interrupt)
        timer.start()
    try:
        if engine.dialect.name == "postgresql":
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}"))
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows: list[list[Any]] = []
        truncated = False
        for row in result:
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append([_jsonable(v) for v in row])
    except SQLAlchemyError as exc:
        message = str(getattr(exc, "orig", exc)).splitlines()[0]
        if "interrupted" in message.lower() or "statement timeout" in message.lower():
            raise QueryError(f"Query exceeded {timeout_s}s timeout") from exc
        raise QueryError(message) from exc
    finally:
        if timer:
            timer.cancel()
        conn.close()
    elapsed = int((time.perf_counter() - started) * 1000)
    return QueryResult(columns, rows, len(rows), truncated, elapsed, sql=sql)


def _coerce(value: str) -> Any:
    """CSV text -> int/float where it looks numeric, so both SQLite and Postgres store real numbers."""
    if value == "":
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    try:
        return (
            float(value)
            if any(ch in value for ch in ".eE") and value.replace(".", "", 1).lstrip("-").isdigit()
            else value
        )
    except ValueError:
        return value


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if type(value).__name__ == "Decimal":
        return float(value)
    return value
