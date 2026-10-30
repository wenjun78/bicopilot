"""SUPERVISOR graph: routes the question through the three specialist agents.

    START -> router -> planner_agent -> sql_agent -> analyst_agent -> respond -> END
                 |           |              |              |
                 |           |              |              +-- critic says "revise" -> sql_agent (once)
                 |           |              +-- unrecoverable SQL error -> respond (error)
                 |           +-- needs clarification -> respond (clarify)
                 +-- greeting / meta / out_of_scope -> respond

Each agent is its own compiled LangGraph sub-graph added as a node, so they can be tested, traced
and swapped independently (multi-agent collaboration on one shared state).
"""

from __future__ import annotations

import re
import time

from langgraph.graph import END, START, StateGraph

from ..llm import RouterOut
from ..prompts import ROUTER_SYSTEM
from .analyst import build_analyst
from .planner import build_planner
from .sql_agent import build_sql_agent
from .state import BIState, Runtime, trace_event

SCHEMA_WORDS = (
    "column",
    "columns",
    "table",
    "tables",
    "field",
    "fields",
    "schema",
    "data dictionary",
    "dictionary",
    "what does",
    "meaning of",
    "defined",
    "definition of",
)


def schema_answer(rt: Runtime, question: str) -> str:
    """Answer a question about the warehouse itself from the structured data dictionary."""
    d = rt.dictionary or {}
    tables = d.get("tables", [])
    q = question.lower()
    if not tables:
        return META_ANSWER
    if re.search(r"\b(what|which|list|show)\b.*\btables?\b", q) and not re.search(r"\bcolumns?\b", q):
        lines = [f"- {t['name']} ({t['row_count']:,} rows): {t['description']}" for t in tables]
        return (
            "The warehouse has these tables:\n"
            + "\n".join(lines)
            + "\n\nThe Dictionary tab shows every column with a profile."
        )
    q_tokens = {w.strip("?.,") for w in q.split()} - {
        "the",
        "a",
        "an",
        "of",
        "is",
        "what",
        "which",
        "does",
        "do",
        "me",
        "tells",
        "tell",
        "column",
        "columns",
        "table",
        "tables",
        "mean",
        "means",
        "if",
        "was",
        "for",
        "in",
        "field",
        "fields",
        "has",
        "shows",
        "holds",
        "to",
        "how",
        "know",
        "order",
        "orders",
        "customer",
        "customers",
        "product",
        "products",
        "that",
        "this",
        "it",
        "or",
        "and",
        "with",
    }

    def stem(w: str) -> str:
        return w[:-1] if len(w) > 4 and w.endswith("s") else w

    q_stems = {stem(t) for t in q_tokens if len(t) > 2}
    scored: list[tuple[int, dict, dict]] = []
    for t in tables:
        for c in t["columns"]:
            name_words = {stem(w) for w in c["name"].split("_")}
            desc_words = {stem(w.strip("(),.;:")) for w in c["description"].lower().split()}
            score = 0
            for tok in q_stems:
                if tok in name_words:
                    score += 3
                elif tok in desc_words:
                    score += 1
            if score and t["kind"] == "fact":
                score += 1  # facts hold the measures people usually ask about
            if score:
                scored.append((score, t, c))
    if not scored:
        return META_ANSWER
    scored.sort(key=lambda x: -x[0])
    lines = []
    for _score, t, c in scored[:3]:
        extra = []
        if c.get("joins_to"):
            extra.append(f"joins to {c['joins_to']}")
        if c.get("samples") and c["samples"][0].get("count") is not None:
            extra.append("values: " + ", ".join(s["value"] for s in c["samples"][:6]))
        if c.get("null_pct"):
            extra.append(f"{c['null_pct']}% null")
        if c.get("used_by"):
            extra.append("used by " + ", ".join(u.split(":", 1)[1] for u in c["used_by"][:4]))
        lines.append(
            f"- {t['name']}.{c['name']} ({c['type']}): {c['description']}" + (f" [{'; '.join(extra)}]" if extra else "")
        )
    return (
        "From the data dictionary:\n"
        + "\n".join(lines)
        + "\n\nThe Dictionary tab lists every table and column with profiles."
    )


