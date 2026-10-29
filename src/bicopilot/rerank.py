"""Reranking: a second, more expensive pass over the fused candidate list.

Hybrid retrieval (BM25 + dense, RRF) is cheap and recall-oriented; the reranker reorders the top
candidates by actual relevance to the question before they go into the SQL agent's context.

Backends, selected by RERANKER:
- "llm":       the chat model scores each candidate 0-100 with a structured output (needs a key)
- "flashrank": a small ONNX cross-encoder (ms-marco MiniLM) run locally, no key; `pip install flashrank`
- "lexical":   deterministic token-overlap scoring, so the stage runs (and is traced) in CI and the demo
- "none":      stage skipped

Every backend returns the candidates in new order plus a score per chunk; the trace records
the pre- and post-rerank position so the effect is inspectable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings

_TOKEN = re.compile(r"[a-z0-9%]+")


@dataclass
class Candidate:
    id: str
    title: str
    text: str


@dataclass
class Reranked:
    id: str
    score: float
    before: int
    after: int


class RerankScores(BaseModel):
    scores: list[int] = Field(description="relevance 0-100 for each candidate, in the order given")


class Reranker:
    name = "none"

    def rerank(self, question: str, candidates: list[Candidate], keep: int) -> list[Reranked]:
        return [Reranked(c.id, 0.0, i + 1, i + 1) for i, c in enumerate(candidates[:keep])]


class LexicalReranker(Reranker):
    """Deterministic: overlap of question tokens with the chunk, title matches weighted double."""

    name = "lexical"

    def rerank(self, question: str, candidates: list[Candidate], keep: int) -> list[Reranked]:
        q = set(_TOKEN.findall(question.lower()))
        scored = []
        for i, c in enumerate(candidates):
            title = set(_TOKEN.findall(c.title.lower()))
            body = set(_TOKEN.findall(c.text.lower()))
            score = 2 * len(q & title) + len(q & body)
            scored.append((score / max(len(q), 1), i, c))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [Reranked(c.id, round(s, 4), i + 1, pos + 1) for pos, (s, i, c) in enumerate(scored[:keep])]


class FlashRankReranker(Reranker):
    name = "flashrank"

    def __init__(self, model: str = "ms-marco-MiniLM-L-12-v2"):
        from flashrank import Ranker, RerankRequest

        self._ranker = Ranker(model_name=model)
        self._request = RerankRequest

    def rerank(self, question: str, candidates: list[Candidate], keep: int) -> list[Reranked]:
        passages = [{"id": i, "text": f"{c.title}\n{c.text}"} for i, c in enumerate(candidates)]
        results = self._ranker.rerank(self._request(query=question, passages=passages))
        out = []
        for pos, r in enumerate(results[:keep]):
            i = int(r["id"])
            out.append(Reranked(candidates[i].id, round(float(r["score"]), 4), i + 1, pos + 1))
        return out


class LLMReranker(Reranker):
    name = "llm"

    def __init__(self, llm: Any):
        self.llm = llm

    def rerank(self, question: str, candidates: list[Candidate], keep: int) -> list[Reranked]:
        listing = "\n\n".join(f"[{i + 1}] {c.title}\n{c.text[:600]}" for i, c in enumerate(candidates))
        system = (
            "You rerank documentation chunks for a text-to-SQL agent. Score each candidate 0-100 by how much "
            "it is needed to write correct SQL for the question (metric definitions and table docs the query "
            "touches score high; unrelated metrics score low). Return one score per candidate, same order."
        )
        out: RerankScores = self.llm.structured(
            "rerank", RerankScores, system, f"QUESTION: {question}\n\n{listing}", {}
        )
        scores = list(out.scores) + [0] * (len(candidates) - len(out.scores))
        order = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
        return [Reranked(candidates[i].id, float(scores[i]), i + 1, pos + 1) for pos, i in enumerate(order[:keep])]


def make_reranker(settings: Settings, llm: Any | None = None) -> Reranker:
    choice = settings.reranker
    if choice == "llm" and llm is not None and getattr(llm, "name", "") == "openai":
        return LLMReranker(llm)
    if choice == "flashrank":
        try:
            return FlashRankReranker()
        except Exception:  # package or model missing: fall through to the deterministic stage
            pass
    if choice in ("llm", "flashrank", "lexical"):
        return LexicalReranker()
    return Reranker()
