"""V3.1B API tests: never connect to DeepSeek or load saved model files."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.v3_api as api
from scripts.install_v3_api import install


class FakeBackend:
    def invoke(self, messages):
        return {"content": json.dumps({"status": "route", "tool": "business_report",
                                        "arguments": {"report_id": "order_status"}}),
                "usage": {"total_tokens": 6}}


class FakeService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def business_report(self, report_id):
        return {"tool": "business_report", "status": "ok", "data": {
            "rows": [{"n": 96478}], "truncated": False}, "message": None, "error": None, "evidence": {"report_id": report_id}}


def client(host="127.0.0.1"):
    app = FastAPI()
    app.include_router(api.router)
    app.state.model = object()
    app.state.metadata = {"features": [], "model_name": "fake"}
    return TestClient(app, client=(host, 80001))


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DATAPILOT_V3_ENABLED", raising=False)
    monkeypatch.setattr(api, "DeepSeekSQLBackend", lambda: 1 / 0)
    assert client().post("/api/v3/chat", json={"question": "查询"}).status_code == 404


def test_remote_forbidden_before_model(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", lambda: 1 / 0)
    assert client("192.0.2.42").post("/api/v3/chat", json={"question": "查询"}).status_code == 403


def test_blank_question_rejected(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    assert client().post("/api/v3/chat", json={"question": "  "}).status_code == 422


def test_extra_fields_rejected(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    assert client().post("/api/v3/chat", json={"question": "查询", "conversation_id": "hello"}).status_code == 422


def test_local_report_route(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", FakeBackend)
    monkeypatch.setattr(api, "V3ToolService", FakeService)
    response = client().post("/api/v3/chat", json={"question": "查询订单状态"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["selected_tool"] == "business_report"
    assert data["tool_result"]["data"]["rows"][0]["n"] == 96478


def test_bad_model_configuration(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    monkeypatch.setattr(api, "DeepSeekSQLBackend", lambda: 1 / 0)
    assert client().post("/api/v3/chat", json={"question": "查询"}).status_code == 503


def test_missing_loaded_model(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    c = client()
    del c.app.state.model
    assert c.post("/api/v3/chat", json={"question": "查询"}).status_code == 503


def test_installer_idempotent_and_original_preserved(tmp_path):
    main = tmp_path / "main.py"
    backup = tmp_path / "main.py.bak"
    orig = b"from app.v2_api import router as v2_router\napp = object()\napp.include_router(v2_router)\n"
    main.write_bytes(orig)
    assert "Installed" in install(main, backup)
    text = main.read_text(encoding="utf8")
    assert text.count("app.include_router(v3_router)") == 1
    assert "Already" in install(main, backup)
    assert backup.read_bytes() == orig


def test_installer_does_not_guess_unexpected_main(tmp_path):
    main = tmp_path / "main.py"
    main.write_text("app = object()\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        install(main, tmp_path / "main.bak")
    assert not (tmp_path / "main.bak").exists()


def test_installer_preserves_crlf(tmp_path):
    main = tmp_path / "main.py"
    backup = tmp_path / "main.py.bak"
    orig = b"from app.v2_api import router as v2_router\r\napp = object()\r\napp.include_router(v2_router)\r\n"
    main.write_bytes(orig)
    install(main, backup)
    assert b"\r\napp.include_router(v3_router)" in main.read_bytes()
    assert b"\r\n" in main.read_bytes()
