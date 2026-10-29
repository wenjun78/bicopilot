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

from .agents.graph import build_graph
from .agents.state import Runtime
from .cache import cache_key, make_cache
from .config import Settings
from .db import init_schema, load_csvs, make_engine, max_order_date, warehouse_ready
from .llm import FakeLLM, make_llm
from .rerank import make_reranker
from .retrieval import Retriever
from .semantic import SemanticLayer

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
            init_schema(self.engine)
            if not warehouse_ready(self.engine):
                load_csvs(self.engine)
        metadata.create_all(self.engine)
        _add_missing_columns(self.engine)
        self.semantic = SemanticLayer.load(self.settings.semantic_dir)
        self.retriever = Retriever(self.semantic, self.settings, self.engine)
        self.llm = make_llm(self.settings, self.semantic)
        self.retriever.set_reranker(make_reranker(self.settings, self.llm))
        self.runtime = Runtime(self.settings, self.semantic, self.retriever, self.llm, self.engine)
        self.graph = build_graph(self.runtime)
        # Deterministic stand-in used when the model path fails at runtime, so a provider outage or a
        # model/schema mismatch degrades to a labelled answer instead of an error page.
        self.fallback_graph = None
        if self.settings.llm_fallback_to_fake and self.llm.name != "fake":
            fallback_rt = Runtime(self.settings, self.semantic, self.retriever, FakeLLM(self.semantic), self.engine)
            self.fallback_graph = build_graph(fallback_rt)
        self.as_of = self.settings.as_of_date or max_order_date(self.engine)
        self.cache = make_cache(self.settings.cache_url, self.settings.cache_ttl_s)
        self.semantic_version = hashlib.sha256((self.settings.semantic_dir / "metrics.yaml").read_bytes()).hexdigest()[
            :12
        ]

    def ask(
        self,
        question: str,
        *,
        pending_question: str = "",
        history: list[dict[str, str]] | None = None,
        session_id: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        key = cache_key(question, pending_question or "", self.as_of, self.semantic_version)
        cached = self.cache.get(key)
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
            "history": history or [],
            "as_of": self.as_of,
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
        total_ms = int((time.perf_counter() - started) * 1000)
        run_id = str(uuid.uuid4())
        result = {
            "id": run_id,
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
        if result["status"] == "answered" and not fallback_error:  # never cache a degraded answer
            self.cache.set(key, result)
        return result

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
