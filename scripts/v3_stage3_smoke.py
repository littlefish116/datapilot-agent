"""Offline V3.3 smoke: simulated initial timeout, real V2 safe candidate.

Requires the user's installed sqlglot, original local Olist database, and
V2 schema catalog. Never contacts DeepSeek and never adjusts V2 resource caps.
This is NOT a replay of the frozen Hard8/X08 first-run or an automatic X08 fix.
Run: python -m scripts.v3_stage3_smoke
"""
from __future__ import annotations

import json
from pathlib import Path

from app.v2_runtime import run_v2_query
from app.v2_safe_sql import execute_safe_sql
from app.v3_sql_recovery import run_v3_sql_recovery
from app.v3_tool_service import DB_PATH

OLD = ("SELECT COUNT(*) AS n FROM "
       "(SELECT order_status FROM orders WHERE order_status = 'delivered') AS t")
NEW = "SELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered'"


class Model:
    def __init__(self, first=OLD, repair=NEW):
        self.prompts = []
        self.first = first
        self.repair = repair

    def invoke(self, messages):
        self.prompts.append(messages)
        sql = self.first if len(self.prompts) == 1 else self.repair
        return {"content": json.dumps({"status": "sql", "sql": sql}),
                "usage": {"total_tokens": 0}}


def run():
    if not DB_PATH.is_file():
        raise RuntimeError("未找到 data/processed/olist.db；请先准备本地 Olist 数据")
    backend = Model()
    calls = []

    def runner(question, db_path, *, backend):
        calls.append(question)
        if len(calls) == 1:
            # Synthetic timeout in the *first* V2 execution only. This
            # exercises recovery control flow, not SQLite performance.
            def fake_timeout(sql, path):
                return {"status": "error", "sql": sql,
                        "error": {"code": "query_timeout", "message": "查询超时或超过执行预算"},
                        "rows": [], "columns": [], "returned_rows": 0, "truncated": False}
            return run_v2_query(question, db_path, backend=backend,
                                executor=fake_timeout)
        return run_v2_query(question, db_path, backend=backend,
                            executor=execute_safe_sql)

    result = run_v3_sql_recovery("统计已交付订单数", DB_PATH,
                                 backend=backend, runner=runner)
    assert len(backend.prompts) == 2, "必须正好一次原始生成、一次修复生成"
    assert len(calls) == 2, "必须正好一次初次查询、一次受控重试"
    assert result["status"] == "ok", result
    assert result["automatic_repair"] is True
    assert result["recovery"]["outcome"] == "technical_success"
    assert result["recovery"]["semantic_equivalence_proven"] is False
    assert result["recovery"]["budget_increased"] is False
    value = result["execution"]["rows"][0]["n"]
    assert value == 96478, value
    print(f"PASS 原始执行模拟 query_timeout；受控候选重新走 V2 安全执行器：delivered={value}")
    print("PASS 修复模型调用 1 次；重试执行 1 次；不放宽 SQL 执行预算")

    # Unauthorized source must NOT request a new SQL or execute a repair.
    bad = Model(first="SELECT COUNT(*) AS n FROM order_reviews")
    second_calls = []
    def bad_runner(question, db_path, *, backend):
        second_calls.append(1)
        return run_v2_query(question, db_path, backend=backend)
    blocked = run_v3_sql_recovery("查询订单评分", DB_PATH,
                                  backend=bad, runner=bad_runner)
    assert blocked["status"] == "error" and blocked["automatic_repair"] is False
    assert len(bad.prompts) == len(second_calls) == 1
    print("PASS 未授权表不会触发修复生成或第二次执行")
    print("说明：首次超时为模拟故障，未对 Hard8/X08 首次得分做任何改写；0 次 DeepSeek 请求")


if __name__ == "__main__":
    run()
