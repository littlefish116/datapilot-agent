"""V3.2 API tests: real FastAPI client with simulated LangGraph executor."""
import json
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.v3_api as api
from app.v3_conversation import run_v3_conversation_turn


class FakeBackend:
    def invoke(self, messages):
        return {"content": json.dumps({"status": "standalone"})}


class FakeService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def fake_graph(question, *, backend, service):
    return {"status": "ok", "question": question, "selected_tool": "sql_analysis",
            "used_tools": ["sql_analysis"], "answer": f"统计结果：{question}",
            "tool_result": {"tool": "sql_analysis", "status": "ok",
                            "data": {"rows": [{"n": 1}], "truncated": False},
                            "evidence": {"source": "v2_safe_sql_pipeline"}},
            "route_usage": {"total_tokens": 11}, "automatic_repair": False}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", FakeBackend)
    monkeypatch.setattr(api, "V3ToolService", FakeService)
    monkeypatch.setattr(api, "run_v3_conversation_turn", lambda *args, **kw:
                        run_v3_conversation_turn(*args, **kw, runner=fake_graph))
    app = FastAPI()
    app.include_router(api.router)
    app.state.model = object()
    app.state.metadata = {"features": [], "model_name": "fake"}
    return TestClient(app, client=("127.0.0.1", 50001))


def test_first_api_turn_returns_session_and_utf8_charset(client):
    response = client.post("/api/v3/chat", json={"question": "查询2018年3月的延期率"})
    assert response.status_code == 200
    assert "charset=utf-8" in response.headers["content-type"]
    data = response.json()
    assert UUID(data["conversation_id"])
    assert data["answer"] == "统计结果：查询2018年3月的延期率"
    assert data["selected_tool"] == "sql_analysis"
    assert data["session_turn"] == 1


def test_second_api_turn_inherits_same_session(client):
    a = client.post("/api/v3/chat", json={"question": "查询2018年3月的延期率"}).json()
    b = client.post("/api/v3/chat", json={
        "question": "那4月呢？", "conversation_id": a["conversation_id"],
    }).json()
    assert b["conversation_id"] == a["conversation_id"]
    assert b["question"] == "那4月呢？"
    assert b["effective_question"] == "查询2018年4月的延期率"
    assert b["context_used"] is True
    assert b["session_turn"] == 2


def test_empty_context_questions_do_not_use_llm(client):
    data = client.post("/api/v3/chat", json={"question": "那4月呢？"}).json()
    assert data["status"] == "clarify"
    assert data["used_tools"] == []
    assert data["tool_result"] is None


def test_unknown_session_http_409(client):
    response = client.post("/api/v3/chat", json={
        "question": "那4月呢？", "conversation_id": "55555555-5555-4555-8555-555555555555",
    })
    assert response.status_code == 409
    assert "过期" in response.json()["detail"]


def test_invalid_session_uuid_http_422(client):
    response = client.post("/api/v3/chat", json={
        "question": "查询", "conversation_id": "hello",
    })
    assert response.status_code == 422


def test_reset_context_api_changes_uuid(client):
    a = client.post("/api/v3/chat", json={"question": "查询2018年3月的延期率"}).json()
    b = client.post("/api/v3/chat", json={
        "question": "查询2017年3月的订单数", "conversation_id": a["conversation_id"],
        "reset_context": True,
    }).json()
    assert a["conversation_id"] != b["conversation_id"]
    assert b["session_turn"] == 1


def test_two_clients_per_app_state_are_isolated(client):
    a = client.post("/api/v3/chat", json={"question": "查询2017年3月的订单数"}).json()
    b = client.post("/api/v3/chat", json={"question": "查询2018年3月的延期率"}).json()
    ra = client.post("/api/v3/chat", json={"question": "那4月呢？", "conversation_id": a["conversation_id"]}).json()
    rb = client.post("/api/v3/chat", json={"question": "那4月呢？", "conversation_id": b["conversation_id"]}).json()
    assert ra["effective_question"] == "查询2017年4月的订单数"
    assert rb["effective_question"] == "查询2018年4月的延期率"


def test_get_route_is_not_allowed(client):
    assert client.get("/api/v3/chat").status_code == 405


def test_invalid_payload_extra_fields_422(client):
    assert client.post("/api/v3/chat", json={"question":"查询", "sql":"DROP TABLE"}).status_code == 422


def test_default_off_still_404(client, monkeypatch):
    monkeypatch.delenv("DATAPILOT_V3_ENABLED", raising=False)
    assert client.post("/api/v3/chat", json={"question":"查询"}).status_code == 404


def test_remote_still_forbidden(client):
    remote = TestClient(client.app, client=("192.0.2.25", 50001))
    assert remote.post("/api/v3/chat", json={"question":"查询"}).status_code == 403
