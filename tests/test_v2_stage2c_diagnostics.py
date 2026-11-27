"""Stage 2C offline diagnostic tests; no DeepSeek and no Olist DB required."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evaluation import v2_stage2c_diagnostics as d
from evaluation.v2_hard_fixture import create_hard_fixture


def local_authorizer(action, a, b, _db, _src):
    """Unit-test-only authorizer; production path uses Stage 2A's authorizer."""
    if action in (sqlite3.SQLITE_SELECT,):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and (a or '') in {'orders', 'order_items', 'order_payments'}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (b or a or '').lower() in {'count', 'sum', 'avg', 'round', 'strftime', 'date', 'nullif'}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def simple_validator(sql, limits):
    if len(sql) > limits.max_sql_chars:
        raise ValueError('too large')
    return sql.strip()


class Stage2CDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = create_hard_fixture(Path(self.temp.name) / 'fixture.db')

    def measure(self, sql, *, vm=5_000_000, secs=3):
        return d.diagnose_query(sql, self.db, vm_budget=vm, timeout_s=secs,
                                validator=simple_validator, authorizer=local_authorizer)

    def test_readonly_scalar(self):
        out = self.measure('SELECT COUNT(*) AS n FROM orders')
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['rows'][0]['n'], 18)
        self.assertGreater(len(out['plan']), 0)

    def test_plan_has_sqlite_details(self):
        out = self.measure('SELECT COUNT(*) AS n FROM orders')
        self.assertTrue(all('detail' in v for v in out['plan']))

    def test_vm_budget_can_interrupt(self):
        out = self.measure('SELECT COUNT(*) AS n FROM orders a, orders b, orders c', vm=1000)
        self.assertEqual(out['status'], 'error')
        self.assertEqual(out['interrupt_reason'], 'vm_step_budget')
        self.assertGreaterEqual(out['vm_steps_lower_bound'], 1000)

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.measure('SELECT COUNT(*) FROM orders', vm=0)

    def test_sqlite_connection_is_closed(self):
        path = Path(self.temp.name) / 'other.db'
        with closing(sqlite3.connect(path)) as con:
            con.execute('CREATE TABLE orders(order_id TEXT)')
        d.diagnose_query('SELECT COUNT(*) AS n FROM orders', path,
                         validator=simple_validator, authorizer=local_authorizer)
        path.unlink()
        self.assertFalse(path.exists())

    def test_x03_adversarial_fixture_cases(self):
        p = Path(self.temp.name) / 'x03.db'
        d.x03_adversarial_fixture(p)
        with closing(sqlite3.connect(p)) as c:
            gold = c.execute('''SELECT COUNT(*) FROM orders
               WHERE order_purchase_timestamp BETWEEN '2019-12-01' AND '2019-12-31'
               AND DATE(order_delivered_customer_date) IS NOT NULL
               AND DATE(order_estimated_delivery_date) IS NOT NULL''').fetchone()[0]
            naive = c.execute('''SELECT COUNT(*) FROM orders
               WHERE order_purchase_timestamp BETWEEN '2019-12-01' AND '2019-12-31'
               AND order_delivered_customer_date IS NOT NULL
               AND order_estimated_delivery_date IS NOT NULL''').fetchone()[0]
        self.assertEqual((gold, naive), (2, 4))

    def test_x03_fixture_wont_overwrite(self):
        with self.assertRaises(FileExistsError):
            d.x03_adversarial_fixture(self.db)

    def test_x08_field_alias_mapping(self):
        model = [{'month': '2018-04', 'delivered_orders': 3,
                  'avg_item_value_brl': 31.67, 'avg_payment_value_brl': 36.67}]
        gold = [{'purchase_month': '2018-04', 'eligible_orders': 3,
                 'avg_item_amount_brl': 31.67, 'avg_payment_amount_brl': 36.67}]
        self.assertTrue(d.compare_x08(model, 'model', gold)['match'])

    def test_x08_count_mismatch(self):
        model = [{'month': '2018-04', 'delivered_orders': 2,
                  'avg_item_value_brl': 31.67, 'avg_payment_value_brl': 36.67}]
        gold = [{'purchase_month': '2018-04', 'eligible_orders': 3,
                 'avg_item_amount_brl': 31.67, 'avg_payment_amount_brl': 36.67}]
        self.assertEqual(d.compare_x08(model, 'model', gold)['reason'], 'count_mismatch')

    def test_x08_month_order_mismatch(self):
        gold = [{'purchase_month': '2018-04', 'eligible_orders': 3,
                 'avg_item_amount_brl': 31.67, 'avg_payment_amount_brl': 36.67}]
        model = [{'month': '2018-05', 'delivered_orders': 3,
                  'avg_item_value_brl': 31.67, 'avg_payment_value_brl': 36.67}]
        self.assertFalse(d.compare_x08(model, 'model', gold)['match'])

    def test_verified_response_rejects_tampering(self):
        rp = Path(self.temp.name) / 'run'
        (rp / 'responses').mkdir(parents=True)
        suite = Path(self.temp.name) / 'suite.json'; suite.write_text('{}')
        gold = Path(self.temp.name) / 'gold.sql'; gold.write_text('SELECT 1')
        (rp / 'run_manifest.json').write_text(json.dumps({'mode':'live', 'hashes':{
            'suite_sha256':d.sha256_file(suite),
            'gold_sql_sha256':{'X08':d.sha256_file(gold)}
        }}))
        response = rp / 'responses/X08.json'
        response.write_text('{"generated_sql":"SELECT 1"}')
        (rp / 'response_sha256.json').write_text(json.dumps({'X08':d.sha256_file(response)}))
        with patch.object(d, 'SUITE', suite), patch.object(d, 'GOLD_X08', gold):
            self.assertEqual(d.verified_first_sql(rp, 'X08'), 'SELECT 1')
            response.write_text('{"generated_sql":"SELECT 2"}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                d.verified_first_sql(rp, 'X08')

    def test_authorizer_denies_delete_in_profile(self):
        result = self.measure('DELETE FROM orders')
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['error_type'].startswith('plan_error:'))

    def test_readonly_denies_write(self):
        with self.assertRaises(sqlite3.DatabaseError):
            with closing(sqlite3.connect(self.db.resolve().as_uri() + '?mode=ro', uri=True)) as c:
                c.execute('DELETE FROM orders')

    def test_candidate_returns_gold_on_synthetic(self):
        sql = d.FILTER_FIRST.read_text(encoding='utf-8')
        out = self.measure(sql)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual([r['purchase_month'] for r in out['rows']], ['2018-04','2018-05','2018-06'])
        self.assertEqual([r['eligible_orders'] for r in out['rows']], [3,3,2])


if __name__ == '__main__':
    unittest.main()
