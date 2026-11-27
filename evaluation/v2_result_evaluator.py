"""Strict task-output comparison for the six *development* SQL benchmark cases.

This does NOT prove arbitrary query correctness, compare SQL text, or score
auxiliary Gold columns not requested by the corresponding user question.
Pure Python stdlib; no model calls or database writes.
"""
from __future__ import annotations

import math
from typing import Any

# observed LLM output column -> Gold column, with per-field numeric tolerances.
# This fixed scoring contract is recorded *before* running future experiments.
SPECS = {
    "D01": {"key": "payment_type", "columns": {"payment_type": "payment_type", "payment_amount_brl": "amount_brl", "share_pct": "amount_share_pct"}, "tolerance": {"payment_amount_brl": 0.011, "share_pct": 0.011}},
    "D02": {"key": None, "columns": {"avg_freight_per_order": "avg_freight_per_order_brl"}, "tolerance": {"avg_freight_per_order": 0.011}},
    "D03": {"key": None, "columns": {"average_items_per_order": "avg_item_rows_per_order"}, "tolerance": {"average_items_per_order": 0.0001}},
    "D04": {"key": None, "columns": {"repeat_buyer_count": "buyers_with_at_least_two_orders"}, "tolerance": {}},
    "D05": {"key": "customer_state", "columns": {"customer_state": "customer_state", "delivered_orders_with_dates": "delivered_with_dates", "late_delivery_rate_pct": "late_rate_pct"}, "tolerance": {"late_delivery_rate_pct": 0.011}},
    "D06": {"key": "seller_state", "columns": {"seller_state": "seller_state", "delivered_item_value_brl": "item_value_brl"}, "tolerance": {"delivered_item_value_brl": 0.011}},
}


def _numeric_equal(observed: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(observed, bool) or isinstance(expected, bool):
        return observed is expected
    if isinstance(observed, (int, float)) and isinstance(expected, (int, float)):
        return math.isfinite(observed) and math.isfinite(expected) and abs(observed - expected) <= tolerance
    return observed == expected


def _row_map(rows: list[dict], key: str) -> dict | None:
    mapped: dict = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in mapped:
            return None
        mapped[value] = row
    return mapped


def compare_task_result(case_id: str, response: dict, reference: dict) -> dict:
    """Compare *requested* output fields, with row-set equality and no truncation."""
    if case_id not in SPECS:
        raise ValueError(f"未定义的评分 case ID: {case_id}")
    spec = SPECS[case_id]
    issues: list[str] = []
    execution = response.get("execution") if isinstance(response, dict) else None
    if not isinstance(execution, dict) or response.get("status") != "ok" or execution.get("status") != "ok":
        err = response.get("error") if isinstance(response, dict) else None
        if not err and isinstance(execution, dict):
            err = execution.get("error")
        issues.append("查询未成功执行: " + str(err))
        return {"case_id": case_id, "verdict": "fail", "reason": "execution_failure", "observed_rows": None, "gold_rows": None, "issues": issues}
    if execution.get("truncated") is not False:
        issues.append("执行结果被截断或缺少截断标识")
    if not isinstance(reference, dict) or reference.get("status") != "ok" or reference.get("truncated") is not False:
        return {"case_id": case_id, "verdict": "not_scored", "reason": "invalid_gold", "observed_rows": None, "gold_rows": None, "issues": ["参考 SQL 结果缺失、失败或被截断"]}
    obs_rows, gold_rows = execution.get("rows"), reference.get("rows")
    if not isinstance(obs_rows, list) or not isinstance(gold_rows, list) or any(not isinstance(r, dict) for r in obs_rows + gold_rows):
        return {"case_id": case_id, "verdict": "fail", "reason": "invalid_rows", "observed_rows": None, "gold_rows": None, "issues": ["行数据格式不正确"]}
    if not gold_rows:
        return {"case_id": case_id, "verdict": "not_scored", "reason": "empty_gold", "observed_rows": len(obs_rows), "gold_rows": 0, "issues": ["参考结果为空，当前 D01–D06 应有非空参考结果"]}
    if execution.get("returned_rows") != len(obs_rows):
        issues.append("returned_rows 与实际返回数据不一致")
    if len(obs_rows) != len(gold_rows):
        issues.append(f"行数不符: actual={len(obs_rows)}, gold={len(gold_rows)}")
    key = spec["key"]
    if key:
        obs_map = _row_map(obs_rows, key)
        gold_map = _row_map(gold_rows, spec["columns"][key])
        if obs_map is None or gold_map is None:
            issues.append("分组键缺失、重复或非法")
            pairs = []
        else:
            missing, extra = sorted(gold_map.keys() - obs_map.keys()), sorted(obs_map.keys() - gold_map.keys())
            if missing:
                issues.append(f"缺失分组键: {missing}")
            if extra:
                issues.append(f"多余分组键: {extra}")
            pairs = [(k, obs_map[k], gold_map[k]) for k in sorted(gold_map.keys() & obs_map.keys())]
    else:
        pairs = [("scalar", obs_rows[0], gold_rows[0])] if len(obs_rows) == len(gold_rows) == 1 else []
    for name, observed, gold in pairs:
        for obs_field, gold_field in spec["columns"].items():
            if obs_field == key:
                continue
            if obs_field not in observed:
                issues.append(f"{name}: 缺少输出列 {obs_field}")
                continue
            if gold_field not in gold:
                issues.append(f"{name}: Gold 缺少比较列 {gold_field}")
                continue
            tol = spec["tolerance"].get(obs_field, 0)
            if not _numeric_equal(observed[obs_field], gold[gold_field], tol):
                issues.append(f"{name}/{obs_field}: actual={observed[obs_field]!r}, gold={gold[gold_field]!r}, tolerance={tol}")
    return {
        "case_id": case_id,
        "verdict": "fail" if issues else "pass",
        "reason": "mismatch" if issues else "matched_requested_metrics",
        "observed_rows": len(obs_rows), "gold_rows": len(gold_rows),
        "checked_fields": list(spec["columns"].keys()),
        "issues": issues[:100],
    }


def summarize(records: list[dict]) -> dict:
    scores = [r["score"] for r in records]
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    generation_ms, execution_ms = [], []
    for r in records:
        res = r["response"]
        usage = res.get("usage") or {}
        for k in totals:
            v = usage.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                totals[k] += v
        if isinstance(res.get("generation_ms"), (int, float)):
            generation_ms.append(res["generation_ms"])
        ex = res.get("execution") or {}
        if isinstance(ex, dict) and isinstance(ex.get("duration_ms"), (int, float)):
            execution_ms.append(ex["duration_ms"])
    scored = sum(s["verdict"] != "not_scored" for s in scores)
    passed = sum(s["verdict"] == "pass" for s in scores)
    return {
        "case_count": len(records), "scored_cases": scored, "passed_cases": passed,
        "development_case_pass_rate": round(passed / scored, 4) if scored else None,
        "generated_sql_cases": sum(bool(r["response"].get("generated_sql")) for r in records),
        "execution_ok_cases": sum((r["response"].get("execution") or {}).get("status") == "ok" for r in records),
        "total_token_usage": totals,
        "mean_generation_ms": round(sum(generation_ms) / len(generation_ms), 2) if generation_ms else None,
        "mean_execution_ms": round(sum(execution_ms) / len(execution_ms), 2) if execution_ms else None,
        "note": "仅评分 D01–D06 已知开发题的用户请求指标，非全量字段校验或未知问题泛化准确率。",
    }
