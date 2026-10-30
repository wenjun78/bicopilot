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

from sqlalchemy import Engine, column, create_engine, event, insert, table, text
from sqlalchemy.exc import SQLAlchemyError

from .config import ROOT, Settings

CSV_DIR = ROOT / "data" / "csv"
SCHEMA_SQL = ROOT / "data" / "schema.sql"
TABLE_ORDER = [
    "dim_date",
    "dim_customer",
    "dim_seller",
    "dim_product",
    "dim_geo",
    "fact_orders",
    "fact_order_items",
    "fact_payments",
    "fact_reviews",
]
# Bumped whenever schema.sql changes shape. A deployed database built for an older shape (the synthetic
# warehouse this project started with, for example) is dropped and rebuilt from the CSVs on start; the
# run/visit logs (copilot_*) are never touched.
WAREHOUSE_VERSION = "olist-1"
LEGACY_TABLES = ["dim_sales_rep", "fact_targets"]
INSERT_BATCH = 5000


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
    if url.startswith("postgres://"):  # Heroku/Neon style scheme
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):  # the only installed driver is psycopg 3
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return create_engine(url, pool_pre_ping=True)


def init_schema(engine: Engine) -> None:
    raw = "\n".join(line.split("--", 1)[0] for line in SCHEMA_SQL.read_text(encoding="utf-8").splitlines())
    statements = [s.strip() for s in raw.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS warehouse_meta (key VARCHAR(64) PRIMARY KEY, value VARCHAR(255))")
        )
        conn.execute(text("CREATE TABLE IF NOT EXISTS warehouse_cache (key VARCHAR(128) PRIMARY KEY, value TEXT)"))


def warehouse_version(engine: Engine) -> str | None:
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT value FROM warehouse_meta WHERE key = 'schema_version'")
            ).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def drop_warehouse(engine: Engine) -> None:
    """Drop the warehouse tables (current and legacy names) but never the copilot_* logs."""
    cascade = " CASCADE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        for t in reversed(TABLE_ORDER + LEGACY_TABLES + ["warehouse_meta", "warehouse_cache"]):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}{cascade}"))


def ensure_warehouse(engine: Engine, csv_dir: Path = CSV_DIR) -> dict[str, int] | None:
    """Create or upgrade the warehouse: schema, data, version marker.

    - fresh database: create tables, load CSVs, stamp the version
    - same version and data present: nothing
    - different version (tables from an older schema): drop warehouse tables, recreate, reload
    Returns the row counts when a load happened, else None.
    """
    if warehouse_version(engine) != WAREHOUSE_VERSION:
        drop_warehouse(engine)
        init_schema(engine)
    elif warehouse_ready(engine):
        return None
    counts = load_csvs(engine, csv_dir)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM warehouse_meta WHERE key = 'schema_version'"))
        conn.execute(
            text("INSERT INTO warehouse_meta (key, value) VALUES ('schema_version', :v)"), {"v": WAREHOUSE_VERSION}
        )
    return counts


def load_csvs(engine: Engine, csv_dir: Path = CSV_DIR, replace: bool = True) -> dict[str, int]:
    """Load the warehouse CSVs. PostgreSQL gets COPY (450k rows in seconds, even to a remote Neon);
    SQLite gets batched multi-row inserts."""
    counts = {}
    with engine.begin() as conn:
        for name in TABLE_ORDER:
            path = csv_dir / f"{name}.csv"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing - run `python data/build_olist.py` first")
            if replace:
                conn.execute(text(f"DELETE FROM {name}"))
            if engine.dialect.name == "postgresql":
                counts[name] = _copy_postgres(conn, name, path)
            else:
                counts[name] = _insert_rows(conn, name, path)
    return counts


def _copy_postgres(conn, name: str, path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        header = f.readline().strip()
    sql = f"COPY {name} ({header}) FROM STDIN WITH (FORMAT csv, HEADER true)"
    raw = conn.connection.dbapi_connection
    with raw.cursor() as cur, path.open("rb") as f:
        if hasattr(cur, "copy"):  # psycopg 3
            with cur.copy(sql) as copy:
                while chunk := f.read(1 << 20):
                    copy.write(chunk)
        else:  # psycopg2
            cur.copy_expert(sql, f)
        n = cur.rowcount
    if n is None or n < 0:
        with path.open(encoding="utf-8") as f:
            n = sum(1 for _ in f) - 1
    return int(n)


def _insert_rows(conn, name: str, path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        rows = [{k: _coerce(v) for k, v in r.items()} for r in csv.DictReader(f)]
    if rows:
        tbl = table(name, *[column(c) for c in rows[0].keys()])
        for i in range(0, len(rows), INSERT_BATCH):
            conn.execute(insert(tbl), rows[i : i + INSERT_BATCH])
    return len(rows)


def cache_get(engine: Engine, key: str) -> str | None:
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT value FROM warehouse_cache WHERE key = :k"), {"k": key}
            ).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def cache_set(engine: Engine, key: str, value: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM warehouse_cache WHERE key = :k"), {"k": key})
            conn.execute(text("INSERT INTO warehouse_cache (key, value) VALUES (:k, :v)"), {"k": key, "v": value})
    except SQLAlchemyError:  # pragma: no cover - cache is best effort
        pass


def warehouse_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM fact_orders")).scalar_one()
        return int(n) > 0
    except SQLAlchemyError:
        return False


def max_order_date(engine: Engine) -> str:
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(purchase_date) FROM fact_orders")).scalar_one()
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
