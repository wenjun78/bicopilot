import tempfile

import pytest

from bicopilot.config import Settings
from bicopilot.rerank import Candidate, LexicalReranker, Reranker, make_reranker
from bicopilot.retrieval import Retriever
from bicopilot.semantic import SemanticLayer


def test_lexical_reranker_prefers_title_matches():
    cands = [
        Candidate("a", "Table dim_product", "product master with category and family"),
        Candidate("b", "Definition: gross margin", "gross margin is revenue minus cost"),
        Candidate("c", "Definition: revenue", "sum of net amount"),
    ]
    out = LexicalReranker().rerank("gross margin by product category", cands, keep=2)
    assert [r.id for r in out] == ["b", "a"]
    assert out[0].before == 2 and out[0].after == 1


def test_noop_reranker_keeps_order():
    cands = [Candidate(str(i), f"t{i}", "x") for i in range(4)]
    assert [r.id for r in Reranker().rerank("q", cands, keep=3)] == ["0", "1", "2"]


def test_make_reranker_falls_back_without_key():
    assert make_reranker(Settings(reranker="llm"), llm=None).name == "lexical"
    assert make_reranker(Settings(reranker="none")).name == "none"


def test_retrieval_trace_records_rerank_positions(copilot):
    r = copilot.ask("gross margin % by product category FY26")
    hits = r["retrieval"]["hits"]
    assert r["retrieval"]["reranker"] == "lexical"
    ids = [h["id"] for h in hits]
    # structured hits and base table docs are pinned, the rest carry rerank positions
    assert ids[:2] == ["m:gross_margin_pct", "d:product_category"]
    assert "g:table-fact-orders" in ids and "g:table-dim-date" in ids
    assert all(
        h["rerank"]
        for h in hits
        if not h["structured"] and not h["id"].startswith("g:table-fact") and not h["id"].startswith("g:table-dim-date")
    )


def test_milvus_lite_store_matches_memory_store():
    pytest.importorskip("pymilvus")
    d = tempfile.mkdtemp()
    semantic = SemanticLayer.load(Settings().semantic_dir)
    mem = Retriever(semantic, Settings(vector_backend="memory"))
    mil = Retriever(semantic, Settings(vector_backend="milvus", milvus_uri=f"{d}/m.db"))
    assert mil.backend == "milvus"
    q = "cancellation rate by channel"
    assert [h.chunk.id for h in mem.search(q)][:4] == [h.chunk.id for h in mil.search(q)][:4]
