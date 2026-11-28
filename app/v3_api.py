"""V3.2 local-only API with bounded, ephemeral per-session context.

V1 and V2 endpoints remain unchanged. This experimental endpoint has neither
production authentication nor persistent chat history; never expose publicly.
"""
from __future__ import annotations

import os
import threading
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.v2_text_to_sql import DeepSeekSQLBackend
from app.v3_conversation import ConversationMissing, V3SessionStore, run_v3_conversation_turn
from app.v3_tool_service import V3ToolService

router = APIRouter(prefix="/api/v3", tags=["V3 Experimental"])
_STORE_INIT_LOCK = threading.Lock()


class V3ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    reset_context: bool = False

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


def _get_sessions(request: Request) -> V3SessionStore:
    sessions = getattr(request.app.state, "v3_sessions", None)
    if sessions is not None:
        return sessions
    with _STORE_INIT_LOCK:
        sessions = getattr(request.app.state, "v3_sessions", None)
        if sessions is None:
            sessions = V3SessionStore()
            request.app.state.v3_sessions = sessions
        return sessions


@router.post("/chat")
def v3_chat(payload: V3ChatRequest, request: Request):
    _guard_local_enabled(request)
    model = getattr(request.app.state, "model", None)
    metadata = getattr(request.app.state, "metadata", None)
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="延期预测模型未加载")
    try:
        backend = DeepSeekSQLBackend()
    except Exception:
        raise HTTPException(status_code=503, detail="V3 路由模型尚未正确配置") from None
    service = V3ToolService(model=model, metadata=metadata, sql_backend=backend)
    try:
        response = run_v3_conversation_turn(
            payload.question, store=_get_sessions(request),
            conversation_id=str(payload.conversation_id) if payload.conversation_id else None,
            backend=backend, service=service, reset_context=payload.reset_context,
        )
    except ConversationMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=503, detail="V3 多轮工具调度暂时不可用") from None
    # Explicit charset also fixes mojibake in Windows PowerShell 5.1.
    return JSONResponse(content=response, media_type="application/json; charset=utf-8")
