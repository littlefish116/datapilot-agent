"""V3.4 local-only frontend route and installer regression tests.

No real model or DB is loaded and no DeepSeek requests occur.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.v3_web import router
from scripts.install_v3_web import install, NEW_IMPORT, NEW_INCLUDE


def make_client(host: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, client=(host, 55000))


@pytest.mark.parametrize("path", ["/v3", "/v3/app.js", "/v3/app.css"])
def test_disabled_by_default(path, monkeypatch):
    monkeypatch.delenv("DATAPILOT_V3_ENABLED", raising=False)
    assert make_client().get(path).status_code == 404


@pytest.mark.parametrize("path", ["/v3", "/v3/app.js", "/v3/app.css"])
def test_remote_forbidden(path, monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    assert make_client("192.0.2.54").get(path).status_code == 403


def test_local_html_and_security_headers(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    res = make_client().get("/v3")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "utf-8" in res.headers["content-type"]
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    assert "script-src 'self'" in res.headers["content-security-policy"]
    assert '<html lang="zh-CN">' in res.text
    assert 'href="/v3/app.css"' in res.text
    assert 'src="/v3/app.js"' in res.text


def test_local_css_js_mime_and_content(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    client = make_client()
    js = client.get("/v3/app.js")
    css = client.get("/v3/app.css")
    assert js.status_code == css.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "text/css" in css.headers["content-type"]
    assert "utf-8" in js.headers["content-type"]
    assert "fetch('/api/v3/chat'" in js.text
    assert '.answer-card' in css.text
    assert 'eval(' not in js.text
    assert '.innerHTML' not in js.text
    assert 'localStorage' not in js.text


def test_no_path_traversal_or_asset_browsing(monkeypatch):
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    c = make_client()
    assert c.get("/v3/anything").status_code == 404
    assert c.get("/v3/../main.py").status_code == 404


def test_missing_client_denied(monkeypatch):
    from starlette.requests import Request
    from app.v3_web import _guard_v3_web
    from fastapi import HTTPException
    monkeypatch.setenv("DATAPILOT_V3_ENABLED", "1")
    request = Request({"type": "http", "method": "GET", "path": "/v3", "headers": [], "client": None})
    with pytest.raises(HTTPException) as err:
        _guard_v3_web(request)
    assert err.value.status_code == 403


def test_install_idempotent_and_backup(tmp_path):
    main = tmp_path / "main.py"
    backup = tmp_path / "main.py.bak"
    original = b"from app.v3_api import router as v3_router\napp=object()\napp.include_router(v3_router)\n"
    main.write_bytes(original)
    assert "Installed" in install(main, backup)
    assert "Already" in install(main, backup)
    content = main.read_bytes()
    assert content.count(NEW_IMPORT) == 1
    assert content.count(NEW_INCLUDE) == 1
    assert backup.read_bytes() == original


def test_install_preserves_crlf(tmp_path):
    main = tmp_path / "main.py"
    orig = b"from app.v3_api import router as v3_router\r\napp=object()\r\napp.include_router(v3_router)\r\n"
    main.write_bytes(orig)
    install(main, tmp_path / "main.py.bak")
    assert b"\r\n" + NEW_IMPORT + b"\r\n" in main.read_bytes()
    assert b"\r\n" + NEW_INCLUDE + b"\r\n" in main.read_bytes()


def test_install_refuses_wrong_layout(tmp_path):
    main = tmp_path / "main.py"
    main.write_text("app = object()\n", encoding="utf-8")
    backup = tmp_path / "a.bak"
    with pytest.raises(RuntimeError):
        install(main, backup)
    assert not backup.exists()


def test_install_refuses_partial(tmp_path):
    main = tmp_path / "main.py"
    backup = tmp_path / "a.bak"
    main.write_bytes(b"from app.v3_api import router as v3_router\n" + NEW_IMPORT + b"\napp.include_router(v3_router)\n")
    with pytest.raises(RuntimeError, match="Partial"):
        install(main, backup)
    assert not backup.exists()


def test_install_refuses_backup_overwrite(tmp_path):
    main = tmp_path / "main.py"
    backup = tmp_path / "a.bak"
    original = b"from app.v3_api import router as v3_router\napp.include_router(v3_router)\n"
    main.write_bytes(original)
    backup.write_bytes(b"important")
    with pytest.raises(RuntimeError, match="Backup exists"):
        install(main, backup)
    assert main.read_bytes() == original
    assert backup.read_bytes() == b"important"


def test_ui_files_present():
    root = Path(__file__).resolve().parents[1] / "frontend"
    for filename in ("v3.html", "v3.js", "v3.css"):
        assert (root / filename).stat().st_size > 1500
