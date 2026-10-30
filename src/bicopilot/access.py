"""Role-based masking, the three-layer pattern from a master-data assistant:

1. SQL rewrite: columns the role may not see are replaced by a literal in the SELECT list, so the
   database never returns them. Done on the sqlglot AST after the guard.
2. Post-filter: result columns are checked against the mask list after execution, in case an
   expression smuggled a masked column through (string concat, CASE, subquery alias).
3. Prompt: the agents are told which columns are masked so they neither select nor describe them.

Roles: "analyst" sees everything; "viewer" sees no personal or free-text columns. Which columns are
masked comes from semantic/dictionary.yaml (`masked: true`), never from code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

MASK = "***"
ROLES = ("analyst", "viewer")


@dataclass
class Policy:
    role: str = "analyst"
    masked: set[str] = field(default_factory=set)  # "table.column"

    @property
    def active(self) -> bool:
        return self.role == "viewer" and bool(self.masked)

    def masked_columns(self) -> set[str]:
        return {c.split(".", 1)[1] for c in self.masked}

    def prompt_note(self) -> str:
        if not self.active:
            return ""
        return (
            "MASKED COLUMNS (role=viewer): "
            + ", ".join(sorted(self.masked))
            + ". Never select, group by or describe them."
        )


def masked_from_dictionary(dictionary: dict) -> set[str]:
    out = set()
    for t in dictionary.get("tables", []):
        for c in t["columns"]:
            if c.get("masked_for_viewer"):
                out.add(f"{t['name']}.{c['name']}")
    return out


def rewrite_sql(sql: str, policy: Policy, dialect: str) -> tuple[str, list[str]]:
    """Layer 1: replace masked columns in the projection with a literal. Returns (sql, masked_aliases)."""
    if not policy.active:
        return sql, []
    masked_cols = policy.masked_columns()
    from .sqlguard import DIALECT_MAP

    dialect = DIALECT_MAP.get(dialect, dialect)  # SQLAlchemy says "postgresql", sqlglot says "postgres"
    tree = sqlglot.parse_one(sql, read=dialect)
    hits: list[str] = []
    for col in list(tree.find_all(exp.Column)):
        if col.name in masked_cols:
            alias = col.name
            parent = col.parent
            literal = exp.Literal.string(MASK)
            if isinstance(parent, exp.Alias):
                alias = parent.alias
            # a masked column anywhere (SELECT, WHERE, GROUP BY, ORDER BY) becomes the literal
            col.replace(literal)
            hits.append(alias)
    # bare masked columns in the SELECT list keep their name as alias so the UI shows the column
    for i, proj in enumerate(tree.expressions if isinstance(tree, exp.Select) else []):
        if isinstance(proj, exp.Literal) and proj.this == MASK:
            name = hits[i] if i < len(hits) else "masked"
            tree.expressions[i] = exp.alias_(proj, name)
    return tree.sql(dialect=dialect, pretty=True), hits


def post_filter(columns: list[str], rows: list[list], policy: Policy) -> tuple[list[list], list[str]]:
    """Layer 2: any result column whose name matches a masked column is blanked out."""
    if not policy.active:
        return rows, []
    masked_cols = policy.masked_columns()
    idx = [i for i, c in enumerate(columns) if c in masked_cols or c.lower() in masked_cols]
    if not idx:
        return rows, []
    for r in rows:
        for i in idx:
            r[i] = MASK
    return rows, [columns[i] for i in idx]
