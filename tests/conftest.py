from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from bicopilot.api import create_app
from bicopilot.config import Settings
from bicopilot.service import Copilot


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path_factory.mktemp('db') / 'warehouse.db'}"
    return Settings(
        database_url=url,
        llm_provider="fake",
        embedding_provider="hash",
        vector_backend="pgvector" if url.startswith("postgresql") else "memory",
        cache_url=os.environ.get("TEST_CACHE_URL", "memory"),
        as_of_date="2026-09-28",
    )


@pytest.fixture(scope="session")
def copilot(settings) -> Copilot:
    return Copilot(settings)


@pytest.fixture(scope="session")
def client(settings, copilot) -> TestClient:
    app = create_app(settings, copilot=copilot)
    with TestClient(app) as c:
        yield c
