"""Non-LLM adversarial SQLite probes; do not modify the frozen Holdout suite.

The H06 candidate query is accepted ONLY when its saved first-run response bytes
match the previously reviewed SHA256. Queries run against a temporary synthetic
DB in SQLite read-only mode with a deny-by-default authorizer and VM budget.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from evaluation import v2_holdout_benchmark as h
from evaluation.v2_holdout_posthoc_audit import DEFAULT_REVIEW, _read_verified

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'reports/agent_eval_v2_boundaries'


def _readonly_select(db: Path, sql: str, allowed: dict[str, set[str]]) -> list[dict]:
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        functions = {'count','sum','avg','round','strftime','date','nullif','coalesce'}
        def authorize(op, a, b, _db, _source):
            if op == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if op == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if (a or '').lower() in allowed and (b or '').lower() in allowed[(a or '').lower()] | {''} else sqlite3.SQLITE_DENY
            if op == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (b or a or '').lower() in functions else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY
        con.set_authorizer(authorize)
        budget = [0]
        def watchdog():
            budget[0] += 1
            return 1 if budget[0] >= 2000 else 0
        con.set_progress_handler(watchdog, 100)
        cur = con.execute(sql)
        if cur.description is None:
            raise ValueError('只允许返回结果集的 SELECT')
        return [dict(r) for r in cur.fetchall()]


def probe(run_dir: Path, review_file: Path = DEFAULT_REVIEW) -> dict:
    review = json.loads(review_file.read_text(encoding='utf-8'))
    h06 = review['items']['H06']
    response = _read_verified(run_dir / 'responses' / 'H06.json', h06['response_sha256'])
    if response.get('execution', {}).get('status') != 'ok':
        raise ValueError('固定 H06 首次 SQL 未成功执行，不能用于对抗探针')
    model_sql = response['generated_sql']
    gold_sql = (ROOT / 'benchmarks/holdout_gold_sql/H06.sql').read_text(encoding='utf-8-sig')
    expected_gold_sha = review['suite_sha256']
    if h.sha256(ROOT / 'benchmarks/v2_holdout_cases.json') != expected_gold_sha:
        raise ValueError('Holdout 题集变更，拒绝使用')
    # Compare exact Gold SQL hash from the frozen manifest, not merely file names.
    manifest = _read_verified(run_dir / 'run_manifest.json', review['original_manifest_sha256'])
    if h.sha256(ROOT / 'benchmarks/holdout_gold_sql/H06.sql') != manifest['hashes']['gold_sql_sha256']['H06']:
        raise ValueError('H06 参考 SQL 发生变化')
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / 'edge_fixture.db'
        with closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT, order_status TEXT, order_purchase_timestamp TEXT, order_delivered_customer_date TEXT, order_estimated_delivery_date TEXT)')
            data = [
                ('on_time','2018-01-02','2018-01-20','2018-01-20'),
                ('late','2018-01-03','2018-01-22','2018-01-20'),
                ('bad_actual','2018-01-04','not-a-date','2018-01-20'),
                ('bad_estimate','2018-01-05','2018-01-23','not-a-date'),
                ('null_actual','2018-01-06',None,'2018-01-20'),
            ]
            con.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?)',
                            [(name, 'cust', 'delivered', bought, delivered, estimated)
                             for name,bought,delivered,estimated in data])
            con.execute('CREATE TABLE order_items(order_id TEXT, order_item_id INTEGER, price REAL)')
            con.execute('CREATE TABLE order_payments(order_id TEXT, payment_sequential INTEGER, payment_value REAL)')
            con.executemany('INSERT INTO order_items VALUES (?,?,?)', [('on_time',1,10.0),('on_time',2,20.0)])
            con.executemany('INSERT INTO order_payments VALUES (?,?,?)', [('on_time',1,15.0),('on_time',2,15.0)])
            con.commit()
        allowed = {
            'orders': {'order_id','customer_id','order_status','order_purchase_timestamp','order_delivered_customer_date','order_estimated_delivery_date'},
            'order_items': {'order_id','order_item_id','price'},
            'order_payments': {'order_id','payment_sequential','payment_value'},
        }
        gold = _readonly_select(db, gold_sql, allowed)
        model = _readonly_select(db, model_sql, allowed)
        field_mapping = h06['field_mapping_gold_to_model']
        if len(gold) != 1 or len(model) != 1:
            raise ValueError('合成测试预期 January 恰好产生一行')
        gold_count = gold[0]['delivered_with_dates']
        model_count = model[0][field_mapping['delivered_with_dates']]
        gold_rate = gold[0]['late_rate_pct']
        model_rate = model[0][field_mapping['late_rate_pct']]
        date_divergence = gold_count != model_count or gold_rate != model_rate
        # Only two valid dates, including one late: expected 2 / 50%.
        if (gold_count, gold_rate) != (2, 50.0):
            raise ValueError(f'参考 SQL 在合成样本上不符合预期: {gold}')
        naive = _readonly_select(db, 'SELECT SUM(i.price) AS amount FROM order_items i JOIN order_payments p ON i.order_id = p.order_id', allowed)[0]['amount']
        safe = _readonly_select(db, 'SELECT SUM(price) AS amount FROM order_items', allowed)[0]['amount']
        # This is an example, not an LLM output from H01–H12.
        zero = _readonly_select(db, "SELECT 100.0 * SUM(CASE WHEN order_status = 'delivered' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS rate FROM orders WHERE order_purchase_timestamp >= '2030-01-01'", allowed)[0]['rate']
    return {
        'kind': 'synthetic_edge_probes_not_holdout_score', 'llm_calls': 0,
        'H06_malformed_nonnull_dates': {
            'gold': {'count': gold_count, 'late_rate_pct': gold_rate},
            'first_run_model': {'count': model_count, 'late_rate_pct': model_rate},
            'divergence': date_divergence,
            'reason': 'H06 model SQL only tests IS NOT NULL whereas Gold requires DATE(value) IS NOT NULL',
            'null_date_rows_excluded_by_both': True,
        },
        'join_fanout_synthetic': {'expected_item_sum': safe, 'naive_join_sum': naive,
                                  'inflation_factor': naive / safe if safe else None},
        'zero_denominator_synthetic': {'safe_nullif_rate': zero, 'returns_null': zero is None},
        'conclusion': 'These stress tests are illustrative reliability probes. They do not revise the frozen clean-data 8/8 audit.',
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Adversarial synthetic SQL boundary probes')
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--review-file', type=Path, default=DEFAULT_REVIEW)
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)
    try:
        result = probe(args.run_dir, args.review_file)
        args.out.mkdir(parents=True, exist_ok=True)
        destination = args.out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:6] + '.json')
        h.json_save(destination, result)
        print(json.dumps({**result, 'report': str(destination)}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, json.JSONDecodeError) as exc:
        p.exit(2, f'边界测试失败: {type(exc).__name__}: {exc}\n')

if __name__ == '__main__':
    raise SystemExit(main())
