"""Isolated Text-to-SQL prototype for DataPilot Stage 2B.

Does not change FastAPI, LangGraph, V1 Agent or model weights. The LLM only
proposes a SELECT; the existing Stage 2A executor owns database permissions.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from app.v2_schema_context import build_schema_context

SYSTEM_RULES = """你是 DataPilot 电商分析助手的 SQL 生成子模块。
目标是把问题转成 SQLite SELECT，且必须遵守随后提供的业务语义字典。
严格只返回一个 JSON 对象，禁止 Markdown 或任何 JSON 之外的内容。
JSON 必须符合以下三个之一：
{"status":"sql","sql":"SELECT ..."}
{"status":"clarify","message":"需要用户补充的关键口径"}
{"status":"unsupported","message":"当前只读分析库无法回答的原因"}
仅在具有足够口径且可查询时使用 sql；不得输出 DDL/DML、PRAGMA、ATTACH 或数据库修改语句。
日期和指标口径按语义字典，尤其注意 JOIN 多对多放大、支付与商品金额的区分。
无须编造答案或解释原因；只负责生成 SQL / 澄清 / 不支持，不得主动调用工具。
不要依赖提示词保证安全：数据库端还有强制执行检查。
"""

MAX_QUESTION_CHARS = 2000
MAX_MODEL_OUTPUT_CHARS = 14000


def _generation_error(code: str, message: str, elapsed_ms: float = 0.0) -> dict:
    return {
        "status": "error", "sql": None, "message": None,
        "error": {"code": code, "message": message},
        "generation_ms": round(elapsed_ms, 2), "usage": None,
    }


def parse_model_json(content: str) -> dict[str, str | None]:
    """Parse an exact model decision; fail closed on nonconforming content."""
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_MODEL_OUTPUT_CHARS:
        raise ValueError("模型响应为空或长度异常")
    payload = content.strip()
    if payload.startswith("```") and payload.endswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
            payload = "\n".join(lines[1:-1]).strip()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("模型响应必须是 JSON 对象")
    status = data.get("status")
    if status == "sql":
        if set(data) != {"status", "sql"}:
            raise ValueError("sql 响应只能包含 status 和 sql")
        sql = data["sql"]
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("SQL 缺失")
        return {"status": "sql", "sql": sql.strip(), "message": None}
    if status in {"clarify", "unsupported"}:
        if set(data) != {"status", "message"}:
            raise ValueError("非 SQL 响应只能包含 status 和 message")
        message = data["message"]
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise ValueError("澄清或不支持信息无效")
        return {"status": status, "sql": None, "message": message.strip()}
    raise ValueError("未知响应状态")


class DeepSeekSQLBackend:
    """OpenAI-compatible client, using the same .env keys as DataPilot V1.

    Real network calls are made *only* after CLI --live is explicitly passed.
    """

    def __init__(self, *, model: str | None = None, client: Any = None):
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env", encoding="utf-8-sig", override=True)
        self.model = model or os.getenv("OPENAI_MODEL", "deepseek-chat")
        if client is not None:
            self.client = client
            return
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 OPENAI_API_KEY，请检查本地 .env")
        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
            timeout=30.0,
            max_retries=0,
        )

    def invoke(self, messages: list[dict[str, str]]) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            max_tokens=1600,
        )
        if not response.choices:
            raise ValueError("模型没有返回候选回复")
        message = response.choices[0].message
        usage = getattr(response, "usage", None)
        return {
            "content": message.content,
            "usage": None if usage is None else {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            },
        }


def generate_sql_decision(
    question: str, *, backend: Any, catalog_path: Path | str | None = None,
) -> dict:
    """Generate once; does not automatically retry invalid or refused queries."""
    start = time.monotonic()
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        return _generation_error("invalid_question", "问题不能为空或超过 2000 字")
    try:
        context = build_schema_context(catalog_path) if catalog_path is not None else build_schema_context()
    except (OSError, ValueError, TypeError) as exc:
        return _generation_error("invalid_catalog", "无法读取有效的业务语义字典", (time.monotonic() - start) * 1000)

    messages = [
        {"role": "system", "content": SYSTEM_RULES + "\n业务语义字典（仅供生成，不代表数据库授权）：\n" + context},
        {"role": "user", "content": question.strip()},
    ]
    try:
        raw = backend.invoke(messages)
    except Exception:
        return _generation_error("llm_api_error", "模型调用失败，请检查网络、服务及本地配置", (time.monotonic() - start) * 1000)
    if not isinstance(raw, dict):
        return _generation_error("invalid_llm_output", "模型返回格式错误", (time.monotonic() - start) * 1000)
    try:
        parsed = parse_model_json(raw.get("content"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return _generation_error("invalid_llm_output", "模型未返回符合协议的 JSON", (time.monotonic() - start) * 1000)
    return {**parsed, "error": None, "generation_ms": round((time.monotonic() - start) * 1000, 2), "usage": raw.get("usage")}


def run_text_to_sql(
    question: str, db_path: Path | str, *, backend: Any,
    catalog_path: Path | str | None = None,
    executor: Any = None,
) -> dict:
    """LLM decision → optional SQL → enforced Stage 2A safe executor.

    SQL semantics require Gold-result evaluation; execution success != correct.
    """
    decision = generate_sql_decision(question, backend=backend, catalog_path=catalog_path)
    result = {
        "status": decision["status"],
        "question": question,
        "generated_sql": decision.get("sql"),
        "message": decision.get("message"),
        "generation_ms": decision["generation_ms"],
        "usage": decision.get("usage"),
        "execution": None,
        "error": decision.get("error"),
    }
    if decision["status"] != "sql":
        return result
    if executor is None:
        # Import only at the execution boundary: offline tests of the generation
        # protocol remain independent of SQLGlot and external API credentials.
        from app.v2_safe_sql import execute_safe_sql
        executor = execute_safe_sql
    try:
        execution = executor(decision["sql"], db_path)
    except Exception:
        result["status"] = "error"
        result["error"] = {"code": "executor_error", "message": "安全 SQL 执行器运行失败"}
        return result
    result["execution"] = execution
    if not isinstance(execution, dict) or execution.get("status") != "ok":
        result["status"] = "error"
        result["error"] = (execution.get("error") if isinstance(execution, dict) else None) or {
            "code": "execution_failed", "message": "查询被安全策略拒绝或执行失败"
        }
    else:
        result["status"] = "ok"
    return result
