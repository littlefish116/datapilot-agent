
from pathlib import Path
from contextlib import closing
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

DB_PATH = ROOT / "data" / "processed" / "olist.db"
SQL_DIR = ROOT / "sql" / "queries"

# Agent未来只能选择这些预定义查询
REPORTS = {
    "order_status": "01_order_status.sql",
    "monthly_late_delivery": "03_monthly_late_delivery.sql",
    "top_categories": "04_top_categories.sql",
    "top_states": "05_top_states.sql",
}

MAX_ROWS = 50


def run_report(report_id: str):

    if report_id not in REPORTS:
        raise ValueError("不支持的报表ID")

    sql_path = SQL_DIR / REPORTS[report_id]
    sql = sql_path.read_text(encoding="utf-8")

    # 只读连接，避免API修改数据库
    db_uri = DB_PATH.resolve().as_uri() + "?mode=ro"

    with closing(
        sqlite3.connect(db_uri, uri=True)
    ) as conn:

        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)

        # 最多返回51行，用于判断结果是否截断
        fetched = cursor.fetchmany(MAX_ROWS + 1)

    truncated = len(fetched) > MAX_ROWS
    rows = [dict(row) for row in fetched[:MAX_ROWS]]

    return {
        "report_id": report_id,
        "returned_rows": len(rows),
        "truncated": truncated,
        "rows": rows
    }
