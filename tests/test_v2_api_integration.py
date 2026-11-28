"""V2 API route and runtime tests; no DeepSeek calls or real Olist DB needed."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.v2_api as api
from app.v2_runtime import run_v2_query


class FakeBackend:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return {"content": self.content, "usage": {"total_tokens": 11}}


def decision_sql(sql="SELECT COUNT(*) AS n FROM orders"):
    return json.dumps({"status": "sql", "sql": sql})


def safe_inspector(sql):
    return {"status": "ok", "risk_count": 0, "risks": [], "disposition": "no_known_risk", "semantics_proven": False}


def fake_executor(sql, path):
    return {"status": "ok", "sql": sql, "columns": ["n"], "rows": [{"n": 4}], "returned_rows": 1, "truncated": False, "error": None}


def test_sql_executes_only_once():
    backend = FakeBackend(decision_sql())
    seen = []
    def exec_once(sql, path):
        seen.append(sql)
        return fake_executor(sql, path)
    result = run_v2_query("统计订单", "unused.db", backend=backend, inspector=safe_inspector, executor=exec_once)
    assert result["status"] == "ok"
    assert result["execution"]["rows"] == [{"n": 4}]
    assert result["automatic_repair"] is False
    assert len(seen) == backend.calls == 1


def test_risk_blocks_execution():
    backend = FakeBackend(decision_sql())
    def should_not_execute(*args):
        raise AssertionError("Must not execute risky SQL")
    def review(sql):
        return {"status": "ok", "risk_count": 1, "risks": [{"code": "DATE_VALIDITY_GAP"}], "disposition": "needs_review"}
    result = run_v2_query("问题", "unused.db", backend=backend, inspector=review, executor=should_not_execute)
    assert result["status"] == "needs_review"
    assert result["execution"] is None
    assert backend.calls == 1


def test_inspector_failure_stops_execution():
    backend = FakeBackend(decision_sql())
    def bad_guard(sql):
        raise RuntimeError("parse failure")
    result = run_v2_query("问题", "unused.db", backend=backend, inspector=bad_guard, executor=lambda *args: 1 / 0)
    assert result["status"] == "error"
    assert result["error"]["code"] == "semantic_check_error"


def test_clarify_never_runs_sql():
    backend = FakeBackend(json.dumps({"status": "clarify", "message": "请问时间范围？"}, ensure_ascii=False))
    result = run_v2_query("查最近数据", "unused.db", backend=backend, inspector=lambda *a: 1 / 0, executor=lambda *a: 1 / 0)
    assert result["status"] == "clarify"
    assert result["message"] == "请问时间范围？"


def test_unsupported_never_runs_sql():
    backend = FakeBackend(json.dumps({"status": "unsupported", "message": "未开放评论字段"}, ensure_ascii=False))
    result = run_v2_query("查评论", "unused.db", backend=backend, inspector=lambda *a: 1 / 0)
    assert result["status"] == "unsupported"


def test_truncated_explicitly_partial():
    backend = FakeBackend(decision_sql())
    def limited(sql, path):
        data = fake_executor(sql, path)
        data["truncated"] = True
        return data
    result = run_v2_query("问题", "unused.db", backend=backend, inspector=safe_inspector, executor=limited)
    assert result["status"] == "partial"
    assert result["execution"]["truncated"] is True


def test_executor_error_preserved():
    backend = FakeBackend(decision_sql())
    def denied(sql, path):
        return {"status": "error", "error": {"code": "query_timeout", "message": "资源超限"}}
    result = run_v2_query("问题", "unused.db", backend=backend, inspector=safe_inspector, executor=denied)
    assert result["status"] == "error"
    assert result["error"]["code"] == "query_timeout"


def make_client(client_host="127.0.0.1"):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app, client=(client_host, 50001))


def test_route_off_by_default(monkeypatch):
    monkeypatch.delenv("DATAPILOT_V2_ENABLED", raising=False)
    c = make_client()
    assert c.post("/api/v2/query", json={"question": "统计订单"}).status_code == 404


def test_route_remote_forbidden(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V2_ENABLED", "1")
    c = make_client("192.0.2.99")
    assert c.post("/api/v2/query", json={"question": "统计订单"}).status_code == 403


def test_route_rejects_blank_question(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V2_ENABLED", "1")
    c = make_client()
    assert c.post("/api/v2/query", json={"question": "   "}).status_code == 422


def test_route_uses_backend_local(monkeypatch, tmp_path):
    backend = FakeBackend(json.dumps({"status": "clarify", "message": "请明确时间范围"}, ensure_ascii=False))
    db = tmp_path / "olist.db"
    db.write_bytes(b"dummy")
    monkeypatch.setenv("DATAPILOT_V2_ENABLED", "1")
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "DeepSeekSQLBackend", lambda: backend)
    c = make_client()
    r = c.post("/api/v2/query", json={"question": "最近是多少？"})
    assert r.status_code == 200
    assert r.json()["status"] == "clarify"
    assert backend.calls == 1


def test_route_no_database_no_model_call(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAPILOT_V2_ENABLED", "1")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", lambda: 1 / 0)
    c = make_client()
    assert c.post("/api/v2/query", json={"question": "订单是多少"}).status_code == 503
