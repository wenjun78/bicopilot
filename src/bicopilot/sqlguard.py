"""SQL guard: the only path from model output to the database.

Parses with sqlglot, rejects anything but a single SELECT, enforces a table allowlist,
strips comments, adds a LIMIT when missing, and transpiles between dialects so the agent can
write in one dialect and run on SQLite or PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"fact_orders", "dim_date", "dim_customer", "dim_product", "dim_sales_rep", "fact_targets"}
# SQLAlchemy dialect name -> sqlglot dialect. The agent writes PostgreSQL; the guard transpiles.
DIALECT_MAP = {
    "sqlite": "sqlite",
    "postgresql": "postgres",
    "mysql": "mysql",
    "mssql": "tsql",
    "snowflake": "snowflake",
}


class GuardError(Exception):
    pass


@dataclass
class GuardResult:
    sql: str
    tables: set[str] = field(default_factory=set)
    added_limit: bool = False
    warnings: list[str] = field(default_factory=list)


def guard(
    sql: str, target_dialect: str = "sqlite", max_rows: int = 500, allowed_tables: set[str] = ALLOWED_TABLES
) -> GuardResult:
    sql = _strip_fence(sql)
    if not sql.strip():
        raise GuardError("empty SQL")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise GuardError(f"SQL does not parse: {str(exc).splitlines()[0]}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise GuardError("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select) and not (isinstance(tree, exp.Union)):
        raise GuardError(f"only SELECT is allowed, got {type(tree).__name__}")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Merge)
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise GuardError(f"{type(node).__name__} is not allowed")
    tables = {t.name.lower() for t in tree.find_all(exp.Table) if t.name}
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    unknown = tables - allowed_tables - cte_names
    if unknown:
        raise GuardError(f"unknown table(s): {', '.join(sorted(unknown))}")
    result = GuardResult(sql="", tables=tables - cte_names)
    for node in tree.find_all(exp.Column):
        if node.name == "*" and len(tables) > 1:
            result.warnings.append("SELECT * across joins returns every column; prefer named columns")
            break
    if isinstance(tree, exp.Select) and tree.args.get("limit") is None:
        tree = tree.limit(max_rows)
        result.added_limit = True
    elif isinstance(tree, exp.Select):
        try:
            current = int(tree.args["limit"].expression.this)
            if current > max_rows:
                tree = tree.limit(max_rows)
                result.warnings.append(f"LIMIT lowered to {max_rows}")
        except (AttributeError, ValueError, TypeError):
            pass
    write = DIALECT_MAP.get(target_dialect, target_dialect)
    result.sql = tree.sql(dialect=write, pretty=True)
    return result


def _strip_fence(sql: str) -> str:
    s = sql.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s.strip().rstrip(";").strip()
