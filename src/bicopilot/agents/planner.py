"""PLANNER agent (sub-graph): resolve -> plan -> retrieve.

Pattern: Plan-and-Execute. The resolver does deterministic field mapping from the semantic
layer; the model corrects the draft and decides whether a clarifying question is needed; the
retriever pulls the definitions and table docs the SQL agent will need (hybrid RAG).
"""

from __future__ import annotations

import time
from datetime import date

from langgraph.graph import END, START, StateGraph

from ..llm import FakeLLM
from ..prompts import PLANNER_SYSTEM
from ..semantic import Plan
from .state import BIState, Runtime, trace_event


def build_planner(rt: Runtime):
    def resolve(state: BIState) -> BIState:
        started = time.perf_counter()
        question = state["question"]
        if state.get("intent") == "clarification_reply" and state.get("pending_question"):
            question = f"{state['pending_question']} ({state['question']})"
        as_of = date.fromisoformat(state["as_of"])
        res = rt.semantic.resolve(question, as_of)
        draft = rt.semantic.plan_from_resolution(res)
        if state.get("intent") == "clarification_reply" and draft.needs_clarification:
            # the reply itself may name the metric; try to pin it from the reply text
            reply = state["question"].lower()
            for mid, m in rt.semantic.metrics.items():
                if m["label"].lower() == reply.strip() or mid in reply or m["label"].lower() in reply:
                    draft.metrics = [mid]
                    draft.needs_clarification, draft.clarification, draft.options = False, "", []
                    break
        return {
            "question": question,
            "draft_plan": draft.model_dump(),
            "trace": trace_event(
                state, "planner.resolve", started, matched=res.matched_phrases, ambiguous=res.ambiguous
            ),
        }

    def plan(state: BIState) -> BIState:
        started = time.perf_counter()
        draft = Plan.model_validate(state["draft_plan"])
        as_of = date.fromisoformat(state["as_of"])
        fy, fq = rt.semantic.fiscal_of(as_of)
        system = PLANNER_SYSTEM.format(
            catalog=rt.semantic.catalog_text(), rules=rt.semantic.rules_text(), as_of=as_of, fy=fy, fq=fq
        )
        user = f"QUESTION: {state['question']}\n\nDRAFT PLAN (from alias resolver):\n{draft.model_dump_json(indent=2)}"
        if isinstance(rt.llm, FakeLLM):
            final = draft
        else:
            final = rt.llm.structured("plan", Plan, system, user, {"draft_plan": draft, "question": state["question"]})
            # the model may not invent ids; anything unknown falls back to the draft
            final.metrics = [m for m in final.metrics if m in rt.semantic.metrics] or draft.metrics
            final.dimensions = [d for d in final.dimensions if d in rt.semantic.dimensions]
            if not final.metrics and not final.needs_clarification:
                final = draft
        if not final.metrics and not final.needs_clarification:
            final.needs_clarification = True
            final.clarification = "Which metric should I use?"
            final.options = ["Revenue", "Orders", "Units sold", "Gross margin %"]
        return {
            "plan": final.model_dump(),
            "awaiting_clarification": final.needs_clarification,
            "clarification": final.clarification,
            "options": final.options,
            "trace": trace_event(
                state,
                "planner.plan",
                started,
                metrics=final.metrics,
                dimensions=final.dimensions,
                clarify=final.needs_clarification,
            ),
        }

    def retrieve(state: BIState) -> BIState:
        started = time.perf_counter()
        plan = Plan.model_validate(state["plan"])
        hits = rt.retriever.search(state["question"], plan.metrics, plan.dimensions)
        chunks = [
            {
                "id": h.chunk.id,
                "title": h.chunk.title,
                "text": h.chunk.text,
                "kind": h.chunk.kind,
                "structured": h.structured,
            }
            for h in hits
        ]
        meta = {
            "backend": rt.retriever.backend,
            "hits": [
                {
                    "id": h.chunk.id,
                    "score": round(h.score, 4),
                    "bm25_rank": h.bm25_rank,
                    "dense_rank": h.dense_rank,
                    "structured": h.structured,
                    "rerank": None
                    if h.rerank_after is None
                    else {"score": h.rerank_score, "before": h.rerank_before, "after": h.rerank_after},
                }
                for h in hits
            ],
            "reranker": rt.retriever.reranker.name,
        }
        return {
            "context_chunks": chunks,
            "retrieval_meta": meta,
            "trace": trace_event(state, "planner.retrieve", started, chunks=len(chunks)),
        }

    g = StateGraph(BIState)
    g.add_node("resolve", resolve)
    g.add_node("plan", plan)
    g.add_node("retrieve", retrieve)
    g.add_edge(START, "resolve")
    g.add_edge("resolve", "plan")
    g.add_conditional_edges(
        "plan", lambda s: "retrieve" if not s.get("awaiting_clarification") else END, {"retrieve": "retrieve", END: END}
    )
    g.add_edge("retrieve", END)
    return g.compile()
