import uuid


def test_healthz_and_catalog(client):
    h = client.get("/healthz").json()
    assert h["ok"] and h["llm"] == "fake"
    cat = client.get("/catalog").json()
    assert any(m["id"] == "revenue" for m in cat["metrics"]) and cat["rules"]


def test_chat(client):
    r = client.post("/chat", json={"question": "top 5 categories by revenue 2017", "session_id": "t1"}).json()
    assert r["status"] == "answered" and r["row_count"] == 5 and r["sql"]
    auth = {"Authorization": "Bearer test-admin-token"}
    assert client.get("/runs").status_code == 401
    runs = client.get("/runs", headers=auth).json()
    assert runs[0]["session_id"] == "t1"
    assert client.get(f"/runs/{runs[0]['id']}", headers=auth).json()["plan"]["top_n"] == 5


def test_chat_validation(client):
    assert client.post("/chat", json={"question": ""}).status_code == 422


def test_agent_card_and_a2a_message_send(client):
    card = client.get("/.well-known/agent.json").json()
    assert card["skills"][0]["id"] == "ask-data" and card["url"].endswith("/a2a")
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": "revenue by region 2017"}],
            }
        },
    }
    body = client.post("/a2a", json=payload).json()
    task = body["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    data = next(p["data"] for p in task["artifacts"][0]["parts"] if p["kind"] == "data")
    assert data["columns"] == ["region", "revenue"] and len(data["rows"]) == 5


def test_a2a_clarification_is_input_required(client):
    payload = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "m",
                "parts": [{"kind": "text", "text": "delivery performance by region"}],
            }
        },
    }
    assert client.post("/a2a", json=payload).json()["result"]["status"]["state"] == "input-required"


def test_a2a_rejects_unknown_method(client):
    body = client.post("/a2a", json={"jsonrpc": "2.0", "id": "3", "method": "tasks/get", "params": {}}).json()
    assert body["error"]["code"] == -32601
