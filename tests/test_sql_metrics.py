"""Tiny independent data fixture to test SQL definitions (not Olist benchmark values)."""
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SQLMetricTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
        self.conn.executemany('INSERT INTO customers(customer_id) VALUES (?)', [('c1',), ('c2',), ('c3',)])
        self.conn.executemany('INSERT INTO products(product_id, product_category_name) VALUES (?,?)',
                              [('p1', 'a'), ('p2', 'b')])
        self.conn.execute("INSERT INTO category_translation VALUES ('a', 'category_a')")
        self.conn.executemany('''INSERT INTO orders
            (order_id,customer_id,order_status,order_purchase_timestamp,
            order_delivered_customer_date,order_estimated_delivery_date)
            VALUES (?,?,?,?,?,?)''', [
            ('o1', 'c1', 'delivered', '2018-07-01 10:00:00', '2018-07-04 10:00:00', '2018-07-03 00:00:00'),
            ('o2', 'c2', 'delivered', '2018-07-02 10:00:00', '2018-07-04 10:00:00', '2018-07-05 00:00:00'),
            ('o3', 'c3', 'canceled', '2018-07-03 10:00:00', None, '2018-07-05 00:00:00'),
        ])
        self.conn.executemany('''INSERT INTO order_items
            (order_id, order_item_id, product_id, price, freight_value) VALUES (?,?,?,?,?)''', [
            ('o1', 1, 'p1', 10, 1), ('o1', 2, 'p1', 20, 2),
            ('o2', 1, 'p2', 50, 5), ('o3', 1, 'p1', 999, 9),
        ])
        self.conn.executemany('''INSERT INTO order_payments
            (order_id, payment_sequential, payment_type, payment_value) VALUES (?,?,?,?)''', [
            ('o1', 1, 'credit_card', 12), ('o1', 2, 'voucher', 21),
            ('o2', 1, 'credit_card', 55),
        ])

    def tearDown(self):
        self.conn.close()

    def run_sql(self, name):
        sql = (ROOT / 'sql' / 'queries' / name).read_text(encoding='utf-8')
        return self.conn.execute(sql).fetchall()

    def test_monthly_items_excludes_payment_fanout_and_canceled(self):
        rows = self.run_sql('02_monthly_delivered_items.sql')
        self.assertEqual(rows, [('2018-07', 2, 80.0, 8.0)])

    def test_late_rate_uses_order_grain(self):
        rows = self.run_sql('03_monthly_late_delivery.sql')
        self.assertEqual(rows, [('2018-07', 2, 1, 50.0)])

    def test_category_amounts(self):
        rows = self.run_sql('04_top_categories.sql')
        self.assertEqual(rows[0], ('b', 1, 1, 50.0))
        self.assertEqual(rows[1], ('category_a', 2, 1, 30.0))

    def test_known_join_fanout_is_bad(self):
        correct = self.conn.execute("SELECT SUM(price) FROM order_items WHERE order_id='o1'").fetchone()[0]
        incorrect = self.conn.execute('''SELECT SUM(i.price) FROM order_items i JOIN order_payments p
            ON i.order_id=p.order_id WHERE i.order_id='o1' ''').fetchone()[0]
        self.assertEqual(correct, 30)
        self.assertEqual(incorrect, 60)


if __name__ == '__main__':
    unittest.main()
