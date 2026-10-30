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
from ..rerank import LexicalReranker
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
        if state.get("attachment_text") and not res.metrics:
            # a pasted KPI card or table often names the metric the question only points at ("how did this do")
            extra = rt.semantic.resolve(state["attachment_text"][:1500], as_of)
            res.metrics = extra.metrics[:1]
            for f in extra.filters:
                if f.dimension not in {x.dimension for x in res.filters}:
                    res.filters.append(f)
            if res.time is None:
                res.time = extra.time
        draft = rt.semantic.plan_from_resolution(res)
        if state.get("intent") == "clarification_reply" and draft.needs_clarification:
            # the reply itself may name the metric; try to pin it from the reply text
            reply = state["question"].lower()
            for mid, m in rt.semantic.metrics.items():
                if m["label"].lower() == reply.strip() or mid in reply or m["label"].lower() in reply:
                    draft.metrics = [mid]
                    draft.needs_clarification, draft.clarification, draft.options = False, "", []
                    break
        # Confident resolution: the layer named the metric, nothing was ambiguous, no word of the question was
        # left unexplained and nothing extra (attachment, clarification reply) is in play -> deterministic plan
        # and SQL, model only for the analysis and the critic. This is what makes the common question fast.
        fast = bool(
            draft.metrics
            and not draft.needs_clarification
            and not res.ambiguous
            and not res.residual
            and not state.get("attachment_text")
            and state.get("intent") != "clarification_reply"
        )
        return {
            "question": question,
            "draft_plan": draft.model_dump(),
            "fast_path": fast,
            "trace": trace_event(
                state,
                "planner.resolve",
                started,
                matched=res.matched_phrases,
                ambiguous=res.ambiguous,
                unexplained=res.residual,
                fast_path=fast,
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
        if state.get("attachment_text"):
            user += f"\n\nATTACHED CONTEXT (from a pasted file; use it to fill in metrics, periods, categories or filters the question refers to):\n{state['attachment_text'][:3000]}"
        if state.get("policy_note"):
            user += f"\n\n{state['policy_note']}"
        if isinstance(rt.llm, FakeLLM) or state.get("fast_path"):
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
            final.options = [rt.semantic.metrics[m]["label"] for m in list(rt.semantic.metrics)[:4]]
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
                model="skipped (confident resolution)"
                if state.get("fast_path") and not isinstance(rt.llm, FakeLLM)
                else "used",
            ),
        }

    def retrieve(state: BIState) -> BIState:
        started = time.perf_counter()
        plan = Plan.model_validate(state["plan"])
        fast = bool(state.get("fast_path"))
        # On the fast path the SQL is compiled, so the retrieved context only documents the answer; the
        # lexical reranker gives the same ranks for that purpose without a model round trip.
        override = LexicalReranker() if fast and rt.retriever.reranker.name == "llm" else None
        hits = rt.retriever.search(state["question"], plan.metrics, plan.dimensions, reranker=override)
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
            "reranker": (override or rt.retriever.reranker).name + (" (fast path)" if override else ""),
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
