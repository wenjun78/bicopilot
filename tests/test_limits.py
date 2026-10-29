from fastapi.testclient import TestClient

from bicopilot.api import create_app
from bicopilot.limits import VisitorLimiter


def test_visitor_limiter_minute_and_day():
    lim = VisitorLimiter(per_minute=2, per_day=3, salt="s")
    assert lim.check("1.1.1.1").allowed and lim.check("1.1.1.1").allowed
    third = lim.check("1.1.1.1")
    assert not third.allowed and third.code == "visitor_minute" and third.retry_after >= 1
    assert lim.check("2.2.2.2").allowed  # other visitors unaffected


def test_limiter_disabled_when_zero():
    lim = VisitorLimiter(0, 0, "s")
    assert all(lim.check("x").allowed for _ in range(50))


def _client(copilot, **overrides) -> TestClient:
    s = copilot.settings.model_copy(update=overrides)
    return TestClient(create_app(s, copilot=copilot))


def test_chat_returns_429_after_per_minute_limit(copilot):
    with _client(copilot, visitor_per_minute=2, visitor_daily_limit=10, global_daily_limit=0) as c:
        assert c.post("/chat", json={"question": "revenue by region FY2026"}).status_code == 200
        r = c.post("/chat", json={"question": "orders by channel FY2026"})
        assert r.status_code == 200 and r.json()["questions_left"] == 8
        blocked = c.post("/chat", json={"question": "units sold last quarter"})
        assert (
            blocked.status_code == 429
            and blocked.json()["code"] == "visitor_minute"
            and "Retry-After" in blocked.headers
        )


def test_global_daily_cap_counts_model_answers(copilot):
    copilot.cache.clear()
    before = copilot.model_answers_today()
    with _client(copilot, visitor_per_minute=0, visitor_daily_limit=0, global_daily_limit=before + 1) as c:
        assert c.post("/chat", json={"question": "active customers by region"}).status_code == 200
        r = c.post("/chat", json={"question": "revenue by tier FY2026"})
        assert r.status_code == 429 and r.json()["code"] == "site_limit"


def test_runs_require_admin_token_when_configured(copilot):
    with _client(
        copilot, admin_token="secret-token", visitor_per_minute=0, visitor_daily_limit=0, global_daily_limit=0
    ) as c:
        assert c.get("/runs").status_code == 401
        assert c.get("/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/runs", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_client_ip_behind_proxy():
    from starlette.requests import Request

    from bicopilot.limits import client_ip

    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")], "client": ("10.0.0.1", 1)}
    assert client_ip(Request(scope), trusted_hops=1) == "10.0.0.1"
    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.9")], "client": ("10.0.0.1", 1)}
    assert client_ip(Request(scope), trusted_hops=1) == "203.0.113.9"
    assert client_ip(Request(scope), trusted_hops=0) == "10.0.0.1"
