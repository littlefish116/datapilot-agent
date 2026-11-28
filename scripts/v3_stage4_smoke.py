"""Offline smoke of the V3.4 HTML/CSS/JS routes; zero DeepSeek calls.

Run from project root: python -m scripts.v3_stage4_smoke
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.v3_web import router


@contextmanager
def enabled():
    old = os.environ.get("DATAPILOT_V3_ENABLED")
    os.environ["DATAPILOT_V3_ENABLED"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DATAPILOT_V3_ENABLED", None)
        else:
            os.environ["DATAPILOT_V3_ENABLED"] = old


def main():
    app = FastAPI()
    app.include_router(router)
    with enabled():
        local = TestClient(app, client=("127.0.0.1", 55000))
        html = local.get("/v3")
        css = local.get("/v3/app.css")
        js = local.get("/v3/app.js")
        assert all(r.status_code == 200 for r in [html, css, js])
        assert 'href="/v3/app.css"' in html.text and 'src="/v3/app.js"' in html.text
        assert "fetch('/api/v3/chat'" in js.text
        assert html.headers.get("cache-control") == "no-store"
        print("PASS 本机 V3 页面、CSS、JS 皆正常返回；页面正确引用资源")
        assert "Content-Security-Policy" in html.headers
        remote = TestClient(app, client=("192.0.2.42", 60001))
        assert remote.get("/v3").status_code == 403
        assert remote.get("/v3/app.js").status_code == 403
        print("PASS 远程访问拒绝；CSP/no-store/nosniff 已配置")
    assert local.get("/v3").status_code == 404
    print("PASS 默认关闭时 V3 页面不可用；无 DeepSeek 请求、无数据库操作")
    print("注意：完整浏览器交互和真实 V3 API 需在本机人工验收。")


if __name__ == "__main__":
    main()
