"""Hybrid retrieval over the semantic layer: keyword (BM25) + dense (embeddings) fused with RRF,
plus structured lookup of the metrics/dimensions the resolver already mapped.

Chunks = glossary sections + one card per metric + one card per dimension. A few dozen chunks,
so the keyword index lives in memory. Vectors live in memory (numpy) or in Postgres/pgvector.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import Engine, text

from .config import Settings
from .rerank import Candidate, Reranker
from .semantic import SemanticLayer

RRF_K = 60
ALWAYS = ("g:table-fact-orders", "g:table-fact-order-items", "g:table-dim-date")  # every query touches these
_TOKEN = re.compile(r"[a-z0-9%]+")
STOP = {"the", "a", "an", "of", "in", "by", "for", "and", "or", "to", "is", "are", "what", "which", "show", "me", "per"}


def tokenize(s: str) -> list[str]:
    return [t for t in _TOKEN.findall(s.lower()) if t not in STOP]


@dataclass
class Chunk:
    id: str
    title: str
    text: str
    kind: str  # glossary | metric | dimension


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    structured: bool = False
    rerank_score: float | None = None
    rerank_before: int | None = None
    rerank_after: int | None = None


# ----- embeddings --------------------------------------------------------------------------------
class Embedder:
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class HashEmbedder(Embedder):
    """Deterministic bag-of-hashed-tokens vector. No network. Good enough to exercise the dense path;
    keyword search carries real retrieval quality when no embedding key is configured."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                h = int(hashlib.blake2b(tok.encode(), digest_size=4).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
            n = np.linalg.norm(out[i]) or 1.0
            out[i] /= n
        return out


class OpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings):
        from langchain_openai import OpenAIEmbeddings

        kwargs = {"model": settings.embedding_model, "api_key": settings.llm_api_key}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = OpenAIEmbeddings(**kwargs)
        self.dim = settings.embedding_dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.asarray(self.client.embed_documents(texts), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai" and settings.llm_api_key:
        return OpenAIEmbedder(settings)
    return HashEmbedder()


# ----- vector stores -----------------------------------------------------------------------------
class MemoryVectorStore:
    def __init__(self):
        self.ids: list[str] = []
        self.matrix = np.zeros((0, 0), dtype=np.float32)

    def upsert(self, ids: list[str], vectors: np.ndarray) -> None:
        self.ids, self.matrix = list(ids), vectors

    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self.matrix.size == 0:
            return []
        scores = self.matrix @ query
        order = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in order]


