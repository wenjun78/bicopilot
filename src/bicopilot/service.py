"""Copilot service: wires runtime + graph, runs a question, records a trace row."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    insert,
    inspect,
    select,
    text,
)

from .access import ROLES, Policy, masked_from_dictionary, post_filter, rewrite_sql
from .agents.graph import build_graph
from .agents.state import Runtime
from .attachments import context_block
from .cache import cache_key, make_cache
from .config import Settings
from .db import WAREHOUSE_VERSION, cache_get, cache_set, ensure_warehouse, make_engine, max_order_date, run_query
from .dictionary import build_dictionary, dictionary_chunks
from .llm import FakeLLM, make_llm
from .rerank import make_reranker
from .retrieval import Retriever
from .semantic import SemanticLayer
from .sqlguard import ALLOWED_TABLES, guard

log = logging.getLogger("bicopilot")
metadata = MetaData()
runs = Table(
    "copilot_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", DateTime, nullable=False),
    Column("session_id", String(64)),
    Column("question", Text, nullable=False),
    Column("intent", String(32)),
    Column("status", String(16)),
    Column("sql", Text),
    Column("row_count", Integer),
    Column("attempts", Integer),
    Column("critic_verdict", String(16)),
    Column("total_ms", Integer),
    Column("llm", String(16)),
    Column("cached", Boolean, default=False),
    Column("plan", JSON),
    Column("trace", JSON),
    Column("answer", Text),
)


def _add_missing_columns(engine) -> None:
    """Columns added after a database was created (no migration tool here): ALTER TABLE ... ADD COLUMN."""
    inspector = inspect(engine)
    for table in metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        with engine.begin() as conn:
            for column in table.columns:
                if column.name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}")
                    )


class Copilot:
    def __init__(self, settings: Settings | None = None, *, bootstrap: bool = True):
        self.settings = settings or Settings()
        self.engine = make_engine(self.settings)
        if bootstrap:
            loaded = ensure_warehouse(self.engine)
            if loaded:
                log.info("warehouse loaded: %s", ", ".join(f"{k}={v}" for k, v in loaded.items()))
        metadata.create_all(self.engine)
        _add_missing_columns(self.engine)
        self.semantic = SemanticLayer.load(self.settings.semantic_dir)
        self.semantic_version = hashlib.sha256((self.settings.semantic_dir / "metrics.yaml").read_bytes()).hexdigest()[
            :12
        ]
        self.dictionary = self._load_dictionary()
        self.masked_columns = masked_from_dictionary(self.dictionary)
        self.retriever = Retriever(
            self.semantic, self.settings, self.engine, extra_chunks=dictionary_chunks(self.dictionary)
        )
        self.llm = make_llm(self.settings, self.semantic)
        self.retriever.set_reranker(make_reranker(self.settings, self.llm))
        self.runtime = Runtime(
            self.settings, self.semantic, self.retriever, self.llm, self.engine, self.dictionary, self.masked_columns
        )
        self.graph = build_graph(self.runtime)
        # Deterministic stand-in used when the model path fails at runtime, so a provider outage or a
        # model/schema mismatch degrades to a labelled answer instead of an error page.
        self.fallback_graph = None
        if self.settings.llm_fallback_to_fake and self.llm.name != "fake":
            fallback_rt = Runtime(
                self.settings,
                self.semantic,
                self.retriever,
                FakeLLM(self.semantic),
                self.engine,
                self.dictionary,
                self.masked_columns,
            )
            self.fallback_graph = build_graph(fallback_rt)
        self.as_of = self.settings.as_of_date or self.semantic.as_of_default or max_order_date(self.engine)
        self.cache = make_cache(self.settings.cache_url, self.settings.cache_ttl_s)

    def _load_dictionary(self) -> dict[str, Any]:
        """Column profiles cost a few hundred queries; on a remote warehouse that is a minute of startup,
        so the result is cached in the database, keyed by warehouse version + dictionary/semantic text."""
        key_src = (self.settings.semantic_dir / "dictionary.yaml").read_bytes() + self.semantic_version.encode()
        key = f"dictionary:{WAREHOUSE_VERSION}:{hashlib.sha256(key_src).hexdigest()[:16]}"
        cached = cache_get(self.engine, key)
        if cached:
            try:
                return json.loads(cached)
            except ValueError:
                pass
        dictionary = build_dictionary(self.engine, self.semantic, ALLOWED_TABLES)
        cache_set(self.engine, key, json.dumps(dictionary, default=str))
        return dictionary

    def policy(self, role: str) -> Policy:
        role = role if role in ROLES else "analyst"
        return Policy(role=role, masked=self.masked_columns if role == "viewer" else set())

    def ask(
        self,
        question: str,
        *,
        pending_question: str = "",
        history: list[dict[str, str]] | None = None,
        session_id: str = "",
        role: str = "analyst",
        attachments: list[dict[str, Any]] | None = None,
        retain_memory: bool = True,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        policy = self.policy(role)
        attachment_text = context_block(attachments or [])
        key = cache_key(
            question + ("|att:" + hashlib.sha1(attachment_text.encode()).hexdigest()[:12] if attachment_text else ""),
            pending_question or "",
            self.as_of,
            self.semantic_version + "|" + policy.role,
        )
        cached = self.cache.get(key) if not attachment_text else None
        if cached:
            cached = {
                **cached,
                "id": str(uuid.uuid4()),
                "cached": True,
                "total_ms": int((time.perf_counter() - started) * 1000),
            }
            self._record(cached, session_id)
            return cached
        state = {
            "question": question.strip(),
            "pending_question": pending_question or "",
            "history": (history or []) if retain_memory else [],
            "as_of": self.as_of,
            "attachment_text": attachment_text,
            "policy_note": policy.prompt_note(),
            "role": policy.role,
        }
        llm_name, fallback_error = self.llm.name, ""
        try:
            final = self.graph.invoke(state, config={"recursion_limit": 40})
        except Exception as exc:
            if self.fallback_graph is None:
                raise
            log.exception("model path failed; answering with the deterministic path")
            fallback_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            final = self.fallback_graph.invoke(dict(state), config={"recursion_limit": 40})
            llm_name = "fake (fallback)"
            if final.get("status") == "answered":
                final["answer"] = (
                    final.get("answer", "")
                    + "\n\nNote: the language model was unavailable for this question, so the deterministic compiler answered it."
                )
        masked_cols: list[str] = []
        if policy.active and final.get("columns"):
            final["rows"], masked_cols = post_filter(
                list(final["columns"]), [list(r) for r in final.get("rows", [])], policy
            )
        total_ms = int((time.perf_counter() - started) * 1000)
        run_id = str(uuid.uuid4())
        result = {
            "id": run_id,
            "role": policy.role,
            "masked_columns": masked_cols,
            "attachments": [
                {k: v for k, v in a.items() if k != "text"} | {"chars": a.get("chars", 0)} for a in (attachments or [])
            ],
            "question": final.get("question", question),
            "intent": final.get("intent"),
            "status": final.get("status"),
            "answer": final.get("answer", ""),
            "clarification": {"question": final.get("clarification", ""), "options": final.get("options", [])}
            if final.get("status") == "clarify"
            else None,
            "pending_question": final.get("pending_question", ""),
            "plan": final.get("plan"),
            "sql": final.get("sql") if final.get("status") == "answered" else (final.get("sql") or None),
            "columns": final.get("columns", []),
            "rows": final.get("rows", []),
            "row_count": final.get("row_count", 0),
            "truncated": final.get("truncated", False),
            "analysis": final.get("analysis"),
            "critic": final.get("critic"),
            "attempts": final.get("sql_attempts", []),
            "tool_steps": final.get("tool_steps", []),
            "retrieval": final.get("retrieval_meta"),
            "context_chunks": [c["id"] for c in final.get("context_chunks", [])],
            "trace": final.get("trace", []),
            "total_ms": total_ms,
            "as_of": self.as_of,
            "llm": llm_name,
            "fallback_error": fallback_error,
            "cached": False,
        }
        self._record(result, session_id)
        if (
            result["status"] == "answered" and not fallback_error and not attachment_text
        ):  # never cache a degraded answer
            self.cache.set(key, result)
        return result

    def run_sql(self, sql: str, role: str = "analyst") -> dict[str, Any]:
        """SQL console: guard -> role rewrite -> read-only execute -> post-filter. Same path the agents use."""
        started = time.perf_counter()
        policy = self.policy(role)
        checked = guard(sql, target_dialect=self.engine.dialect.name, max_rows=self.settings.max_rows)
        final_sql, rewritten = checked.sql, []
        if policy.active:
            final_sql, rewritten = rewrite_sql(checked.sql, policy, self.engine.dialect.name)
        result = run_query(self.engine, final_sql, self.settings.max_rows, self.settings.query_timeout_s)
        rows, masked = post_filter(result.columns, result.rows, policy)
        return {
            "id": str(uuid.uuid4()),
            "sql": final_sql,
            "columns": result.columns,
            "rows": rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
            "warnings": checked.warnings + ([f"LIMIT {self.settings.max_rows} added"] if checked.added_limit else []),
            "masked_columns": sorted(set(rewritten) | set(masked)),
            "role": policy.role,
            "total_ms": int((time.perf_counter() - started) * 1000),
        }

    def explain_sql(self, sql: str, columns: list[str], rows: list[list[Any]], role: str = "analyst") -> dict[str, Any]:
        """Console 'Explain': the analyst agent interprets a result set the visitor produced by hand."""
        from .agents.analyst import build_analyst
        from .semantic import Plan

        policy = self.policy(role)
        agent = build_analyst(self.runtime)
        state = {
            "question": "Explain this result set",
            "as_of": self.as_of,
            "plan": Plan(metrics=[], dimensions=[]).model_dump(),
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "policy_note": policy.prompt_note(),
        }
        out = agent.invoke(state)
        return {"analysis": out.get("analysis"), "critic": out.get("critic")}

    def _record(self, result: dict[str, Any], session_id: str) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(runs).values(
                        id=result["id"],
                        created_at=datetime.now(UTC).replace(tzinfo=None),
                        session_id=session_id[:64],
                        question=result["question"],
                        intent=result["intent"],
                        status=result["status"],
                        sql=result["sql"],
                        row_count=result["row_count"],
                        attempts=len(result["attempts"]),
                        critic_verdict=(result.get("critic") or {}).get("verdict"),
                        total_ms=result["total_ms"],
                        llm=result["llm"],
                        cached=bool(result.get("cached")),
                        plan=json.loads(json.dumps(result["plan"], default=str)) if result["plan"] else None,
                        trace=json.loads(json.dumps(result["trace"], default=str)),
                        answer=result["answer"],
                    )
                )
        except Exception:  # logging must never break an answer
            pass

    def model_answers_today(self) -> int:
        """Site-wide count of non-cached runs since 00:00 UTC (the global daily cap counts these)."""
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        with self.engine.connect() as conn:
            n = conn.execute(
                select(func.count()).select_from(runs).where(runs.c.created_at >= start, runs.c.cached.is_(False))
            ).scalar_one()
        return int(n or 0)

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(runs).order_by(runs.c.created_at.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        return dict(row) if row else None
