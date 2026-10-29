"""Result cache for answered questions.

Key = normalised question + pending question + as-of date + semantic-layer version, so a changed
definition invalidates every cached answer. Backends: Redis (`CACHE_URL=redis://...`) for a shared
cache across API replicas, or an in-process LRU for the zero-infra path. Only "answered" results are
cached; clarifications, refusals and errors are always recomputed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


class MemoryCache:
    name = "memory"

    def __init__(self, ttl_s: int, max_items: int = 512):
        self.ttl, self.max_items = ttl_s, max_items
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if not item:
            return None
        expires, payload = item
        if expires < time.time():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return json.loads(payload)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = (time.time() + self.ttl, json.dumps(value, default=str))
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


class RedisCache:
    name = "redis"

    def __init__(self, url: str, ttl_s: int, prefix: str = "bicopilot:answer:"):
        import redis

        self.client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self.ttl, self.prefix = ttl_s, prefix
        self.client.ping()

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.client.get(self.prefix + key)
        except Exception:  # Redis down: behave like a miss, never fail the answer
            return None
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        try:
            self.client.setex(self.prefix + key, self.ttl, json.dumps(value, default=str))
        except Exception:
            pass

    def clear(self) -> None:
        for k in self.client.scan_iter(self.prefix + "*"):
            self.client.delete(k)


def cache_key(question: str, pending_question: str, as_of: str, semantic_version: str) -> str:
    raw = "|".join(
        [" ".join(question.lower().split()), " ".join(pending_question.lower().split()), as_of, semantic_version]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def make_cache(url: str, ttl_s: int) -> MemoryCache | RedisCache:
    if url.startswith("redis://") or url.startswith("rediss://"):
        try:
            return RedisCache(url, ttl_s)
        except Exception:  # unreachable Redis at startup: fall back rather than refuse to boot
            return MemoryCache(ttl_s)
    return MemoryCache(ttl_s)
