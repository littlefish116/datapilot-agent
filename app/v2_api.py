"""Local-only, opt-in FastAPI endpoint for the V2 Text-to-SQL prototype.

This is a development/demo route: no authentication, quota enforcement,
production-grade privacy audit or general semantic correctness guarantee.
Never bind an enabled instance to a publicly reachable interface.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.v2_runtime import run_v2_query
from app.v2_text_to_sql import DeepSeekSQLBackend

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "olist.db"
router = APIRouter(prefix="/api/v2", tags=["V2 Experimental"])


class V2QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def normalized_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


def _guard_local_enabled(request: Request) -> None:
    if os.getenv("DATAPILOT_V2_ENABLED", "0").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="V2 试验接口未启用")
    # ASGI client address, NOT the user-controlled Host / X-Forwarded-For header.
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="V2 试验接口仅允许本机访问")


@router.post("/query")
def query_v2(payload: V2QueryRequest, request: Request):
    """Single-turn query. Emits SQL + execution / risk evidence, no fabricated prose."""
    _guard_local_enabled(request)
    if not DB_PATH.is_file():
        raise HTTPException(status_code=503, detail="业务数据库不可用")
    try:
        backend = DeepSeekSQLBackend()
    except Exception:
        raise HTTPException(status_code=503, detail="V2 模型服务尚未正确配置") from None
    return run_v2_query(payload.question, DB_PATH, backend=backend)
