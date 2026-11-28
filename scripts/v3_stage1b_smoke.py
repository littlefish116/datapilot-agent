"""Offline V3.1B LangGraph three-tool smoke; no DeepSeek network calls.

Requires existing trusted Olist SQLite and HGB model artifacts. No writes to DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib

from app.v3_routing import run_v3_turn
from app.v3_tool_service import V3ToolService

ROOT = Path(__file__).resolve().parents[1]


class FixedBackend:
    def __init__(self, route: dict, sql: dict | None = None):
        self.route, self.sql = route, sql
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        is_sql_generation = "业务语义字典" in messages[0]["content"]
        decision = self.sql if is_sql_generation else self.route
        if decision is None:
            raise AssertionError("unexpected LLM request")
        return {"content": json.dumps(decision, ensure_ascii=False), "usage": {"total_tokens": 0}}


def main():
    model_path = ROOT / "models" / "delivery_risk_hgb_v1.joblib"
    metadata_path = ROOT / "models" / "metadata.json"
    db = ROOT / "data" / "processed" / "olist.db"
    if not db.is_file() or not model_path.is_file() or not metadata_path.is_file():
        raise SystemExit("FAIL: 缺少本机可信 Olist 数据库或已训练 HGB 模型")
    model = joblib.load(model_path)  # Trusted model produced in the local project only.
    metadata = json.loads(metadata_path.read_text(encoding="utf8"))
    tests = [
        ("SQL", "2018年3月购买且已交付的订单有多少笔？",
         {"status": "route", "tool": "sql_analysis", "arguments": {}},
         {"status": "sql", "sql": ("SELECT COUNT(DISTINCT order_id) AS delivered_orders FROM orders "
                                   "WHERE order_status = 'delivered' AND "
                                   "strftime('%Y-%m', order_purchase_timestamp)='2018-03'")}),
        ("Report", "查询订单状态分布", {"status": "route", "tool": "business_report",
                                     "arguments": {"report_id": "order_status"}}, None),
        ("HGB", "7月星期二购买2件199.9元SP州订单，请预测延期风险",
         {"status": "route", "tool": "delivery_risk_prediction", "arguments": {"features": {
             "purchase_month": 7, "purchase_weekday": 2, "item_count": 2,
             "item_value": 199.9, "customer_state": "SP"}}}, None),
    ]
    for name, question, route, sql in tests:
        backend = FixedBackend(route, sql)
        service = V3ToolService(db_path=db, sql_backend=backend, model=model, metadata=metadata)
        result = run_v3_turn(question, backend=backend, service=service)
        assert result["status"] == "ok", (name, result)
        assert result["selected_tool"] == route["tool"]
        assert result["used_tools"] == [route["tool"]]
        assert backend.calls == (2 if name == "SQL" else 1)
        if name == "SQL":
            assert result["tool_result"]["data"]["rows"][0]["delivered_orders"] == 7003
        elif name == "Report":
            rows = result["tool_result"]["data"]["rows"]
            assert next(row["order_count"] for row in rows if row["order_status"] == "delivered") == 96478
        else:
            assert abs(result["tool_result"]["data"]["risk_score"] - 0.025889) < 0.00001
        print(f"PASS {name}: {result['selected_tool']} ({backend.calls} fake model decisions)")
    print("PASS V3.1B: LangGraph 单轮三工具离线调度完成；未访问 DeepSeek。")


if __name__ == "__main__":
    main()