META_ANSWER = (
    "I answer questions about the Olist e-commerce warehouse (Brazil, 2016-2018, BRL): revenue (GMV), freight, "
    "orders, items, customers and repeat rate, average order value, review score, on-time and late delivery, delivery "
    "days, cancellation rate, payment types and installments - by state, region, city, category, category group, "
    "seller, payment type, status, and by year, quarter or month. "
    'Try: "revenue by state 2017", "top 5 categories by revenue last quarter", "late delivery rate by region 2018".'
)


def build_graph(rt: Runtime):
    planner_agent = build_planner(rt)
    sql_agent = build_sql_agent(rt)
    analyst_agent = build_analyst(rt)

    def router(state: BIState) -> BIState:
        started = time.perf_counter()
        history = state.get("history", [])
        awaiting = bool(state.get("pending_question"))
        user = f"LATEST MESSAGE: {state['question']}\nPREVIOUS TURNS: {history[-4:]}\nCOPILOT JUST ASKED A CLARIFYING QUESTION: {awaiting}"
        out: RouterOut = rt.llm.structured(
            "route", RouterOut, ROUTER_SYSTEM, user, {"question": state["question"], "awaiting_clarification": awaiting}
        )
        intent = out.intent
        if intent == "clarification_reply" and not awaiting:
            intent = "data_question"
        return {
            "intent": intent,
            "trace": trace_event(state, "router", started, intent=intent),
            "sql_attempts": [],
            "repairs": 0,
            "critic_loops": 0,
            "critic_hint": "",
            "exec_error": "",
        }

    def after_router(state: BIState) -> str:
        return "planner_agent" if state["intent"] in ("data_question", "clarification_reply") else "respond"

    def after_planner(state: BIState) -> str:
        return "respond" if state.get("awaiting_clarification") else "sql_agent"

    def after_sql(state: BIState) -> str:
        return "respond" if state.get("exec_error") else "analyst_agent"

    def after_analyst(state: BIState) -> str:
        return "sql_agent" if state.get("critic_hint") else "respond"

    def respond(state: BIState) -> BIState:
        started = time.perf_counter()
        intent = state.get("intent", "")
        if intent == "greeting":
            return {
                "answer": "Hi! Ask me about revenue, margin, orders or customers - by region, product, segment or fiscal period.",
                "status": "greeting",
                "trace": trace_event(state, "respond", started),
            }
        if intent == "meta":
            q = state["question"].lower()
            answer = schema_answer(rt, state["question"]) if any(w in q for w in SCHEMA_WORDS) else META_ANSWER
            return {"answer": answer, "status": "meta", "trace": trace_event(state, "respond", started)}
        if intent == "out_of_scope":
            return {
                "answer": "That is outside the sales warehouse I can query. I can answer questions about revenue, margin, orders, customers and products.",
                "status": "refused",
                "trace": trace_event(state, "respond", started),
            }
        if state.get("awaiting_clarification"):
            return {
                "answer": state.get("clarification", "Could you clarify?"),
                "status": "clarify",
                "pending_question": state["question"],
                "trace": trace_event(state, "respond", started),
            }
        if state.get("exec_error"):
            attempts = state.get("sql_attempts", [])
            msg = f"I could not get a valid query after {len(attempts)} attempt(s). Last error: {state['exec_error']}"
            return {
                "answer": msg,
                "status": "error",
                "pending_question": "",
                "trace": trace_event(state, "respond", started),
            }
        analysis = state.get("analysis", {})
        answer = analysis.get("summary", "")
        if analysis.get("insights"):
            answer += "\n\n" + "\n".join(f"- {i}" for i in analysis["insights"])
        if analysis.get("caveats"):
            answer += "\n\n" + "\n".join(f"Note: {c}" for c in analysis["caveats"])
        return {
            "answer": answer,
            "status": "answered",
            "pending_question": "",
            "trace": trace_event(state, "respond", started),
        }

    g = StateGraph(BIState)
    g.add_node("router", router)
    g.add_node("planner_agent", planner_agent)
    g.add_node("sql_agent", sql_agent)
    g.add_node("analyst_agent", analyst_agent)
    g.add_node("respond", respond)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", after_router, {"planner_agent": "planner_agent", "respond": "respond"})
    g.add_conditional_edges("planner_agent", after_planner, {"respond": "respond", "sql_agent": "sql_agent"})
    g.add_conditional_edges("sql_agent", after_sql, {"respond": "respond", "analyst_agent": "analyst_agent"})
    g.add_conditional_edges("analyst_agent", after_analyst, {"sql_agent": "sql_agent", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()
