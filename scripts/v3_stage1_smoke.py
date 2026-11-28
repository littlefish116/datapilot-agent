"""Offline smoke check of all three V3.1A adapters against local trusted data.

NO real LLM calls. Uses a fixed SQL proposal solely to test the *existing*
V2 safety + execution path. This is not a Text-to-SQL generation benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib

from app.v3_tool_service import V3ToolService

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "delivery_risk_hgb_v1.joblib"
METADATA_PATH = ROOT / "models" / "metadata.json"
DB_PATH = ROOT / "data" / "processed" / "olist.db"


class FixedSQLBackend:
    def invoke(self, _messages):
        return {"content": json.dumps({
            "status": "sql",
            "sql": "SELECT COUNT(DISTINCT order_id) AS delivered_orders "
                   "FROM orders WHERE order_status = 'delivered' "
                   "AND strftime('%Y-%m', order_purchase_timestamp) = '2018-03'",
        }), "usage": {"total_tokens": 0}}


def main():
    missing = [str(path) for path in (DB_PATH, MODEL_PATH, METADATA_PATH)
               if not path.is_file()]
    if missing:
        raise SystemExit("缺少本地数据或模型：" + ", ".join(missing))

    # Only load the model produced by this project's trusted local training.
    model = joblib.load(MODEL_PATH)
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    tools = V3ToolService(db_path=DB_PATH, sql_backend=FixedSQLBackend(),
                          model=model, metadata=metadata)

    dynamic = tools.sql_analysis("2018年3月购买且已交付的订单有多少笔？")
    if dynamic["status"] != "ok" or dynamic["data"]["rows"] != [{"delivered_orders": 7003}]:
        raise SystemExit("V3 动态 SQL 工具未通过本地冒烟检查：" + repr(dynamic))
    print("PASS sql_analysis: 2018-03 delivered_orders=7003 (fixed SQL, no LLM)")

    report = tools.business_report("order_status")
    if report["status"] != "ok" or not any(
        row.get("order_status") == "delivered" and row.get("order_count") == 96478
        for row in report["data"]["rows"]
    ):
        raise SystemExit("V3 固定报表工具未通过本地冒烟检查：" + repr(report))
    print("PASS business_report: delivered=96478")

    pred = tools.delivery_risk_prediction({
        "purchase_month": 7, "purchase_weekday": 2, "item_count": 2,
        "item_value": 199.9, "customer_state": "SP",
    })
    if pred["status"] != "ok" or pred["data"]["score_type"] != "uncalibrated_model_score":
        raise SystemExit("V3 模型预测工具未通过本地冒烟检查：" + repr(pred))
    print("PASS delivery_risk_prediction: risk_score=" + str(pred["data"]["risk_score"]))
    print("说明：risk_score 未经概率校准，不能解释为真实延期概率。")
    print("PASS: V3.1A 三工具本地离线冒烟检查完成。")


if __name__ == "__main__":
    main()
