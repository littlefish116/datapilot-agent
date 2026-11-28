"""V3.3 service, API and LangGraph wiring; deterministic and offline."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.v3_api as api
import app.v3_sql_recovery as recovery
from app.v3_tool_service import V3ToolService


def fake_ok(sql):
    return {"status": "ok", "generated_sql": sql,
            "execution": {"status": "ok", "rows": [{"n": 2}], "columns": ["n"],
                          "returned_rows": 1, "truncated": False, "sql": sql},
            "risk_review": {"status": "ok", "risk_count": 0, "disposition": "no_known_risk"},
            "automatic_repair": False}


def test_v3_service_repair_disabled_by_default(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"not actually read by stub")
    seen = []

    def runner(question, path, *, backend):
        seen.append(question)
        return fake_ok("SELECT COUNT(*) AS n FROM orders")

    def fail(*args, **kwargs):
        raise AssertionError("recovery must not run")

    monkeypatch.setattr(recovery, "run_v3_sql_recovery", fail)
    out = V3ToolService(db_path=db, sql_backend=object(), sql_runner=runner).sql_analysis("订单数")
    assert out["status"] == "ok" and out["evidence"]["automatic_repair"] is False
    assert seen == ["订单数"]


def test_v3_service_success_evidence_from_recovery(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"x")
    seen = []

    def stub_recovery(question, path, *, backend, runner):
        seen.append((question, path, backend, runner))
        data = fake_ok("SELECT COUNT(*) AS n FROM orders")
        data["automatic_repair"] = True
        data["recovery"] = {"outcome": "technical_success", "attempted": True}
        return data

    monkeypatch.setattr(recovery, "run_v3_sql_recovery", stub_recovery)
    obj = object()
    service = V3ToolService(db_path=db, sql_backend=obj, allow_sql_repair=True)
    result = service.sql_analysis("多少订单")
    assert len(seen) == 1 and seen[0][2] is obj
    assert result["status"] == "ok"
    assert result["evidence"]["automatic_repair"] is True
    assert result["evidence"]["recovery"]["outcome"] == "technical_success"


def test_v3_service_risk_result_not_success_even_if_claimed(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"x")
    def stub_recovery(*args, **kwargs):
        return {"status": "needs_review", "generated_sql": "SELECT...", "execution": None,
                "risk_review": {"status": "ok", "risk_count": 1},
                "automatic_repair": False, "recovery": {"outcome": "semantic_review"}}
    monkeypatch.setattr(recovery, "run_v3_sql_recovery", stub_recovery)
    out = V3ToolService(db_path=db, sql_backend=object(), allow_sql_repair=True).sql_analysis("问题")
    assert out["status"] == "needs_review"
    assert out["data"] is None and not out["evidence"]["automatic_repair"]


class FakeBackend:
    def invoke(self, messages):
        return {"content": json.dumps({"status": "route", "tool": "business_report",
                                       "arguments": {"report_id": "order_status"}})}


class FakeService:
    def __init__(self, **kwargs):
        CAPTURE.append(kwargs)


CAPTURE = []


def client(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", FakeBackend)
    monkeypatch.setattr(api, "V3ToolService", FakeService)
    monkeypatch.setattr(api, "run_v3_conversation_turn", lambda *a, **kw: {"status": "ok"})
    app = FastAPI()
    app.include_router(api.router)
    app.state.model = object()
    app.state.metadata = {"model_name": "test", "features": []}
    return TestClient(app, client=("127.0.0.1", 32000))


def test_v3_api_repair_disabled_when_env_absent(monkeypatch):
    CAPTURE.clear()
    monkeypatch.delenv("DATAPILOT_V3_SQL_REPAIR_ENABLED", raising=False)
    assert client(monkeypatch).post("/api/v3/chat", json={"question": "你好"}).status_code == 200
    assert CAPTURE[-1]["allow_sql_repair"] is False


def test_v3_api_repair_enabled_explicitly(monkeypatch):
    CAPTURE.clear()
    monkeypatch.setenv("DATAPILOT_V3_SQL_REPAIR_ENABLED", "1")
    assert client(monkeypatch).post("/api/v3/chat", json={"question": "你好"}).status_code == 200
    assert CAPTURE[-1]["allow_sql_repair"] is True


def test_v3_api_repair_flag_does_not_enable_v3_api(monkeypatch):
    CAPTURE.clear()
    monkeypatch.delenv("DATAPILOT_V3_ENABLED", raising=False)
    monkeypatch.setenv("DATAPILOT_V3_SQL_REPAIR_ENABLED", "1")
    c = client(monkeypatch)
    monkeypatch.delenv("DATAPILOT_V3_ENABLED", raising=False)
    assert c.post("/api/v3/chat", json={"question": "你好"}).status_code == 404
    assert CAPTURE == []


def test_v3_api_remote_forbidden_with_repair_flag(monkeypatch):
    CAPTURE.clear()
    monkeypatch.setenv("DATAPILOT_V3_SQL_REPAIR_ENABLED", "1")
    c = client(monkeypatch)
    remote = TestClient(c.app, client=("192.0.2.55", 32120))
    assert remote.post("/api/v3/chat", json={"question": "你好"}).status_code == 403
    assert CAPTURE == []


@pytest.mark.skipif(__import__("importlib").util.find_spec("langgraph") is None,
                    reason="LangGraph absent in isolated authoring env")
def test_langgraph_propagates_success_only():
    from app.v3_routing import run_v3_turn

    class B:
        def invoke(self, messages):
            return {"content": json.dumps({"status": "route", "tool": "sql_analysis", "arguments": {}})}

    class S:
        def __init__(self, status, automatic):
            self.status = status
            self.automatic = automatic
        def sql_analysis(self, question):
            return {"tool": "sql_analysis", "status": self.status,
                    "data": {"rows": [{"n": 2}], "truncated": False} if self.status == "ok" else None,
                    "evidence": {"automatic_repair": self.automatic}, "error": None,
                    "message": "需人工复核"}

    assert run_v3_turn("订单数", backend=B(), service=S("ok", True))["automatic_repair"] is True
    assert run_v3_turn("订单数", backend=B(), service=S("needs_review", False))["automatic_repair"] is False
