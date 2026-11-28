"""V3.2 monthly answers: real local Olist report + real LangGraph, fake LLM.

Usage: python -m scripts.v3_stage2_answer_smoke
No DeepSeek request. No SQL rewrite, model prediction, or DB write.
"""
from __future__ import annotations

import json

from app.v3_conversation import V3SessionStore, run_v3_conversation_turn
from app.v3_tool_service import DB_PATH, V3ToolService


class RoutingBackend:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        assert "单轮工具路由器" in messages[0]["content"], "unexpected LLM call"
        return {"content": json.dumps({
            "status": "route", "tool": "business_report",
            "arguments": {"report_id": "monthly_late_delivery"},
        }), "usage": {"total_tokens": 0}}


def main():
    if not DB_PATH.is_file():
        raise SystemExit("FAIL: 没有找到 data/processed/olist.db")
    backend = RoutingBackend()
    service = V3ToolService()
    store = V3SessionStore()
    first = run_v3_conversation_turn(
        "查询2018年3月的订单延期率", store=store, conversation_id=None,
        backend=backend, service=service)
    second = run_v3_conversation_turn(
        "那4月呢？", store=store, conversation_id=first["conversation_id"],
        backend=backend, service=service)
    assert first["status"] == second["status"] == "ok"
    assert first["selected_tool"] == second["selected_tool"] == "business_report"
    assert "18.96%" in first["answer"] and "7,003" in first["answer"], first["answer"]
    assert "4.50%" in second["answer"] and "6,798" in second["answer"], second["answer"]
    assert "2016" not in second["answer"] and "18.96%" not in second["answer"]
    assert second["effective_question"] == "查询2018年4月的订单延期率"
    assert second["context_used"] is True
    assert len(first["tool_result"]["data"]["rows"]) >= 20
    assert (first["tool_result"]["data"]["rows"]
            == second["tool_result"]["data"]["rows"])
    assert first["conversation_id"] == second["conversation_id"]
    assert first["automatic_repair"] is second["automatic_repair"] is False
    assert backend.calls == 2
    print("PASS 2018-03：18.96% / 7003 笔，固定报表精确回答")
    print("PASS 2018-04：4.50% / 6798 笔，继承问题后精确回答")
    print("PASS 完整月度报表未被修改；LangGraph 2 轮；0 次 DeepSeek 请求")


if __name__ == "__main__":
    main()
