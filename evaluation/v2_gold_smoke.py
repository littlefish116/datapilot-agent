"""Run V1's six gold SQL queries through the new restricted V2 executor."""

import argparse
import json
from pathlib import Path

from app.v2_safe_sql import execute_safe_sql

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / 'benchmarks' / 'gold_sql'


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, default=ROOT / 'data' / 'processed' / 'olist.db')
    args = p.parse_args()
    if not GOLD.is_dir():
        p.error('缺少 benchmarks/gold_sql；请先在含 Stage1 评测代码的项目上运行')
    for file in sorted(GOLD.glob('*.sql')):
        result = execute_safe_sql(file.read_text(encoding='utf-8'), args.db)
        print(json.dumps({
            'file': file.name,
            'status': result['status'],
            'rows': result['returned_rows'],
            'truncated': result['truncated'],
            'error': result['error'],
            'first_row': result['rows'][0] if result['rows'] else None,
        }, ensure_ascii=False))
    print('注意：执行成功只证明 SQL 被安全层接受；统计口径仍需与 references.json 对照。')


if __name__ == '__main__':
    main()