class PgVectorStore:
    """pgvector-backed store. Table is created on first use; cosine distance via the <=> operator."""

    def __init__(self, engine: Engine, dim: int, table: str = "semantic_chunks"):
        self.engine, self.dim, self.table = engine, dim, table
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, title TEXT, body TEXT, "
                    f"embedding vector({dim}))"
                )
            )
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS hash TEXT"))

    def existing_hashes(self) -> dict[str, str]:
        """id -> content hash of what is already embedded, so an unchanged chunk is not re-embedded on restart."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"SELECT id, hash FROM {self.table}")).all()
        return {r[0]: r[1] or "" for r in rows}

    def upsert(
        self,
        ids: list[str],
        vectors: np.ndarray,
        titles: list[str] | None = None,
        bodies: list[str] | None = None,
        hashes: list[str] | None = None,
    ) -> None:
        titles = titles or [""] * len(ids)
        bodies = bodies or [""] * len(ids)
        hashes = hashes or [""] * len(ids)
        if not ids:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {self.table} (id, title, body, embedding, hash) "
                    "VALUES (:id, :t, :b, CAST(:e AS vector), :h) "
                    "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, "
                    "embedding = EXCLUDED.embedding, hash = EXCLUDED.hash"
                ),
                [
                    {"id": cid, "t": titles[i], "b": bodies[i], "e": str(vectors[i].tolist()), "h": hashes[i]}
                    for i, cid in enumerate(ids)
                ],
            )

    def delete(self, ids: list[str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.table} WHERE id = ANY(:ids)"), {"ids": list(ids)})

    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score FROM {self.table} ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
                ),
                {"q": str(query.tolist()), "k": k},
            ).all()
        return [(r[0], float(r[1])) for r in rows]


class MilvusVectorStore:
    """Milvus-backed store. `uri` is a local file (Milvus Lite, embedded) or a server/Zilliz endpoint."""

    def __init__(self, uri: str, dim: int, collection: str = "semantic_chunks", token: str = ""):
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri=uri, token=token or None)
        self.collection = collection
        if self.client.has_collection(collection):
            self.client.drop_collection(collection)  # chunks are rebuilt from the semantic layer on startup
        self.client.create_collection(
            collection, dimension=dim, metric_type="COSINE", id_type="string", max_length=128, auto_id=False
        )

    def upsert(self, ids: list[str], vectors: np.ndarray) -> None:
        rows = [{"id": cid, "vector": vectors[i].tolist()} for i, cid in enumerate(ids)]
        self.client.insert(self.collection, rows)

    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        hits = self.client.search(self.collection, data=[query.tolist()], limit=k, output_fields=["id"])
        return [(h["id"], float(h["distance"])) for h in hits[0]] if hits else []


# ----- keyword index -----------------------------------------------------------------------------
class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / len(docs) if docs else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, query: list[str]) -> list[float]:
        out = []
        for tf, ln in zip(self.tf, self.len):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / (self.avg or 1)))
            out.append(s)
        return out


# ----- retriever ---------------------------------------------------------------------------------
def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def build_chunks(semantic: SemanticLayer, semantic_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    glossary = (semantic_dir / "glossary.md").read_text(encoding="utf-8")
    for section in re.split(r"^## ", glossary, flags=re.M)[1:]:
        title, _, body = section.partition("\n")
        chunks.append(Chunk(id="g:" + slug(title), title=title.strip(), text=body.strip(), kind="glossary"))
    for m in semantic.metrics:
        chunks.append(
            Chunk(
                id=f"m:{m}",
                title=f"Metric {m}",
                text=semantic.metric_card(m) + " Aliases: " + ", ".join(semantic.metrics[m].get("aliases", [])),
                kind="metric",
            )
        )
    for d in semantic.dimensions:
        chunks.append(
            Chunk(
                id=f"d:{d}",
                title=f"Dimension {d}",
                text=semantic.dimension_card(d) + " Aliases: " + ", ".join(semantic.dimensions[d].get("aliases", [])),
                kind="dimension",
            )
        )
    return chunks


class Retriever:
    def __init__(
        self,
        semantic: SemanticLayer,
        settings: Settings,
        engine: Engine | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        extra_chunks: list[tuple[str, str, str]] | None = None,
    ):
        self.semantic = semantic
        self.settings = settings
        self.chunks = build_chunks(semantic, settings.semantic_dir)
        for cid, title, body in extra_chunks or []:
            self.chunks.append(Chunk(id=cid, title=title, text=body, kind="dictionary"))
        self.by_id = {c.id: c for c in self.chunks}
        self.bm25 = BM25([tokenize(c.title + " " + c.text) for c in self.chunks])
        self.embedder = embedder or make_embedder(settings)
        self.store: MemoryVectorStore | PgVectorStore | MilvusVectorStore
        if settings.vector_backend == "pgvector" and engine is not None and engine.dialect.name == "postgresql":
            self.store = PgVectorStore(engine, self.embedder.dim)
        elif settings.vector_backend == "milvus":
            self.store = MilvusVectorStore(settings.milvus_uri, self.embedder.dim, token=settings.milvus_token)
        else:
            self.store = MemoryVectorStore()
        self.reranker = reranker or Reranker()
        texts = [c.title + "\n" + c.text for c in self.chunks]
        ids = [c.id for c in self.chunks]
        if isinstance(self.store, PgVectorStore):
            # Persistent store: embed only chunks whose text changed since the last start. On a warm restart
            # this is zero embedding calls and zero writes, which keeps a free-tier cold start short.
            hashes = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
            known = self.store.existing_hashes()
            todo = [i for i, cid in enumerate(ids) if known.get(cid) != hashes[i]]
            stale = [cid for cid in known if cid not in set(ids)]
            if todo:
                vectors = self.embedder.embed([texts[i] for i in todo])
                self.store.upsert(
                    [ids[i] for i in todo],
                    vectors,
                    [self.chunks[i].title for i in todo],
                    [self.chunks[i].text for i in todo],
                    [hashes[i] for i in todo],
                )
            if stale:
                self.store.delete(stale)
        else:
            self.store.upsert(ids, self.embedder.embed(texts))

    @property
    def backend(self) -> str:
        return type(self.store).__name__.replace("VectorStore", "").lower()

    def set_reranker(self, reranker: Reranker) -> None:
        self.reranker = reranker

    def search(
        self, question: str, metric_ids: list[str] = (), dimension_ids: list[str] = (), k: int | None = None
    ) -> list[Hit]:
        k = k or self.settings.top_k_chunks
        q_tokens = tokenize(question)
        bm25 = self.bm25.score(q_tokens)
        bm25_rank = {
            self.chunks[i].id: r + 1
            for r, i in enumerate(sorted(range(len(bm25)), key=lambda i: -bm25[i]))
            if bm25[i] > 0
        }
        dense = self.store.search(self.embedder.embed([question])[0], k=max(k * 3, 10))
        dense_rank = {cid: r + 1 for r, (cid, _) in enumerate(dense)}
        fused: dict[str, float] = {}
        for cid, r in bm25_rank.items():
            fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + r)
        for cid, r in dense_rank.items():
            fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + r)
        # structured hits: whatever the resolver mapped always comes along, with a bonus
        structured = {f"m:{m}" for m in metric_ids} | {f"d:{d}" for d in dimension_ids}
        for cid in structured:
            fused[cid] = fused.get(cid, 0.0) + 1.0
        # the table docs every SQL needs
        for cid in ALWAYS:
            fused[cid] = fused.get(cid, 0.0) + 0.5
        keep = k + len(structured)
        ranked = [(cid, s) for cid, s in sorted(fused.items(), key=lambda kv: -kv[1]) if cid in self.by_id]
        hits = [
            Hit(self.by_id[cid], s, bm25_rank.get(cid), dense_rank.get(cid), cid in structured) for cid, s in ranked
        ]
        if self.reranker.name == "none":
            return hits[:keep]
        # structured hits and the base table docs are pinned; the reranker reorders the rest of a wider pool
        pinned = [h for h in hits if h.structured or h.chunk.id in ALWAYS]
        pool = [h for h in hits if not (h.structured or h.chunk.id in ALWAYS)][: max(keep * 3, 12)]
        reranked = self.reranker.rerank(
            question,
            [Candidate(h.chunk.id, h.chunk.title, h.chunk.text) for h in pool],
            keep=max(keep - len(pinned), 0),
        )
        by_id = {h.chunk.id: h for h in pool}
        out = list(pinned)
        for r in reranked:
            h = by_id[r.id]
            h.rerank_score, h.rerank_before, h.rerank_after = r.score, r.before, r.after
            out.append(h)
        return out
