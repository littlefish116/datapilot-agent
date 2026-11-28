"""Offline tests for Stage 2C scoped recovery trial and advisory checks.

Pure unit tests use a stand-in validator/executor because the real policy
layer (SQLGlot + authorizer) was already tested in Stage 2A. Real-policy CLI
validation must still run locally on the user's SQLite environment.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.v2_semantic_risk_guard import inspect_semantic_risks
from evaluation.v2_hard_fixture import create_hard_fixture
from evaluation.v2_stage2c_diagnostics import GOLD_X03, GOLD_X08, SUITE, sha256_file
from evaluation import v2_stage2c_recovery_trial as r

ORIGINAL_X08 = '''WITH item_agg AS (SELECT order_id, SUM(price) AS item_value FROM order_items GROUP BY order_id), pay_agg AS (SELECT order_id, SUM(payment_value) AS pay_value FROM order_payments GROUP BY order_id), base AS (SELECT o.order_id, strftime('%Y-%m', o.order_purchase_timestamp) AS ym, i.item_value, p.pay_value FROM orders o JOIN item_agg i ON o.order_id = i.order_id JOIN pay_agg p ON o.order_id = p.order_id WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp IS NOT NULL AND strftime('%Y-%m', o.order_purchase_timestamp) IN ('2018-04','2018-05','2018-06')) SELECT ym AS month, COUNT(DISTINCT order_id) AS delivered_orders, ROUND(AVG(item_value), 2) AS avg_item_value_brl, ROUND(AVG(pay_value), 2) AS avg_payment_value_brl FROM base GROUP BY ym ORDER BY ym ASC'''


def raw_sha(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


class SemanticRiskTests(unittest.TestCase):
    @staticmethod
    def _scan(sql):
        return inspect_semantic_risks(sql, validator=lambda x: x)

    def test_flags_raw_nonnull_dates_in_x03(self):
        sql = "SELECT COUNT(*) FROM orders o WHERE o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL"
        result = self._scan(sql)
        self.assertEqual(result['disposition'], 'needs_review')
        self.assertEqual(result['risks'][0]['code'], 'DATE_VALIDITY_GAP')
        self.assertEqual(len(result['risks'][0]['columns']), 2)

    def test_flags_normalized_not_is_null(self):
        result = self._scan('SELECT COUNT(*) FROM orders WHERE NOT order_delivered_customer_date IS NULL')
        self.assertEqual(result['risks'][0]['code'], 'DATE_VALIDITY_GAP')

    def test_gold_date_validation_not_flagged(self):
        result = self._scan(GOLD_X03.read_text(encoding='utf-8'))
        self.assertEqual(result['risk_count'], 0)
        self.assertFalse(result['semantics_proven'])

    def test_warns_raw_double_detail_aggregation(self):
        sql = 'SELECT SUM(oi.price) FROM orders o JOIN order_items oi ON o.order_id=oi.order_id JOIN order_payments p ON o.order_id=p.order_id'
        self.assertIn('POTENTIAL_JOIN_FANOUT', [e['code'] for e in self._scan(sql)['risks']])

    def test_valid_sql_is_not_proven_correct(self):
        result = self._scan('SELECT COUNT(*) FROM orders')
        self.assertEqual(result['disposition'], 'no_known_risk')
        self.assertFalse(result['semantics_proven'])

    def test_invalid_sql_fails_closed(self):
        def reject(_): raise ValueError('unsafe')
        self.assertEqual(inspect_semantic_risks('DELETE FROM orders', validator=reject)['status'], 'invalid')


class RecoveryTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stage2c_recovery_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        (self.run / 'responses').mkdir(parents=True)
        for d in ['gold_real', 'gold_edge']:
            (self.run / d).mkdir()
        self.real_db = create_hard_fixture(self.root / 'real.db')
        self.edge_db = create_hard_fixture(self.run / 'synthetic_fixture.db')
        response = {
            'status': 'error', 'generated_sql': ORIGINAL_X08,
            'execution': {'status': 'error', 'error': {'code': 'query_timeout'}}
        }
        response_path = self.run / 'responses/X08.json'
        response_path.write_text(json.dumps(response), encoding='utf-8')
        (self.run / 'response_sha256.json').write_text(json.dumps({'X08': sha256_file(response_path)}), encoding='utf-8')
        manifest = {
            'mode': 'live',
            'hashes': {
                'suite_sha256': sha256_file(SUITE),
                'gold_sql_sha256': {'X08': sha256_file(GOLD_X08)},
            },
            'db_sha256': sha256_file(self.real_db),
            'fixture_sha256': sha256_file(self.edge_db),
        }
        (self.run / 'run_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.candidate_sql = r.FILTER_FIRST.read_text(encoding='utf-8')
        self.normalizer = lambda s: s
        for dataset, db in [('real', self.real_db), ('edge', self.edge_db)]:
            with closing(sqlite3.connect(db)) as conn:
                conn.row_factory = sqlite3.Row
                rows = [dict(x) for x in conn.execute(self.candidate_sql).fetchall()]
            data = {'status': 'ok', 'rows': rows}
            (self.run / f'gold_{dataset}/X08.json').write_text(json.dumps(data), encoding='utf-8')
        self.expected_patch = patch.multiple(
            r,
            PINNED_FIRST_SQL_SHA256=raw_sha(ORIGINAL_X08),
            PINNED_CANDIDATE_SHA256=raw_sha(self.candidate_sql),
            PINNED_RESPONSE_SHA256=sha256_file(response_path),
            PINNED_SUITE_SHA256=manifest['hashes']['suite_sha256'],
            PINNED_DB_SHA256=manifest['db_sha256'],
            PINNED_FIXTURE_SHA256=manifest['fixture_sha256'],
            PINNED_GOLD_REAL_SHA256=sha256_file(self.run / 'gold_real/X08.json'),
            PINNED_GOLD_EDGE_SHA256=sha256_file(self.run / 'gold_edge/X08.json'),
        )
        self.expected_patch.start()
        self.addCleanup(self.expected_patch.stop)

    def _inputs(self):
        return r.verified_recovery_inputs(self.run, self.real_db, validator=self.normalizer)

    @staticmethod
    def _fake_executor(sql, db):
        with closing(sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(x) for x in conn.execute(sql).fetchall()]
        return {'status': 'ok', 'rows': rows, 'returned_rows': len(rows), 'truncated': False, 'duration_ms': 1}

    def test_verified_inputs_accept_original_frozen_case(self):
        data = self._inputs()
        self.assertEqual(data['original_error_code'], 'query_timeout')

    def test_recovery_candidate_matches_both_on_fixture(self):
        result = r.trial_verified_candidate(self.run, self.real_db, executor=self._fake_executor, validator=self.normalizer)
        self.assertTrue(result['candidate_passed_both'])
        self.assertEqual(result['llm_calls'], 0)
        self.assertFalse(result['automatic_sql_rewriting'])

    def test_error_code_must_be_query_timeout(self):
        p = self.run / 'responses/X08.json'
        obj = json.loads(p.read_text())
        obj['execution']['error']['code'] = 'unsafe_sql'
        p.write_text(json.dumps(obj))
        (self.run / 'response_sha256.json').write_text(json.dumps({'X08': sha256_file(p)}))
        with patch.object(r, 'PINNED_RESPONSE_SHA256', sha256_file(p)):
            with self.assertRaisesRegex(ValueError, 'query_timeout'):
                self._inputs()

    def test_tampered_original_response_rejected(self):
        p = self.run / 'responses/X08.json'
        obj = json.loads(p.read_text())
        obj['generated_sql'] += ' -- tampered'
        p.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self._inputs()

    def test_changed_database_rejected(self):
        with closing(sqlite3.connect(self.real_db)) as conn:
            conn.execute("INSERT INTO customers VALUES ('new','other','SP')")
            conn.commit()
        with self.assertRaisesRegex(ValueError, 'database hash differs'):
            self._inputs()

    def test_changed_candidate_digest_rejected(self):
        with patch.object(r, 'PINNED_CANDIDATE_SHA256', 'deadbeef'):
            with self.assertRaisesRegex(ValueError, 'Candidate SQL differs'):
                self._inputs()

    def test_candidate_path_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, 'Only the reviewed'):
            r.verified_recovery_inputs(self.run, self.real_db, candidate_path=self.root/'other.sql', validator=self.normalizer)

    def test_different_first_sql_is_not_recoverable(self):
        p = self.run / 'responses/X08.json'
        obj = json.loads(p.read_text())
        obj['generated_sql'] = 'SELECT COUNT(*) FROM orders'
        p.write_text(json.dumps(obj))
        (self.run / 'response_sha256.json').write_text(json.dumps({'X08': sha256_file(p)}))
        with patch.object(r, 'PINNED_RESPONSE_SHA256', sha256_file(p)):
            with self.assertRaisesRegex(ValueError, 'Unrecognized first-run SQL'):
                self._inputs()

    def test_executor_failure_remains_failure(self):
        fail = lambda sql, db: {'status':'error', 'error':{'code':'query_timeout'}}
        result = r.trial_verified_candidate(self.run, self.real_db, executor=fail, validator=self.normalizer)
        self.assertFalse(result['candidate_passed_both'])
        self.assertEqual(result['trials']['real']['status'], 'execution_failed')

    def test_wrong_result_cannot_pass(self):
        def wrong(sql, db):
            v = self._fake_executor(sql, db)
            v['rows'][0]['avg_item_amount_brl'] += 1
            return v
        result = r.trial_verified_candidate(self.run, self.real_db, executor=wrong, validator=self.normalizer)
        self.assertFalse(result['candidate_passed_both'])


if __name__ == '__main__':
    unittest.main()
