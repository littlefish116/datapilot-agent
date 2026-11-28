"""V2 experimental request orchestration; no retries, no SQL rewriting.

The SQL executor is the mandatory security boundary. Semantic-risk checks are
advisory and deliberately fail closed at this API surface: risky generated
SQL is returned for review but NOT executed by this route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.v2_text_to_sql import generate_sql_decision
from app.v2_response_policy import normalize_unsupported_message


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def run_v2_query(
    question: str,
    db_path: Path | str,
    *,
    backend: Any,
    inspector: Callable[[str], dict] | None = None,
    executor: Callable[..., dict] | None = None,
) -> dict:
    """One LLM decision; optional one safe execution; no retry or repair.

    Injectable inspector / executor support offline tests. The endpoint always
    uses the real implementations, never user-supplied implementations.
    """
    decision = generate_sql_decision(question, backend=backend)
    response = {
        "status": decision["status"],
        "question": question,
        "generated_sql": decision.get("sql"),
        "message": decision.get("message"),
        "generation_ms": decision.get("generation_ms"),
        "usage": decision.get("usage"),
        "risk_review": None,
        "execution": None,
        "error": decision.get("error"),
        "automatic_repair": False,
    }
    if decision["status"] != "sql":
        if decision["status"] == "unsupported":
            response["message"] = normalize_unsupported_message(question, response["message"])
        return response

    if inspector is None:
        from app.v2_semantic_risk_guard import inspect_semantic_risks
        inspector = inspect_semantic_risks
    try:
        risk_review = inspector(decision["sql"])
    except Exception:
        response["status"] = "error"
        response["error"] = _error("semantic_check_error", "SQL 语义风险检查失败，未执行查询")
        return response
    if not isinstance(risk_review, dict) or risk_review.get("status") != "ok":
        response["status"] = "error"
        response["error"] = _error("sql_validation_failed", "SQL 未通过语义检查入口，未执行查询")
        return response

    response["risk_review"] = risk_review
    if risk_review.get("risk_count", 0) or risk_review.get("disposition") == "needs_review":
        response["status"] = "needs_review"
        response["message"] = "检测到可能影响统计口径的 SQL 风险；未执行该查询，请人工核验。"
        return response

    if executor is None:
        from app.v2_safe_sql import execute_safe_sql
        executor = execute_safe_sql
    try:
        execution = executor(decision["sql"], db_path)
    except Exception:
        response["status"] = "error"
        response["error"] = _error("executor_error", "安全 SQL 执行器运行失败")
        return response

    if not isinstance(execution, dict):
        response["status"] = "error"
        response["error"] = _error("executor_error", "安全 SQL 执行器返回格式错误")
        return response
    response["execution"] = execution
    if execution.get("status") != "ok":
        response["status"] = "error"
        response["error"] = execution.get("error") or _error("execution_failed", "查询执行失败")
    elif execution.get("truncated"):
        response["status"] = "partial"
        response["message"] = "查询结果已截断，仅展示部分行，不能视为完整结果。"
    else:
        response["status"] = "ok"
    return response
