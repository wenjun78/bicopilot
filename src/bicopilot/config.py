"""Settings. Everything has a zero-infra default so `make demo` runs with no key, no Docker, no Postgres."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_root() -> Path:
    """Project root = where semantic/ and data/ live. Editable installs resolve it from this file;
    a regular `pip install .` (Docker) sets BICOPILOT_ROOT or runs from the project directory."""
    env = os.environ.get("BICOPILOT_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] if len(here.parents) > 2 else here.parent, Path.cwd()):
        if (candidate / "semantic" / "metrics.yaml").exists():
            return candidate
    return Path.cwd()


ROOT = _find_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Warehouse: SQLite file by default; any SQLAlchemy URL (postgresql+psycopg://...) in production.
    database_url: str = f"sqlite:///{ROOT / 'data' / 'warehouse.db'}"
    # Vector store: "memory" (numpy), "pgvector" (needs Postgres) or "milvus" (Milvus Lite file, server or Zilliz).
    vector_backend: str = "memory"
    milvus_uri: str = f"{ROOT / 'data' / 'milvus.db'}"
    milvus_token: str = ""
    # Reranker over the fused candidates: "lexical" (deterministic), "flashrank" (local cross-encoder),
    # "llm" (model-scored; falls back to lexical without a key) or "none".
    reranker: str = "lexical"
    # Result cache for /chat: "memory" or a redis:// URL. TTL in seconds.
    cache_url: str = "memory"
    cache_ttl_s: int = 3600
    # Embeddings: "openai" or "hash" (deterministic, no network; keyword search carries retrieval).
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # LLM: "openai" (any OpenAI-compatible endpoint) or "fake" (deterministic semantic compiler, for tests/demo).
    llm_provider: str = "fake"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-5.6-sol"
    llm_temperature: float | None = None  # reasoning models accept only their default; leave unset
    llm_reasoning_effort: str = "low"  # sent when non-empty and the model accepts it
    llm_timeout_s: float = 60.0
    llm_fallback_to_fake: bool = True  # if the model path raises, answer with the deterministic path and say so

    semantic_dir: Path = ROOT / "semantic"
    as_of_date: str = ""  # YYYY-MM-DD; empty = max order_date in the warehouse
    max_rows: int = 500
    query_timeout_s: int = 20
    top_k_chunks: int = 6
    max_sql_repairs: int = 2
    max_tool_steps: int = 4  # ReAct tool calls the SQL agent may make before writing
    max_critic_loops: int = 1

    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_url: str = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:8080")
    agent_name: str = "bi-copilot"

    # Public-exposure controls. 0 disables a limit. The IP is hashed with `secret_salt`, never stored.
    trusted_proxy_hops: int = 0  # 1 behind Render / a single reverse proxy
    visitor_per_minute: int = 8
    visitor_daily_limit: int = 30
    global_daily_limit: int = 400  # model-backed answers per day, site-wide (cache hits are free)
    admin_token: str = ""  # protects /runs; empty = open (local dev only)
    secret_salt: str = "change-me"

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")
