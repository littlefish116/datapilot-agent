"""V2 SQL policy tests. Uses a synthetic SQLite DB; no Olist data or API key."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.v2_safe_sql import execute_safe_sql, validate_sql, SqlPolicyError
from app.v2_sql_policy import SqlLimits


class SafeSqlTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "toy.db"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                CREATE TABLE orders (
                  order_id TEXT PRIMARY KEY, customer_id TEXT,
                  order_status TEXT, order_purchase_timestamp TEXT,
                  order_delivered_customer_date TEXT,
                  order_estimated_delivery_date TEXT
                );
                CREATE TABLE customers (
                  customer_id TEXT PRIMARY KEY,
                  customer_unique_id TEXT, customer_state TEXT
                );
                CREATE TABLE order_items (
                  order_id TEXT, order_item_id INTEGER, product_id TEXT,
                  seller_id TEXT, price REAL, freight_value REAL
                );
                CREATE TABLE order_payments (
                  order_id TEXT, payment_sequential INTEGER,
                  payment_type TEXT, payment_value REAL
                );
                CREATE TABLE order_reviews (
                  review_id TEXT, order_id TEXT, review_comment_message TEXT
                );
                INSERT INTO customers VALUES
                  ('c1', 'buyer1', 'SP'), ('c2', 'buyer2', 'RJ'),
                  ('c3', 'buyer1', 'SP');
                INSERT INTO orders VALUES
                  ('o1', 'c1', 'delivered', '2018-03-01', '2018-03-05', '2018-03-04'),
                  ('o2', 'c2', 'delivered', '2018-03-02', '2018-03-03', '2018-03-04'),
                  ('o3', 'c3', 'canceled', '2018-03-03', NULL, NULL);
                INSERT INTO order_items VALUES
                  ('o1', 1, 'p1', 's1', 10, 1),
                  ('o1', 2, 'p1', 's1', 20, 2),
                  ('o2', 1, 'p2', 's2', 50, 5);
                INSERT INTO order_payments VALUES
                  ('o1', 1, 'credit_card', 12),
                  ('o1', 2, 'voucher', 21),
                  ('o2', 1, 'credit_card', 55);
                INSERT INTO order_reviews VALUES ('r1','o1','private feedback');
            """)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_sql(self, sql, limits=None):
        return execute_safe_sql(sql, self.db, limits)

    def test_aggregate_join(self):
        result = self.run_sql("""
            SELECT c.customer_state, COUNT(DISTINCT o.order_id) AS orders_count
            FROM orders o JOIN customers c ON o.customer_id=c.customer_id
            WHERE o.order_status='delivered'
            GROUP BY c.customer_state ORDER BY orders_count DESC
        """)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual({r['customer_state']: r['orders_count'] for r in result['rows']}, {'SP': 1, 'RJ': 1})

    def test_cte_order_grain(self):
        result = self.run_sql("""
            WITH per_order AS (
              SELECT o.order_id, SUM(i.freight_value) AS freight
              FROM orders o JOIN order_items i ON i.order_id=o.order_id
              WHERE o.order_status='delivered' GROUP BY o.order_id
            )
            SELECT ROUND(AVG(freight),2) AS mean_freight FROM per_order
        """)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['rows'][0]['mean_freight'], 4.0)

    def test_small_result_limit(self):
        result = self.run_sql(
            'SELECT payment_type, COUNT(*) AS n FROM order_payments GROUP BY payment_type',
            SqlLimits(max_rows=1),
        )
        self.assertEqual(result['status'], 'ok', result)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['returned_rows'], 1)

    def test_reject_multiple_and_write(self):
        for q in [
            'SELECT COUNT(*) FROM orders; DROP TABLE orders;',
            "DELETE FROM orders WHERE 1=1",
            'UPDATE orders SET order_status=\'done\'',
            'PRAGMA table_info(orders)',
            'WITH x AS (DELETE FROM orders) SELECT * FROM x',
        ]:
            with self.subTest(sql=q):
                self.assertNotEqual(self.run_sql(q)['status'], 'ok')
        self.assertEqual(self.run_sql('SELECT COUNT(*) AS n FROM orders')['rows'][0]['n'], 3)

    def test_reject_unauthorized_table_and_column(self):
        for q in [
            'SELECT COUNT(*) FROM sqlite_master',
            'SELECT COUNT(*) FROM order_reviews',
            'SELECT COUNT(review_comment_message) FROM orders',
            'SELECT customer_zip_code_prefix, COUNT(*) FROM customers GROUP BY customer_zip_code_prefix',
            'SELECT COUNT(*) FROM other_database.orders',
        ]:
            with self.subTest(sql=q):
                self.assertNotEqual(self.run_sql(q)['status'], 'ok')

    def test_reject_raw_ids_and_stars(self):
        for q in [
            'SELECT * FROM orders',
            'SELECT customer_unique_id FROM customers',
            'WITH x AS (SELECT customer_unique_id AS secret FROM customers) SELECT secret FROM x',
        ]:
            with self.subTest(sql=q):
                self.assertNotEqual(self.run_sql(q)['status'], 'ok')

    def test_reject_unsafe_functions(self):
        for q in [
            'SELECT RANDOMBLOB(1000) AS blob FROM orders',
            'SELECT GROUP_CONCAT(customer_unique_id) FROM customers',
        ]:
            with self.subTest(sql=q):
                self.assertNotEqual(self.run_sql(q)['status'], 'ok')

    def test_scalar_subquery_join_keys_not_mistaken_for_output_identifiers(self):
        # Regression for the actual D01 SQL generated by DeepSeek.
        # The inner WHERE/JOIN compares order IDs but projects only SUM(amount).
        sql = """
            SELECT p.payment_type,
                   ROUND(SUM(p.payment_value), 2) AS payment_amount_brl,
                   ROUND(SUM(p.payment_value) * 100.0 /
                     (SELECT SUM(p2.payment_value)
                      FROM order_payments p2
                      JOIN orders o2 ON o2.order_id = p2.order_id
                      WHERE o2.order_status = 'delivered'
                        AND strftime('%Y-%m', o2.order_purchase_timestamp) = '2018-03'),
                   2) AS share_pct
            FROM order_payments p
            JOIN orders o ON o.order_id = p.order_id
            WHERE o.order_status = 'delivered'
              AND strftime('%Y-%m', o.order_purchase_timestamp) = '2018-03'
            GROUP BY p.payment_type
            ORDER BY payment_amount_brl DESC
        """
        result = self.run_sql(sql)
        self.assertEqual(result["status"], "ok", result)
        by_type = {r["payment_type"]: r for r in result["rows"]}
        self.assertEqual(by_type["credit_card"]["payment_amount_brl"], 67.0)
        self.assertEqual(by_type["credit_card"]["share_pct"], 76.14)
        self.assertEqual(by_type["voucher"]["payment_amount_brl"], 21.0)
        self.assertEqual(by_type["voucher"]["share_pct"], 23.86)

    def test_nested_raw_identifiers_still_denied(self):
        # The fix must not allow raw key leakage through scalar subqueries,
        # CTE aliases or simple transformations of identifiers.
        examples = [
            "SELECT (SELECT order_id FROM orders LIMIT 1) AS leaked FROM orders LIMIT 1",
            "SELECT (SELECT SUBSTR(customer_unique_id, 1, 3) FROM customers LIMIT 1) AS leaked FROM orders LIMIT 1",
            "WITH x AS (SELECT order_id AS secret FROM orders) SELECT secret FROM x",
            "SELECT order_id || '-suffix' AS leaked FROM orders",
        ]
        for sql in examples:
            with self.subTest(sql=sql):
                result = self.run_sql(sql)
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(result["error"]["code"], "unsafe_sql", result)

    def test_missing_database(self):
        r = execute_safe_sql('SELECT COUNT(*) FROM orders', self.db.parent / 'missing.db')
        self.assertEqual(r['error']['code'], 'missing_database')

    def test_vm_budget_interrupt(self):
        r = self.run_sql('SELECT COUNT(*) AS n FROM orders a, orders b, orders c, orders d, orders e, orders f, orders g, orders h',
                         SqlLimits(max_vm_steps=1000, vm_check_every=100))
        self.assertEqual(r['error']['code'], 'query_timeout', r)

    def test_unparseable_sql(self):
        r = self.run_sql('SELECT ( FROM orders')
        self.assertEqual(r['status'], 'error')
        self.assertIn(r['error']['code'], {'syntax_error', 'unsafe_sql'})

    def test_output_bytes_limit(self):
        r = self.run_sql('SELECT customer_state, COUNT(*) AS n FROM customers GROUP BY customer_state',
                         SqlLimits(max_result_bytes=4))
        self.assertEqual(r['error']['code'], 'result_too_large')
        self.assertEqual(r['rows'], [])


if __name__ == '__main__':
    unittest.main()
