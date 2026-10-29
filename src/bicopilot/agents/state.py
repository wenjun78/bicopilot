"""Shared graph state. Every agent (sub-graph) reads and writes this one TypedDict."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy import Engine

from ..config import Settings
from ..llm import LLM
from ..retrieval import Retriever
from ..semantic import SemanticLayer


class BIState(TypedDict, total=False):
    # input
    question: str
    pending_question: str  # the data question a clarification reply refers to
    history: list[dict[str, str]]
    as_of: str
    # routing
    intent: str
    # planning
    draft_plan: dict[str, Any]
    plan: dict[str, Any]
    awaiting_clarification: bool
    clarification: str
    options: list[str]
    context_chunks: list[dict[str, Any]]
    retrieval_meta: dict[str, Any]
    # sql
    tool_steps: list[dict[str, Any]]
    explored_sql: str
    sql: str
    sql_attempts: list[dict[str, Any]]
    guard_warnings: list[str]
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    exec_error: str
    repairs: int
    # analysis
    analysis: dict[str, Any]
    critic: dict[str, Any]
    critic_loops: int
    critic_hint: str
    # output
    answer: str
    status: str  # answered | clarify | refused | error | greeting | meta
    trace: list[dict[str, Any]]


@dataclass
class Runtime:
    settings: Settings
    semantic: SemanticLayer
    retriever: Retriever
    llm: LLM
    engine: Engine


def trace_event(state: BIState, node: str, started: float, **extra: Any) -> list[dict[str, Any]]:
    events = list(state.get("trace", []))
    events.append({"node": node, "ms": int((time.perf_counter() - started) * 1000), **extra})
    return events
