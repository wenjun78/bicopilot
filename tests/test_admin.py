"""Owner analytics, authentication, persistence, and anonymous visit tracking."""
from __future__ import annotations

import uuid


def _auth():
    return {"Authorization": "Bearer test-admin-token"}


def test_private_dashboard_and_summary_auth(client):
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/styles.css").status_code == 200
    assert client.get("/admin/app.js").status_code == 200
    assert client.get("/admin/api/overview").status_code == 401
    assert client.get("/runs").status_code == 401
    r = client.get("/admin/api/overview?days=30", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["timezone"] == "UTC" and len(data["daily"]) == 30
    assert data["visits"] >= 0 and data["questions"] >= 0
    assert client.get("/admin/api/overview?days=365", headers=_auth()).status_code == 422


def test_anonymous_visits_are_aggregated(client):
    visitor_id = uuid.uuid4().hex
    url = "/analytics/visit"
    body = {"visitor_id": visitor_id, "session_id": "session-123",
            "path": "/portfolio-demo", "referrer": "example.com"}
    for _ in range(2):
        assert client.post(url, json=body).status_code == 204
    overview = client.get("/admin/api/overview?days=7", headers=_auth()).json()
    assert overview["visits"] >= 2
    assert any(v["referrer"] == "example.com" for v in overview["recent_visits"])
    assert overview["unique_visitors"] >= 1
    assert not any(visitor_id in str(v) for v in overview["recent_visits"])
    assert client.post(url, json={**body, "visitor_id": "!"}).status_code == 422
    assert client.post(url, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
