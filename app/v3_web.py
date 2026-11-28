"""V3.4 local-only, default-off HTML/CSS/JS frontend.

No business data or credentials are embedded in static assets. This router is
intentionally separate from the existing V1/V2/V3 API routes. It shares the
same opt-in and loopback requirements as /api/v3/chat.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
router = APIRouter(tags=["V3 Experimental UI"])

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def _guard_v3_web(request: Request) -> None:
    if os.getenv("DATAPILOT_V3_ENABLED", "0").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="V3 实验界面未启用")
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="V3 实验界面仅允许本机访问")


def _asset(filename: str, media_type: str) -> FileResponse:
    # Constant file names only. No user-controlled file-system paths.
    return FileResponse(
        _FRONTEND / filename, media_type=media_type, headers=_SECURITY_HEADERS
    )


@router.get("/v3", include_in_schema=False)
def v3_home(request: Request):
    _guard_v3_web(request)
    return _asset("v3.html", "text/html; charset=utf-8")


@router.get("/v3/app.css", include_in_schema=False)
def v3_css(request: Request):
    _guard_v3_web(request)
    return _asset("v3.css", "text/css; charset=utf-8")


@router.get("/v3/app.js", include_in_schema=False)
def v3_js(request: Request):
    _guard_v3_web(request)
    return _asset("v3.js", "application/javascript; charset=utf-8")
