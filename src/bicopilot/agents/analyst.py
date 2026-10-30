"""ANALYST agent (sub-graph): interpret -> critic.

The analyst turns rows into a business answer and a chart spec. The critic (Reflection pattern)
checks the answer against question, plan, SQL and rows; a "revise" verdict sends the supervisor
back to the SQL agent with a concrete hint, at most max_critic_loops times.
"""

from __future__ import annotations

import json
import time

from langgraph.graph import END, START, StateGraph

from ..llm import AnalysisOut, CriticOut
from ..prompts import ANALYST_SYSTEM, CRITIC_SYSTEM
from ..semantic import Plan
from .state import BIState, Runtime, trace_event

SAMPLE_ROWS = 50


def build_analyst(rt: Runtime):
    def interpret(state: BIState) -> BIState:
        started = time.perf_counter()
        plan = Plan.model_validate(state["plan"])
        columns, rows = state.get("columns", []), state.get("rows", [])
        sample = [dict(zip(columns, r)) for r in rows[:SAMPLE_ROWS]]
        user = (
            f"QUESTION: {state['question']}\nAS-OF: {state['as_of']}\nPLAN: {plan.model_dump_json()}\n"
            f"SQL:\n{state.get('sql', '')}\nROW COUNT: {state.get('row_count', 0)} (truncated={state.get('truncated', False)})\n"
            f"ROWS (first {SAMPLE_ROWS}):\n{json.dumps(sample, default=str)}"
        )
        out: AnalysisOut = rt.llm.structured(
            "analyze",
            AnalysisOut,
            ANALYST_SYSTEM,
            user,
            {"plan": plan, "columns": columns, "rows": rows, "row_count": state.get("row_count", 0)},
        )
        if plan.time and plan.time.label and not any(plan.time.label in c for c in out.caveats):
            label = plan.time.label
            resolved = []
            if plan.time.fiscal_year or plan.time.fiscal_quarter:
                resolved.append(rt.semantic.period_label(plan.time.fiscal_year, plan.time.fiscal_quarter).strip())
            if plan.time.year_month_from:
                resolved.append(f"{plan.time.year_month_from}..{plan.time.year_month_to}")
            if resolved and label.lower() not in " ".join(resolved).lower():
                note = (
                    "calendar periods, as of " + state["as_of"]
                    if rt.semantic.calendar
                    else "fiscal calendar, FY starts April"
                )
                out.caveats.append(f'"{label}" resolved to {" ".join(resolved)} ({note}).')
        if state.get("truncated"):
            out.caveats.append(f"Result capped at {rt.settings.max_rows} rows.")
        return {
            "analysis": out.model_dump(),
            "trace": trace_event(state, "analyst.interpret", started, chart=out.chart.type),
        }

    def critic(state: BIState) -> BIState:
        started = time.perf_counter()
        loops = state.get("critic_loops", 0)
        plan = Plan.model_validate(state["plan"])
        analysis = state.get("analysis", {})
        columns, rows = state.get("columns", []), state.get("rows", [])
        user = (
            f"QUESTION: {state['question']}\nPLAN: {plan.model_dump_json()}\nSQL:\n{state.get('sql', '')}\n"
            f"ROW COUNT: {state.get('row_count', 0)}\nROWS (first 20): {json.dumps([dict(zip(columns, r)) for r in rows[:20]], default=str)}\n"
            f"DRAFT ANSWER: {json.dumps(analysis)}"
        )
        out: CriticOut = rt.llm.structured(
            "critic",
            CriticOut,
            CRITIC_SYSTEM,
            user,
            {"plan": plan, "row_count": state.get("row_count", 0), "critic_looped": loops > 0},
        )
        revise = out.verdict == "revise" and loops < rt.settings.max_critic_loops
        return {
            "critic": out.model_dump(),
            "critic_loops": loops + 1,
            "critic_hint": out.fix_hint if revise else "",
            "trace": trace_event(state, "analyst.critic", started, verdict=out.verdict, reason=out.reason[:160]),
        }

    g = StateGraph(BIState)
    g.add_node("interpret", interpret)
    g.add_node("critic", critic)
    g.add_edge(START, "interpret")
    g.add_edge("interpret", "critic")
    g.add_edge("critic", END)
    return g.compile()
