"""SQL agent (sub-graph): write -> guard -> execute, with a repair loop.

Pattern: ReAct-style tool use with Reflection on failure. The model writes SQL, the guard is a
tool that validates/rewrites it, the executor is a tool that runs it. Any error (parse, policy,
database) is fed back as an observation and the model writes again, up to max_sql_repairs.
"""

from __future__ import annotations

import logging
import time

from langgraph.graph import END, START, StateGraph

from ..db import QueryError, run_query
from ..llm import SqlOut
from ..prompts import SQL_SYSTEM, format_context, format_error_block
from ..semantic import Plan
from ..sqlguard import GuardError, guard
from ..tools import build_tools
from .state import BIState, Runtime, trace_event

log = logging.getLogger("bicopilot")

EXPLORE_SYSTEM = """You are the SQL agent of a BI copilot. Before writing SQL you may call tools to check a table's
columns, sample a column's values (to spell filters correctly) or validate a draft against the guard. Call only
what you need - at most {max_steps} tool calls - then reply with the final SQL only (no prose)."""


def build_sql_agent(rt: Runtime):
    dialect = rt.engine.dialect.name
    tools = build_tools(rt.engine, rt.settings.max_rows)

    def explore(state: BIState) -> BIState:
        """ReAct exploration with bound tools; the observations and the model's draft feed the write node."""
        started = time.perf_counter()
        if state.get("sql_attempts"):  # repairs reuse the first exploration
            return {"trace": trace_event(state, "sql.explore", started, skipped="repair")}
        if state.get("fast_path"):
            return {
                "tool_steps": [],
                "explored_sql": "",
                "trace": trace_event(state, "sql.explore", started, skipped="confident resolution"),
            }
        plan = Plan.model_validate(state["plan"])
        user = f"QUESTION: {state['question']}\nAS-OF: {state['as_of']}\nPLAN:\n{plan.model_dump_json(indent=2)}"
        try:
            draft, steps = rt.llm.react(
                EXPLORE_SYSTEM.format(max_steps=rt.settings.max_tool_steps),
                user,
                tools,
                rt.settings.max_tool_steps,
                {"plan": plan},
            )
        except Exception as exc:  # exploration is an aid; the write node has the compiler draft regardless
            log.warning("exploration skipped: %s: %s", type(exc).__name__, exc)
            return {
                "tool_steps": [],
                "explored_sql": "",
                "trace": trace_event(state, "sql.explore", started, skipped=f"{type(exc).__name__}: {str(exc)[:160]}"),
            }
        observations = [{"tool": s.tool, "args": s.args, "observation": s.observation[:800]} for s in steps]
        return {
            "tool_steps": observations,
            "explored_sql": draft,
            "trace": trace_event(state, "sql.explore", started, tools=[s.tool for s in steps]),
        }

    def write(state: BIState) -> BIState:
        started = time.perf_counter()
        plan = Plan.model_validate(state["plan"])
        attempts = list(state.get("sql_attempts", []))
        try:
            draft_sql, draft_notes = rt.semantic.compile_sql(plan)
        except ValueError as exc:
            draft_sql, draft_notes = "", [str(exc)]
        hint = state.get("critic_hint", "")
        if state.get("fast_path") and draft_sql and not attempts and not hint:
            return {
                "sql": draft_sql,
                "trace": trace_event(
                    state,
                    "sql.write",
                    started,
                    attempt=1,
                    rationale="compiled from the semantic layer (confident resolution)",
                ),
            }
        error_block = format_error_block(attempts)
        if hint:
            error_block += f"\nCRITIC FEEDBACK: {hint}\n"
        if state.get("tool_steps"):
            observed = "\n".join(f"- {o['tool']}({o['args']}) -> {o['observation'][:300]}" for o in state["tool_steps"])
            error_block += f"\nTOOL OBSERVATIONS\n{observed}\n"
        if state.get("explored_sql"):
            error_block += f"\nYOUR DRAFT AFTER EXPLORATION\n{state['explored_sql']}\n"
        if state.get("policy_note"):
            error_block += f"\n{state['policy_note']}\n"
        if state.get("attachment_text"):
            error_block += f"\nATTACHED CONTEXT\n{state['attachment_text'][:2000]}\n"
        system = SQL_SYSTEM.format(
            context=format_context(state.get("context_chunks", [])),
            rules=rt.semantic.rules_text(),
            draft_sql=draft_sql or "-- compiler could not produce a draft",
            draft_notes="; ".join(draft_notes) or "none",
            max_rows=rt.settings.max_rows,
            aliases=rt.semantic.aliases_text(),
            error_block=error_block,
        )
        user = f"QUESTION: {state['question']}\nAS-OF: {state['as_of']}\nPLAN:\n{plan.model_dump_json(indent=2)}"
        out: SqlOut = rt.llm.structured(
            "sql",
            SqlOut,
            system,
            user,
            {"plan": plan, "error": attempts[-1]["error"] if attempts else "", "hint": hint},
        )
        return {
            "sql": out.sql,
            "trace": trace_event(state, "sql.write", started, attempt=len(attempts) + 1, rationale=out.rationale[:200]),
        }

    def guard_node(state: BIState) -> BIState:
        started = time.perf_counter()
        attempts = list(state.get("sql_attempts", []))
        try:
            result = guard(state["sql"], target_dialect=dialect, max_rows=rt.settings.max_rows)
        except GuardError as exc:
            attempts.append({"sql": state["sql"], "error": f"guard: {exc}", "stage": "guard"})
            return {
                "sql_attempts": attempts,
                "exec_error": f"guard: {exc}",
                "trace": trace_event(state, "sql.guard", started, rejected=str(exc)),
            }
        final_sql, masked = result.sql, []
        if state.get("role") == "viewer":
            from ..access import Policy, rewrite_sql

            policy = Policy(role="viewer", masked=getattr(rt, "masked_columns", set()) or set())
            if policy.active:
                final_sql, masked = rewrite_sql(result.sql, policy, dialect)
        return {
            "sql": final_sql,
            "guard_warnings": result.warnings + ([f"masked: {', '.join(masked)}"] if masked else []),
            "exec_error": "",
            "trace": trace_event(
                state, "sql.guard", started, tables=sorted(result.tables), added_limit=result.added_limit
            ),
        }

    def execute(state: BIState) -> BIState:
        started = time.perf_counter()
        attempts = list(state.get("sql_attempts", []))
        try:
            result = run_query(rt.engine, state["sql"], rt.settings.max_rows, rt.settings.query_timeout_s)
        except QueryError as exc:
            attempts.append({"sql": state["sql"], "error": str(exc), "stage": "execute"})
            return {
                "sql_attempts": attempts,
                "exec_error": str(exc),
                "trace": trace_event(state, "sql.execute", started, error=str(exc)[:200]),
            }
        attempts.append({"sql": state["sql"], "error": "", "stage": "execute", "rows": result.row_count})
        return {
            "sql_attempts": attempts,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
            "exec_error": "",
            "trace": trace_event(state, "sql.execute", started, rows=result.row_count, truncated=result.truncated),
        }

    def after_guard(state: BIState) -> str:
        return "execute" if not state.get("exec_error") else "repair"

    def after_execute(state: BIState) -> str:
        return END if not state.get("exec_error") else "repair"

    def repair(state: BIState) -> BIState:
        started = time.perf_counter()
        repairs = state.get("repairs", 0) + 1
        return {"repairs": repairs, "trace": trace_event(state, "sql.repair", started, repairs=repairs)}

    def after_repair(state: BIState) -> str:
        return "write" if state.get("repairs", 0) <= rt.settings.max_sql_repairs else END

    g = StateGraph(BIState)
    g.add_node("explore", explore)
    g.add_node("write", write)
    g.add_node("guard", guard_node)
    g.add_node("execute", execute)
    g.add_node("repair", repair)
    g.add_edge(START, "explore")
    g.add_edge("explore", "write")
    g.add_edge("write", "guard")
    g.add_conditional_edges("guard", after_guard, {"execute": "execute", "repair": "repair"})
    g.add_conditional_edges("execute", after_execute, {END: END, "repair": "repair"})
    g.add_conditional_edges("repair", after_repair, {"write": "write", END: END})
    return g.compile()
