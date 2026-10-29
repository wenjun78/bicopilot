"""Tools the SQL agent can call (LangChain `@tool`, bound to the model with `bind_tools`).

ReAct, literally: the model reasons, calls a tool, reads the observation, and either calls another
or writes the SQL. The tools are read-only and bounded so a misbehaving model cannot do damage:
- describe_table:  columns and types of one allowed table
- sample_values:   up to 20 distinct values of one column (for spelling filters right)
- validate_sql:    run the guard without executing, returning the normalised SQL or the error
"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from sqlalchemy import Engine, inspect, text

from .sqlguard import ALLOWED_TABLES, GuardError, guard


def build_tools(engine: Engine, max_rows: int):
    dialect = engine.dialect.name

    @tool
    def describe_table(table: str) -> str:
        """Columns and types of one warehouse table (fact_orders, dim_date, dim_customer, dim_product, dim_sales_rep, fact_targets)."""
        table = table.strip().lower()
        if table not in ALLOWED_TABLES:
            return f"error: unknown table {table}; allowed: {', '.join(sorted(ALLOWED_TABLES))}"
        cols = inspect(engine).get_columns(table)
        return json.dumps([{"name": c["name"], "type": str(c["type"])} for c in cols])

    @tool
    def sample_values(table: str, column: str) -> str:
        """Up to 20 distinct values of a column, to get filter spellings right (e.g. region codes, status values)."""
        table, column = table.strip().lower(), column.strip().lower()
        if table not in ALLOWED_TABLES:
            return f"error: unknown table {table}"
        names = {c["name"] for c in inspect(engine).get_columns(table)}
        if column not in names:
            return f"error: {table} has no column {column}; columns: {', '.join(sorted(names))}"
        with engine.connect() as conn:
            rows = conn.execute(text(f"SELECT DISTINCT {column} FROM {table} ORDER BY 1 LIMIT 20")).all()
        return json.dumps([str(r[0]) for r in rows])

    @tool
    def validate_sql(sql: str) -> str:
        """Check a SELECT against the guard (single statement, allowed tables, LIMIT) without running it. Returns the normalised SQL or the error."""
        try:
            return guard(sql, target_dialect=dialect, max_rows=max_rows).sql
        except GuardError as exc:
            return f"error: {exc}"

    return [describe_table, sample_values, validate_sql]
