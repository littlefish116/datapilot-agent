"""Build a limited SQL schema/metric prompt from the *enforced* Stage 2A policy.

A prompt is informational, never an access-control boundary. All generated SQL
must still pass app.v2_safe_sql.execute_safe_sql before touching SQLite.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.v2_sql_policy import IDENTIFIER_COLUMNS, SQLITE_FUNCTIONS, TABLE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "benchmarks" / "metric_catalog.json"

# Small human-readable labels are helpful to the model; actual fields come only
# from TABLE_COLUMNS, never from these descriptions or an untrusted database.
TABLE_DESCRIPTIONS = {
    "orders": "订单级，主键 order_id；订单状态、购买时间、送达日期",
    "order_items": "商品条目级，(order_id, order_item_id) 为联合主键；price 不含运费",
    "order_payments": "支付明细级，一笔订单可能有多笔支付，payment_value 是支付金额",
    "customers": "客户记录，customer_id 每笔订单关联的客户键；customer_unique_id 是跨订单买家键",
    "products": "商品维度，用 product_id 关联商品分类",
    "sellers": "卖家维度，用 seller_id 关联卖家所在州",
    "category_translation": "商品分类名称英译映射",
}


def build_schema_context(catalog_path: str | Path = DEFAULT_CATALOG) -> str:
    """Return a UTF-8 JSON-encoded, business-aware *prompt context*.

    Only relationships between permitted tables are retained. In particular,
    the stage-1 catalog's geolocation relationship is omitted on purpose.
    """
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8-sig"))
    if not isinstance(catalog, dict):
        raise ValueError("业务语义字典必须为 JSON 对象")
    if not isinstance(catalog.get("table_relationships"), list):
        raise ValueError("缺少 table_relationships")
    if not isinstance(catalog.get("business_metrics"), list):
        raise ValueError("缺少 business_metrics")

    permitted_tables = set(TABLE_COLUMNS)
    relationships = []
    for entry in catalog["table_relationships"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("left") not in permitted_tables or entry.get("right") not in permitted_tables:
            continue
        relationships.append({
            key: entry[key]
            for key in ("left", "right", "keys", "cardinality", "warning")
            if key in entry
        })
    metrics = []
    for entry in catalog["business_metrics"]:
        if not isinstance(entry, dict):
            continue
        # Avoid copying outdated placeholder SQL examples (e.g. actual_date)
        # from the research catalog into the operational prompt.
        metrics.append({key: entry[key] for key in ("id", "grain", "definition", "limitations") if key in entry})

    payload = {
        "dialect": "sqlite",
        "source": "Olist 历史订单数据，不是实时系统",
        "allowed_tables": {
            table: {
                "description": TABLE_DESCRIPTIONS.get(table, "业务表"),
                "columns": sorted(columns),
            }
            for table, columns in sorted(TABLE_COLUMNS.items())
        },
        "joins": relationships,
        "metrics": metrics,
        "allowed_functions": sorted(SQLITE_FUNCTIONS),
        "restricted_output_identifier_columns": sorted(IDENTIFIER_COLUMNS),
        "hard_rules": [
            "只生成一个 SQLite SELECT 查询；可以使用非递归 CTE、JOIN、GROUP BY、聚合、窗口函数。",
            "只能使用 allowed_tables 中的表和字段，不要查询 sqlite_master、reviews、geolocation 或邮编/评论。",
            "不得在最终查询结果直接返回原始订单、客户、商品或卖家 ID，可用于 JOIN 或 COUNT(DISTINCT ...) 。",
            "日期延期定义：DATE(order_delivered_customer_date) > DATE(order_estimated_delivery_date)。",
            "月度问题按 orders.order_purchase_timestamp 的购买月份分组，除非提问另有说明。",
            "订单状态 delivered 不等于日期完整；延期率的分母需排除无实际/预计送达日期的订单。",
            "商品金额 SUM(order_items.price) 不包括运费；支付金额 SUM(order_payments.payment_value) 是不同业务指标。",
            "订单商品、订单支付均是一对多；不能同时 JOIN 再直接 SUM，否则会出现 Fan-out 重复。",
            "复购买家依据 customers.customer_unique_id 而不是 customer_id。",
            "不得假装 SQL 计算证明了因果关系；信息不足先澄清，字段/指标不存在时表示不支持。",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
