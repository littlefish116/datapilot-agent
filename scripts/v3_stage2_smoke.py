"""V3.2 two-turn REAL LangGraph + V2 safe SQL smoke, no DeepSeek calls.

Requires the existing Olist database, sqlglot and LangGraph in user's repo.
Run from project root: python -m scripts.v3_stage2_smoke
"""
from __future__ import annotations

import json
import re

from app.v3_conversation import V3SessionStore, run_v3_conversation_turn
from app.v3_tool_service import DB_PATH, V3ToolService


class FakeBackend:
    def __init__(self):
        self.route_calls = 0
        self.sql_calls = 0

    def invoke(self, messages):
        system = messages[0]["content"]
        question = messages[-1]["content"]
        if "单轮工具路由器" in system:
            self.route_calls += 1
            return {"content": json.dumps({
                "status": "route", "tool": "sql_analysis", "arguments": {},
            }), "usage": {"total_tokens": 0}}
        if "SQL 生成子模块" in system:
            self.sql_calls += 1
            date = re.search(r"(2018)\s*年\s*(3|4)\s*月", question)
            if not date:
                raise AssertionError("SQL backend got ungrounded date: " + question)
            key = f"{date.group(1)}-{int(date.group(2)):02d}"
            # Fixed, auditable SQL proposal. The V2 pipeline MUST validate it.
            sql = (
                "SELECT COUNT(*) AS n FROM orders "
                "WHERE order_status = 'delivered' "
                f"AND SUBSTR(order_purchase_timestamp, 1, 7) = '{key}' "
                "AND DATE(order_delivered_customer_date) IS NOT NULL "
                "AND DATE(order_estimated_delivery_date) IS NOT NULL"
            )
            return {"content": json.dumps({"status": "sql", "sql": sql}),
                    "usage": {"total_tokens": 0}}
        raise AssertionError("unexpected backend protocol")


def main():
    if not DB_PATH.is_file():
        raise SystemExit("FAIL: data/processed/olist.db 不存在，先准备本地数据")
    backend = FakeBackend()
    service = V3ToolService(db_path=DB_PATH, sql_backend=backend)
    store = V3SessionStore()
    first = run_v3_conversation_turn(
        "查询2018年3月已交付且送达及预计日期有效的订单数量",
        store=store, conversation_id=None, backend=backend, service=service,
    )
    if first["status"] != "ok" or first["selected_tool"] != "sql_analysis":
        raise AssertionError("first turn did not use safe SQL: " + str(first))
    first_rows = first["tool_result"]["data"]["rows"]
    assert first_rows == [{"n": 7003}], first_rows
    print("PASS 第一轮：2018-03 有效日期已交付订单 7003，V2 安全 SQL 已执行")

    second = run_v3_conversation_turn(
        "那 4 月呢？", store=store, conversation_id=first["conversation_id"],
        backend=backend, service=service,
    )
    assert second["status"] == "ok", second
    assert second["context_used"] is True
    assert second["context_resolution"] == "month_rewrite"
    assert second["conversation_id"] == first["conversation_id"]
    assert "2018年4月" in second["effective_question"]
    assert second["tool_result"]["data"]["rows"] == [{"n": 6798}]
    assert backend.route_calls == backend.sql_calls == 2
    assert second["automatic_repair"] is False
    print("PASS 第二轮：继承指标与年份，2018-04 有效日期已交付订单 6798")
    print("PASS 会话ID不变；2 次路由 + 2 次 SQL 生成；0 次线上 DeepSeek 请求")

    unrelated = run_v3_conversation_turn(
        "那 4 月呢？", store=store, conversation_id=None,
        backend=backend, service=service,
    )
    assert unrelated["status"] == "clarify"
    assert unrelated["tool_result"] is None
    print("PASS 新会话不能继承旧会话的上下文")


if __name__ == "__main__":
    main()
