"""Check imported counts, referential integrity and join fan-out."""
import json
import sqlite3

from config import DB_FILE, FILES, REPORT_DIR


def one(conn, query, args=()):
    return conn.execute(query, args).fetchone()[0]


def main() -> None:
    if not DB_FILE.exists():
        raise FileNotFoundError(f'Run 02_load_sqlite.py first: {DB_FILE}')
    with sqlite3.connect(DB_FILE) as conn:
        checks = {
            'row_counts': {t: one(conn, f'SELECT COUNT(*) FROM "{t}"') for t in FILES},
            'foreign_key_violations': len(conn.execute('PRAGMA foreign_key_check').fetchall()),
            'repeated_review_id_extra_rows': one(conn, '''
                SELECT COUNT(*) - COUNT(DISTINCT review_id) FROM order_reviews
                WHERE review_id IS NOT NULL'''),
            'distinct_geo_zip_prefixes': one(conn, '''
                SELECT COUNT(DISTINCT geolocation_zip_code_prefix) FROM geolocation'''),
            'orders_with_multiple_items': one(conn, '''
                SELECT COUNT(*) FROM (SELECT order_id FROM order_items
                GROUP BY order_id HAVING COUNT(*) > 1)'''),
            'orders_with_multiple_payments': one(conn, '''
                SELECT COUNT(*) FROM (SELECT order_id FROM order_payments
                GROUP BY order_id HAVING COUNT(*) > 1)'''),
            'delivered_missing_actual_date': one(conn, '''
                SELECT COUNT(*) FROM orders WHERE order_status = 'delivered'
                AND order_delivered_customer_date IS NULL'''),
            'products_missing_category': one(conn, '''
                SELECT COUNT(*) FROM products WHERE product_category_name IS NULL'''),
        }
        # Find an actual order where a naive items x payments JOIN multiplies rows.
        sample = conn.execute('''
            SELECT i.order_id FROM order_items AS i
            JOIN order_payments AS p ON i.order_id = p.order_id
            GROUP BY i.order_id
            HAVING COUNT(DISTINCT i.order_item_id) > 1
               AND COUNT(DISTINCT p.payment_sequential) > 1
            LIMIT 1
        ''').fetchone()
        if sample:
            order_id = sample[0]
            true_items = one(conn, 'SELECT SUM(price) FROM order_items WHERE order_id=?', (order_id,))
            wrong_items = one(conn, '''
                SELECT SUM(i.price) FROM order_items i
                JOIN order_payments p ON i.order_id=p.order_id
                WHERE i.order_id=?''', (order_id,))
            checks['fanout_example'] = {
                'order_id': order_id,
                'correct_item_value': round(true_items, 2),
                'incorrect_after_join': round(wrong_items, 2),
                'inflation_factor': round(wrong_items / true_items, 2) if true_items else None,
            }
        else:
            checks['fanout_example'] = None

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = REPORT_DIR / 'db_quality.json'
    dest.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(f'\nSaved {dest}')


if __name__ == '__main__':
    main()
