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

import time

from langgraph.graph import END, START, StateGraph

from ..llm import RouterOut
from ..prompts import ROUTER_SYSTEM
from .analyst import build_analyst
from .planner import build_planner
from .sql_agent import build_sql_agent
from .state import BIState, Runtime, trace_event

META_ANSWER = (
    "I answer questions about sales data: revenue, gross margin (USD or %), units, orders, average order value, "
    "active customers, discounts, cancellation and return rates - by region, country, segment, industry, tier, "
    "customer, product category/family/product, channel, sales rep or team, and by fiscal year, fiscal quarter or month. "
    'Try: "revenue by region FY2026", "top 5 customers by gross margin last quarter", "monthly revenue trend in APAC".'
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
            return {"answer": META_ANSWER, "status": "meta", "trace": trace_event(state, "respond", started)}
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
