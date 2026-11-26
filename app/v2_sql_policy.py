"""DataPilot V2: narrowly scoped Olist analytics access policy.

This is intentionally smaller than the nine-table source schema.  In particular,
reviews, free-form comments, geolocation, and ZIP prefixes are unavailable.
"""

from dataclasses import dataclass

TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "orders": frozenset({
        "order_id", "customer_id", "order_status", "order_purchase_timestamp",
        "order_delivered_customer_date", "order_estimated_delivery_date",
    }),
    "order_items": frozenset({
        "order_id", "order_item_id", "product_id", "seller_id", "price", "freight_value",
    }),
    "order_payments": frozenset({
        "order_id", "payment_sequential", "payment_type", "payment_value",
    }),
    "customers": frozenset({
        "customer_id", "customer_unique_id", "customer_state",
    }),
    "products": frozenset({"product_id", "product_category_name"}),
    "sellers": frozenset({"seller_id", "seller_state"}),
    "category_translation": frozenset({
        "product_category_name", "product_category_name_english",
    }),
}

# SQLite's run-time authorizer is the definitive gate for actual base columns.
SQLITE_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "round", "coalesce", "ifnull",
    "nullif", "date", "strftime", "substr", "substring", "trim", "lower",
    "upper", "cast", "abs", "length",
})

# Olist identifiers may be used internally for joins or COUNT(DISTINCT ...),
# but are not intended to be displayed as raw row-level results.
IDENTIFIER_COLUMNS = frozenset({
    "order_id", "customer_id", "customer_unique_id", "product_id", "seller_id",
    "order_item_id", "payment_sequential",
})


@dataclass(frozen=True)
class SqlLimits:
    max_sql_chars: int = 12000
    max_ast_nodes: int = 600
    max_rows: int = 50
    max_result_bytes: int = 65536
    timeout_seconds: float = 3.0
    max_vm_steps: int = 5_000_000
    vm_check_every: int = 1000
