"""Opt-in local-only V3.1B API: one routing decision and max one tool.

No session memory, authentication, rate limiting or production-facing access.
Never run enabled V3 on a publicly reachable interface.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.v2_text_to_sql import DeepSeekSQLBackend
from app.v3_routing import run_v3_turn
from app.v3_tool_service import V3ToolService

router = APIRouter(prefix="/api/v3", tags=["V3 Experimental"])


class V3ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


def _guard_local_enabled(request: Request) -> None:
    if os.getenv("DATAPILOT_V3_ENABLED", "0").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="V3 实验接口未启用")
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="V3 实验接口仅允许本机访问")


@router.post("/chat")
def v3_chat(payload: V3ChatRequest, request: Request):
    _guard_local_enabled(request)
    model = getattr(request.app.state, "model", None)
    metadata = getattr(request.app.state, "metadata", None)
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="延期预测模型未加载")
    try:
        # Reuses the trusted server-side OpenAI-compatible DeepSeek adapter.
        # SQL queries need another model request inside V2 Text-to-SQL.
        backend = DeepSeekSQLBackend()
    except Exception:
        raise HTTPException(status_code=503, detail="V3 路由模型尚未正确配置") from None
    service = V3ToolService(model=model, metadata=metadata, sql_backend=backend)
    try:
        return run_v3_turn(payload.question, backend=backend, service=service)
    except Exception:
        raise HTTPException(status_code=503, detail="V3 工具调度暂时不可用") from None
