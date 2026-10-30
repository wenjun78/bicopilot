"""Data dictionary: every table and column with a description, type, profile and the governed metrics
and dimensions that touch it.

Descriptions come from `semantic/dictionary.yaml` (hand-written, the authoritative business meaning);
the profile (row counts, distinct counts, null %, min/max, sample values) is computed from the live
warehouse at startup and cached. The dictionary is served at /dictionary, rendered in the UI's
Dictionary tab and the SQL console's schema browser, and chunked into the retriever so the agents can
cite column meanings.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, inspect, text

from .semantic import SemanticLayer

PROFILE_SAMPLE_VALUES = 8


def load_descriptions(semantic_dir: Path) -> dict[str, Any]:
    path = semantic_dir / "dictionary.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"tables": {}}


def build_dictionary(engine: Engine, semantic: SemanticLayer, allowed_tables: set[str]) -> dict[str, Any]:
    started = time.perf_counter()
    desc = load_descriptions(semantic.spec_dir) if hasattr(semantic, "spec_dir") else {"tables": {}}
    inspector = inspect(engine)
    usage = _usage_index(semantic)
    tables: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for name in sorted(allowed_tables):
            if name not in inspector.get_table_names():
                continue
            tdesc = desc.get("tables", {}).get(name, {})
            row_count = int(conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one())
            pk = set(inspector.get_pk_constraint(name).get("constrained_columns") or [])
            columns = []
            for col in inspector.get_columns(name):
                cname = col["name"]
                cdesc = (tdesc.get("columns") or {}).get(cname, {})
                if isinstance(cdesc, str):
                    cdesc = {"description": cdesc}
                profile = _profile_column(conn, name, cname, str(col["type"]), row_count)
                columns.append(
                    {
                        "name": cname,
                        "type": str(col["type"]),
                        "primary_key": cname in pk,
                        "nullable": bool(col.get("nullable", True)),
                        "description": cdesc.get("description", ""),
                        "unit": cdesc.get("unit", ""),
                        "joins_to": cdesc.get("joins_to", ""),
                        "masked_for_viewer": bool(cdesc.get("masked", False)),
                        "used_by": usage.get(f"{name}.{cname}", []),
                        **profile,
                    }
                )
            tables.append(
                {
                    "name": name,
                    "kind": "fact" if name.startswith("fact_") else "dimension",
                    "description": tdesc.get("description") or semantic.tables.get(name, {}).get("description", ""),
                    "grain": tdesc.get("grain", ""),
                    "source": tdesc.get("source", ""),
                    "row_count": row_count,
                    "columns": columns,
                }
            )
    return {
        "dataset": semantic.spec.get("dataset", ""),
        "currency": semantic.spec.get("currency", ""),
        "as_of": semantic.as_of_default,
        "tables": tables,
        "relationships": desc.get("relationships", []),
        "quality_notes": desc.get("quality_notes", []),
        "generated_ms": int((time.perf_counter() - started) * 1000),
    }


def _profile_column(conn, table: str, column: str, ctype: str, row_count: int) -> dict[str, Any]:
    if row_count == 0:
        return {"distinct": 0, "null_pct": 0.0, "min": None, "max": None, "samples": []}
    lower = ctype.lower()
    numeric = any(k in lower for k in ("int", "numeric", "real", "float", "double", "decimal"))
    temporal = any(k in lower for k in ("date", "time"))
    textual = not numeric and not temporal
    try:
        stats = conn.execute(
            text(
                f"SELECT COUNT(DISTINCT {column}), SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END), MIN({column}), MAX({column}) FROM {table}"
            )
        ).one()
    except Exception:
        return {"distinct": None, "null_pct": None, "min": None, "max": None, "samples": []}
    distinct, nulls, mn, mx = stats
    samples: list[Any] = []
    high_cardinality_text = textual and distinct and distinct > 200
    if high_cardinality_text:  # IDs, free text: min/max and samples are noise
        mn = mx = None
    if textual and distinct and distinct <= 50:
        rows = conn.execute(
            text(
                f"SELECT {column}, COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL GROUP BY {column} ORDER BY n DESC LIMIT {PROFILE_SAMPLE_VALUES}"
            )
        ).all()
        samples = [{"value": str(r[0]), "count": int(r[1])} for r in rows]
    elif textual and not high_cardinality_text:
        rows = conn.execute(
            text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {PROFILE_SAMPLE_VALUES}")
        ).all()
        samples = [{"value": str(r[0])[:60], "count": None} for r in rows]
    return {
        "distinct": int(distinct or 0),
        "null_pct": round(100.0 * int(nulls or 0) / row_count, 2),
        "min": _plain(mn),
        "max": _plain(mx),
        "samples": samples,
    }


def _plain(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if type(v).__name__ == "Decimal":
        return float(v)
    return v if isinstance(v, (int, float)) else str(v)[:60]


def _usage_index(semantic: SemanticLayer) -> dict[str, list[str]]:
    """column -> [metric:id | dimension:id] from the semantic layer, by scanning the SQL expressions."""
    alias_to_table: dict[str, str] = {}
    for base, spec in semantic.bases.items():
        alias_to_table[spec["alias"]] = base
        for tbl, join in spec.get("joins", {}).items():
            alias_to_table[join.split(" ON ")[0].split()[-1]] = tbl
    out: dict[str, list[str]] = {}
    import re

    def scan(expr: str, tag: str) -> None:
        for alias, col in re.findall(r"\b([a-z]{1,3})\.([a-z_]+)\b", expr):
            tbl = alias_to_table.get(alias)
            if tbl:
                out.setdefault(f"{tbl}.{col}", [])
                if tag not in out[f"{tbl}.{col}"]:
                    out[f"{tbl}.{col}"].append(tag)

    for mid, m in semantic.metrics.items():
        scan(m["sql"] + " " + " ".join(m.get("filters", [])), f"metric:{mid}")
    for did, d in semantic.dimensions.items():
        scan(d["column"], f"dimension:{did}")
    return out


def dictionary_chunks(dictionary: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(id, title, text) chunks for the retriever: one per table with its columns, one per quality note."""
    chunks = []
    for t in dictionary["tables"]:
        lines = [
            f"{t['name']} ({t['kind']}, {t['row_count']:,} rows). {t['description']} Grain: {t['grain'] or 'n/a'}."
        ]
        for c in t["columns"]:
            bits = [f"{c['name']} ({c['type']})"]
            if c["description"]:
                bits.append(c["description"])
            if c.get("unit"):
                bits.append(f"unit {c['unit']}")
            if c.get("joins_to"):
                bits.append(f"joins to {c['joins_to']}")
            if c.get("samples") and c["samples"][0].get("count") is not None:
                bits.append("values: " + ", ".join(s["value"] for s in c["samples"][:6]))
            if c.get("null_pct"):
                bits.append(f"{c['null_pct']}% null")
            lines.append("- " + "; ".join(bits))
        chunks.append((f"dict:{t['name']}", f"Dictionary: {t['name']}", "\n".join(lines)))
    for i, note in enumerate(dictionary.get("quality_notes", [])):
        chunks.append((f"dict:quality-{i}", "Data quality note", note))
    return chunks
