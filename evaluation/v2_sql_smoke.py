"""CLI-only smoke demo. No FastAPI, DeepSeek, or API key required."""

import argparse
import json
from pathlib import Path

from app.v2_safe_sql import execute_safe_sql

ROOT = Path(__file__).resolve().parents[1]

SAMPLES = {
    'status_counts': """
        SELECT order_status, COUNT(*) AS order_count
        FROM orders GROUP BY order_status ORDER BY order_count DESC
    """,
    'state_delivery': """
        SELECT c.customer_state,
               COUNT(DISTINCT o.order_id) AS delivered_orders
        FROM orders o JOIN customers c ON o.customer_id=c.customer_id
        WHERE o.order_status='delivered'
        GROUP BY c.customer_state
        ORDER BY delivered_orders DESC
        LIMIT 10
    """,
    'write_denial': "DELETE FROM orders WHERE 1=1",
    'schema_denial': "SELECT COUNT(*) FROM sqlite_master",
}


def main():
    parser = argparse.ArgumentParser(description='DataPilot V2 safe SQL smoke checks')
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'processed' / 'olist.db')
    parser.add_argument('--sample', choices=[*SAMPLES, 'all'], default='all')
    args = parser.parse_args()
    for name, sql in SAMPLES.items():
        if args.sample != 'all' and args.sample != name:
            continue
        result = execute_safe_sql(sql, args.db)
        preview = {k: v for k, v in result.items() if k != 'rows'}
        preview['sample_rows'] = result['rows'][:3]
        print(f'== {name} ==')
        print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
