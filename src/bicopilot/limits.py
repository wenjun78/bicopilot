"""Abuse controls for a public deployment with a paid model key.

- per-visitor limits: N questions per minute and per day, keyed by a salted hash of the client IP
  (the IP itself is never stored)
- a site-wide daily cap, counted from the runs table so it survives restarts
- client IP taken from X-Forwarded-For only for the configured number of trusted proxy hops
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request


@dataclass
class Verdict:
    allowed: bool
    retry_after: int = 0
    code: str = ""
    detail: str = ""
    left_today: int | None = None


def client_ip(request: Request, trusted_hops: int) -> str:
    """The real client address behind `trusted_hops` reverse proxies (Render = 1)."""
    if trusted_hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(chain) >= trusted_hops:
            return chain[-trusted_hops]
    return request.client.host if request.client else "unknown"


class VisitorLimiter:
    def __init__(self, per_minute: int, per_day: int, salt: str):
        self.per_minute, self.per_day, self.salt = per_minute, per_day, salt
        self._lock = threading.Lock()
        self._minute: dict[str, deque[float]] = {}
        self._day: dict[str, deque[float]] = {}

    def key(self, ip: str) -> str:
        return hashlib.sha256(f"{self.salt}|{ip}".encode()).hexdigest()[:16]

    def check(self, ip: str) -> Verdict:
        if self.per_minute <= 0 and self.per_day <= 0:
            return Verdict(True)
        now = time.time()
        k = self.key(ip)
        with self._lock:
            minute = self._minute.setdefault(k, deque())
            day = self._day.setdefault(k, deque())
            while minute and minute[0] < now - 60:
                minute.popleft()
            while day and day[0] < now - 86400:
                day.popleft()
            if self.per_day > 0 and len(day) >= self.per_day:
                return Verdict(
                    False,
                    int(day[0] + 86400 - now) + 1,
                    "visitor_day",
                    f"You've used today's {self.per_day} questions. Come back tomorrow.",
                    0,
                )
            if self.per_minute > 0 and len(minute) >= self.per_minute:
                return Verdict(
                    False,
                    int(minute[0] + 60 - now) + 1,
                    "visitor_minute",
                    "That's a lot of questions in a minute. Try again shortly.",
                    max(self.per_day - len(day), 0) if self.per_day else None,
                )
            minute.append(now)
            day.append(now)
            self._prune(now)
            return Verdict(True, 0, "", "", max(self.per_day - len(day), 0) if self.per_day else None)

    def _prune(self, now: float) -> None:
        if len(self._day) > 5000:
            for k in [k for k, d in self._day.items() if not d or d[-1] < now - 86400]:
                self._day.pop(k, None)
                self._minute.pop(k, None)


def bearer_ok(request: Request, token: str) -> bool:
    """Admin endpoints: open when no token is configured (local dev), otherwise Bearer-protected."""
    if not token:
        return True
    header = request.headers.get("authorization", "")
    return header.startswith("Bearer ") and header[7:].strip() == token
