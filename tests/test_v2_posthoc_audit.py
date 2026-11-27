"""Pure offline tests; do not require a particular user's archived first-run files."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing
import unittest

from evaluation import v2_holdout_benchmark as h
from evaluation import v2_holdout_posthoc_audit as a
from evaluation import v2_boundary_probe as b


class PosthocAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c['id']: c for c in h.load_suite()}
        cls.review = json.loads(a.DEFAULT_REVIEW.read_text(encoding='utf-8'))

    def fabricated_pair(self, cid, gold_rows):
        case = self.cases[cid]
        mapping = self.review['items'][cid]['field_mapping_gold_to_model']
        gold_cols = [f['gold'] for f in case['fields']]
        observed_rows = [{mapping[g]: row[g] for g in gold_cols} for row in gold_rows]
        return (case,
                {'status': 'ok', 'generated_sql': 'SELECT 1', 'execution': {'status': 'ok',
                 'columns': [mapping[k] for k in gold_cols], 'rows': observed_rows,
                 'returned_rows': len(observed_rows), 'truncated': False}},
                {'status': 'ok', 'columns': gold_cols, 'rows': gold_rows}, mapping)

    def test_pinned_source_and_posthoc_provenance(self):
        self.assertEqual(self.review['status'], 'posthoc-not-a-frozen-first-attempt-metric')
        self.assertEqual(h.sha256(h.SUITE), self.review['suite_sha256'])
        self.assertEqual(len(self.review['items']), 12)

    def test_relabel_scalar_h03(self):
        args = self.fabricated_pair('H03', [{'avg_item_value_per_order_brl': 144.84}])
        self.assertEqual(a.compare_documented_mapping(*args)['verdict'], 'pass')

    def test_relabel_scalar_h05(self):
        args = self.fabricated_pair('H05', [{'avg_payments_per_order': 1.0412}])
        self.assertEqual(a.compare_documented_mapping(*args)['verdict'], 'pass')

    def test_relabel_h06_all_months(self):
        rows = [{'purchase_month': '2018-01', 'delivered_with_dates': 100, 'late_rate_pct': 5.0},
                {'purchase_month': '2018-02', 'delivered_with_dates': 200, 'late_rate_pct': 7.0}]
        args = self.fabricated_pair('H06', rows)
        self.assertEqual(a.compare_documented_mapping(*args)['verdict'], 'pass')

    def test_h06_missing_or_reordered_months_fail(self):
        rows = [{'purchase_month': '2018-01', 'delivered_with_dates': 100, 'late_rate_pct': 5.0},
                {'purchase_month': '2018-02', 'delivered_with_dates': 200, 'late_rate_pct': 7.0}]
        case,res,gold,mapping = self.fabricated_pair('H06', rows)
        res['execution']['rows'].reverse()
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'month_order_mismatch')
        res['execution']['rows'].pop()
        res['execution']['returned_rows'] -= 1
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'invalid_or_incomplete_rows')

    def test_relabel_h07_group_and_amount(self):
        args = self.fabricated_pair('H07', [
            {'category_english':'watches_gifts','item_value_brl':293015.62},
            {'category_english':'health_beauty','item_value_brl':292338.55}])
        self.assertEqual(a.compare_documented_mapping(*args)['verdict'], 'pass')

    def test_wrong_numeric_value_never_passing_even_if_alias_known(self):
        case,res,gold,mapping = self.fabricated_pair('H03', [{'avg_item_value_per_order_brl':144.84}])
        res['execution']['rows'][0]['avg_item_value_brl'] = 45.0
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'value_mismatch')

    def test_unreviewed_mapping_cannot_automatically_pass(self):
        case,res,gold,mapping = self.fabricated_pair('H03', [{'avg_item_value_per_order_brl':144.84}])
        self.assertEqual(a.compare_documented_mapping(case,res,gold,{'avg_item_value_per_order_brl':'random'})['reason'], 'missing_or_duplicate_columns')

    def test_wrong_ranking_fails_even_if_row_set_matches(self):
        case,res,gold,mapping = self.fabricated_pair('H07', [
            {'category_english':'watches_gifts','item_value_brl':300.0},
            {'category_english':'health_beauty','item_value_brl':200.0}])
        res['execution']['rows'].reverse()
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'ranking_order_mismatch')

    def test_duplicate_keys_fail(self):
        case,res,gold,mapping = self.fabricated_pair('H07', [
            {'category_english':'a','item_value_brl':300.0},
            {'category_english':'b','item_value_brl':200.0}])
        res['execution']['rows'][1]['category_en'] = 'a'
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'group_keys_mismatch')

    def test_truncation_fails(self):
        case,res,gold,mapping = self.fabricated_pair('H03', [{'avg_item_value_per_order_brl':144.84}])
        res['execution']['truncated'] = True
        self.assertEqual(a.compare_documented_mapping(case,res,gold,mapping)['reason'], 'execution_not_ok_or_truncated')

    def test_hash_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'response.json'
            path.write_text('{"value":1}', encoding='utf-8')
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(a._read_verified(path, expected)['value'], 1)
            path.write_text('{"value":2}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '哈希不一致'):
                a._read_verified(path, expected)

    def test_synthetic_readonly_policy_denies_write(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'temp.db'
            with closing(sqlite3.connect(db)) as con:
                con.execute('CREATE TABLE orders(order_id TEXT)')
                con.execute("INSERT INTO orders VALUES('a')")
                con.commit()
            read = b._readonly_select(db, 'SELECT COUNT(*) AS n FROM orders', {'orders': {'order_id'}})
            self.assertEqual(read[0]['n'], 1)
            with self.assertRaises(sqlite3.DatabaseError):
                b._readonly_select(db, "DELETE FROM orders", {'orders': {'order_id'}})
            with self.assertRaises(sqlite3.DatabaseError):
                b._readonly_select(db, 'SELECT name FROM sqlite_master', {'orders': {'order_id'}})
            self.assertTrue(db.exists())

    def test_malformed_dates_and_division_null(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'temp.db'
            with closing(sqlite3.connect(db)) as con:
                con.execute('CREATE TABLE orders(order_delivered_customer_date TEXT)')
                con.executemany('INSERT INTO orders VALUES (?)', [('not-a-date',), (None,), ('2018-01-20',)])
                con.commit()
            # NULL is not counted by COUNT(column), malformed nonempty strings are.
            rows = b._readonly_select(db, 'SELECT COUNT(order_delivered_customer_date) AS nonnull, COUNT(DATE(order_delivered_customer_date)) AS valid FROM orders',
                                      {'orders': {'order_delivered_customer_date'}})
            self.assertEqual((rows[0]['nonnull'], rows[0]['valid']), (2, 1))
            safe = b._readonly_select(db, "SELECT 100.0 / NULLIF(COUNT(*), 0) AS rate FROM orders WHERE DATE(order_delivered_customer_date)='2022-01-01'", {'orders': {'order_delivered_customer_date'}})
            self.assertIsNone(safe[0]['rate'])


if __name__ == '__main__':
    unittest.main()
