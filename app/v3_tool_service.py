"""V3.1A: standard, synchronous adapters for existing DataPilot capabilities.

No FastAPI routes, LangGraph agent, model training or SQL rewriting are added.
In the SQL path V2's run_v2_query remains the mandatory security boundary.
Do not expose this library directly as a network API: V2's local-only HTTP
access check lives in app.v2_api, not in this library.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from app.sql_tools import REPORTS, run_report as existing_report_runner
from app.v2_runtime import run_v2_query

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "olist.db"
PREDICT_FIELDS = (
    "purchase_month", "purchase_weekday", "item_count", "item_value", "customer_state",
)
TOOLS = ("sql_analysis", "business_report", "delivery_risk_prediction")
ALLOWED_STATUSES = frozenset({"ok", "partial", "clarify", "unsupported", "needs_review", "error"})


def _result(tool: str, status: str, *, data: Any = None,
            message: str | None = None, error: dict | None = None,
            evidence: dict | None = None) -> dict:
    """Uniform result envelope. Never synthesize business conclusions."""
    if tool not in TOOLS or status not in ALLOWED_STATUSES:
        raise ValueError("invalid V3 tool result")
    return {
        "tool": tool,
        "status": status,
        "data": data,
        "message": message,
        "error": error,
        "evidence": evidence or {},
    }


def _failure(tool: str, code: str, message: str, *, evidence: dict | None = None) -> dict:
    return _result(tool, "error", error={"code": code, "message": message}, evidence=evidence)


class V3ToolService:
    """Tool adapters with explicit dependencies, suitable for later agent wiring.

    Inject the existing FastAPI lifespan's `app.state.model` and `metadata`
    for prediction. `sql_backend` is a trusted server-side DeepSeekSQLBackend,
    not supplied by users. Optional runners are for deterministic tests only.
    """

    def __init__(self, *, db_path: str | Path = DB_PATH,
                 sql_backend: Any = None,
                 model: Any = None,
                 metadata: Mapping[str, Any] | None = None,
                 sql_runner: Callable[..., dict] | None = None,
                 report_runner: Callable[[str], dict] | None = None):
        self.db_path = Path(db_path)
        self.sql_backend = sql_backend
        self.model = model
        self.metadata = metadata
        self._sql_runner = sql_runner or run_v2_query
        self._report_runner = report_runner or existing_report_runner

    def sql_analysis(self, question: str) -> dict:
        """One generation + V2 validation / risk gate / safe execution."""
        tool = "sql_analysis"
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            return _failure(tool, "invalid_question", "问题不能为空或超过 2000 字")
        if not self.db_path.is_file():
            return _failure(tool, "missing_database", "业务数据库不可用，未调用模型")
        if self.sql_backend is None:
            return _failure(tool, "sql_backend_unavailable", "SQL 生成模型尚未配置")
        try:
            raw = self._sql_runner(question.strip(), self.db_path, backend=self.sql_backend)
        except Exception:
            return _failure(tool, "sql_service_error", "SQL 查询服务异常")
        if not isinstance(raw, dict) or raw.get("status") not in ALLOWED_STATUSES:
            return _failure(tool, "invalid_sql_result", "SQL 查询服务返回格式错误")

        status = raw["status"]
        execution = raw.get("execution")
        evidence = {
            "source": "v2_safe_sql_pipeline",
            "generated_sql": raw.get("generated_sql"),
            "risk_review": raw.get("risk_review"),
            "generation_ms": raw.get("generation_ms"),
            "usage": raw.get("usage"),
            "automatic_repair": False,
        }
        if status in {"ok", "partial"}:
            if (not isinstance(execution, dict)
                    or execution.get("status") != "ok"
                    or not isinstance(execution.get("rows"), list)
                    or not isinstance(execution.get("columns"), list)):
                return _failure(tool, "invalid_sql_result", "SQL 查询执行结果格式错误", evidence=evidence)
            if status == "ok" and execution.get("truncated"):
                return _failure(tool, "invalid_sql_result", "SQL 查询截断状态不一致", evidence=evidence)
            if status == "partial" and not execution.get("truncated"):
                return _failure(tool, "invalid_sql_result", "SQL 查询截断状态不一致", evidence=evidence)
            data = {
                "columns": execution["columns"],
                "rows": execution["rows"],
                "returned_rows": execution.get("returned_rows", len(execution["rows"])),
                "truncated": bool(execution.get("truncated")),
            }
            evidence["executed_sql"] = execution.get("sql")
            evidence["execution_ms"] = execution.get("duration_ms")
            return _result(tool, status, data=data, message=raw.get("message"), evidence=evidence)

        if status == "needs_review" and execution is not None:
            return _failure(tool, "invalid_sql_result", "风险查询不应包含执行结果", evidence=evidence)
        if status in {"clarify", "unsupported"} and execution is not None:
            return _failure(tool, "invalid_sql_result", "未执行状态不应包含执行结果", evidence=evidence)
        return _result(
            tool, status, message=raw.get("message"),
            error=raw.get("error") if status == "error" else None,
            evidence=evidence,
        )

    def business_report(self, report_id: str) -> dict:
        """Directly call the V1 fixed SQL runner; no loopback HTTP request."""
        tool = "business_report"
        if not isinstance(report_id, str) or report_id not in REPORTS:
            return _result(tool, "unsupported", message="未开放此固定报表")
        try:
            raw = self._report_runner(report_id)
        except (sqlite3.Error, OSError):
            return _failure(tool, "report_unavailable", "业务报表或数据库暂时不可用")
        except (ValueError, KeyError):
            return _failure(tool, "report_error", "业务报表执行失败")
        except Exception:
            return _failure(tool, "report_error", "业务报表执行异常")
        if (not isinstance(raw, dict)
                or raw.get("report_id") != report_id
                or not isinstance(raw.get("rows"), list)
                or not isinstance(raw.get("truncated"), bool)
                or raw.get("returned_rows") != len(raw["rows"])):
            return _failure(tool, "invalid_report_result", "业务报表返回格式错误")
        status = "partial" if raw["truncated"] else "ok"
        return _result(
            tool, status,
            data={
                "rows": raw["rows"],
                "returned_rows": raw["returned_rows"],
                "truncated": raw["truncated"],
            },
            message="固定报表结果已截断，不能视为完整数据" if raw["truncated"] else None,
            evidence={"source": "v1_fixed_report", "report_id": report_id},
        )

    def delivery_risk_prediction(self, features: Mapping[str, Any] | None) -> dict:
        """Use the V1 *exact* validation contract and already-loaded HGB model.

        No inferred feature defaults, model file deserialization, HTTP calls or
        probability calibration occur in this adapter.
        """
        tool = "delivery_risk_prediction"
        if not isinstance(features, Mapping):
            return _failure(tool, "invalid_features", "预测输入必须是五项特征的对象")
        missing = [field for field in PREDICT_FIELDS if field not in features]
        if missing:
            return _result(
                tool, "clarify",
                message="请补充预测必需的参数：" + "、".join(missing),
                evidence={"source": "v1_hgb_model", "missing_fields": missing},
            )
        try:
            # Lazy import prevents a module-level cycle when V3 is later wired
            # into app.main; this is the exact schema used by POST /api/predict.
            from app.main import OrderInput, EXPECTED_FEATURES
            import pandas as pd

            order = OrderInput.model_validate(dict(features))
        except ValidationError as exc:
            fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
            return _failure(tool, "invalid_features", "预测特征未通过校验：" + "、".join(fields))
        except ImportError:
            return _failure(tool, "model_schema_unavailable", "V1 预测输入校验模块不可用")
        except (TypeError, ValueError, AttributeError):
            return _failure(tool, "invalid_features", "预测特征格式无效")

        if (self.model is None or not isinstance(self.metadata, Mapping)):
            return _failure(tool, "model_unavailable", "延期风险预测模型尚未加载")
        if (self.metadata.get("features") != EXPECTED_FEATURES
                or set(EXPECTED_FEATURES) != set(PREDICT_FIELDS)
                or not isinstance(self.metadata.get("model_name"), str)):
            return _failure(tool, "model_schema_mismatch", "模型特征配置不匹配")
        try:
            table = pd.DataFrame([order.model_dump()], columns=EXPECTED_FEATURES)
            score = float(self.model.predict_proba(table)[0, 1])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid model score")
        except Exception:
            return _failure(tool, "prediction_failed", "模型推理失败，未返回有效评分")
        return _result(
            tool, "ok",
            data={
                "model_name": self.metadata["model_name"],
                "risk_score": round(score, 6),
                "score_type": "uncalibrated_model_score",
                "notice": "模型评分未经概率校准，不能解释为真实延期概率或自动划分风险等级。",
            },
            evidence={"source": "v1_hgb_model", "input_features": order.model_dump()},
        )
