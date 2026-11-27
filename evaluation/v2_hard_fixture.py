"""Deterministic synthetic Olist-shaped boundary fixture for Hard8.

Contains intentionally malformed non-NULL date strings, multiple order items,
multiple payment rows, repeat buyer identities, and no 2019-12 orders.
All records are synthetic; never copy customer-level production rows here.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT, order_status TEXT,
    order_purchase_timestamp TEXT, order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT);
CREATE TABLE customers(customer_id TEXT PRIMARY KEY, customer_unique_id TEXT, customer_state TEXT);
CREATE TABLE order_items(order_id TEXT, order_item_id INTEGER, product_id TEXT,
    seller_id TEXT, price REAL, freight_value REAL);
CREATE TABLE order_payments(order_id TEXT, payment_sequential INTEGER,
    payment_type TEXT, payment_value REAL);
CREATE TABLE products(product_id TEXT PRIMARY KEY, product_category_name TEXT);
CREATE TABLE sellers(seller_id TEXT PRIMARY KEY, seller_state TEXT);
CREATE TABLE category_translation(product_category_name TEXT,
    product_category_name_english TEXT);
"""

# All prices/payments in BRL; rows have deliberately distinct amounts.
ORDERS = [
    # Q1 delivered repeat buyers: u1 in three orders, u2 in two; malformed dates
    # still count for repeat-buying, but are excluded from X01 late-rate.
    ("jan1", "c1", "delivered", "2018-01-10", "2018-01-18", "2018-01-20"),
    ("jan2", "c2", "delivered", "2018-01-12", "2018-01-20", "2018-01-19"),
    ("feb1", "c3", "delivered", "2018-02-10", "2018-02-19", "2018-02-20"),
    ("m1", "c1", "delivered", "2018-03-10", "2018-03-18", "2018-03-20"),
    ("m2", "c2", "delivered", "2018-03-11", "2018-03-25", "2018-03-20"),
    ("m3", "c1", "delivered", "2018-03-12", "invalid-delivered", "2018-03-20"),
    ("m4", "c1", "delivered", "2018-03-13", "2018-03-22", "invalid-estimated"),
    ("m5", "c4", "delivered", "2018-03-14", None, "2018-03-20"),
    ("m6", "c2", "canceled", "2018-03-15", "2018-03-25", "2018-03-20"),
    # Q2: each order has at least one item and one payment, except a canceled order.
    ("a01", "c1", "delivered", "2018-04-02", "2018-04-15", "2018-04-14"),
    ("a02", "c2", "delivered", "2018-04-07", "2018-04-14", "2018-04-20"),
    ("a03", "c3", "delivered", "2018-04-08", "2018-04-15", "2018-04-20"),
    ("a04", "c1", "canceled", "2018-04-09", "2018-04-15", "2018-04-20"),
    ("y01", "c2", "delivered", "2018-05-02", "2018-05-12", "2018-05-13"),
    ("y02", "c1", "delivered", "2018-05-03", "2018-05-14", "2018-05-13"),
    ("y03", "c3", "delivered", "2018-05-04", "2018-05-14", "2018-05-15"),
    ("j01", "c4", "delivered", "2018-06-02", "2018-06-12", "2018-06-11"),
    ("j02", "c3", "delivered", "2018-06-04", "2018-06-14", "2018-06-15"),
]

# Tuple: (order_id, product_id, seller_id, price, freight_value). Sequential
# order_item_id is assigned per order, not globally.
ITEMS = [
    ("m1", "p1", "s1", 10.0, 2.0), ("m1", "p2", "s2", 20.0, 3.0),
    ("m2", "p3", "s3", 15.0, 1.0),
    ("a01", "p1", "s1", 10.0, 2.0), ("a01", "p2", "s2", 20.0, 3.0),
    ("a02", "p1", "s1", 25.0, 2.0), ("a03", "p3", "s3", 40.0, 2.0),
    ("a04", "p1", "s1", 999.0, 1.0),
    ("y01", "p1", "s1", 30.0, 2.0), ("y01", "p2", "s2", 15.0, 2.0),
    ("y02", "p1", "s1", 12.0, 1.0), ("y03", "p2", "s2", 12.0, 1.0),
    ("j01", "p3", "s3", 30.0, 2.0), ("j02", "p1", "s1", 20.0, 2.0),
]

# Tuple: (order_id, type, value); sequential payment ID per order.
PAYMENTS = [
    ("m1", "credit_card", 25.0), ("m1", "voucher", 5.0),
    ("m2", "boleto", 18.0),
    ("a01", "credit_card", 20.0), ("a01", "voucher", 15.0),
    ("a02", "boleto", 30.0),
    ("a03", "credit_card", 20.0), ("a03", "credit_card", 25.0),
    ("a04", "credit_card", 9999.0),
    ("y01", "credit_card", 20.0), ("y01", "voucher", 30.0),
    ("y02", "boleto", 15.0),
    ("y03", "credit_card", 4.0), ("y03", "voucher", 4.0), ("y03", "boleto", 6.0),
    ("j01", "credit_card", 35.0), ("j02", "voucher", 23.0),
]


def create_hard_fixture(db_path: Path) -> Path:
    """Create a new, isolated SQLite fixture. Refuses existing paths."""
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"Fixture path already exists: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO customers VALUES (?,?,?)", [
                ("c1", "u1", "SP"), ("c2", "u2", "RJ"),
                ("c3", "u1", "SP"), ("c4", "u3", "MG"),
            ])
            conn.executemany("INSERT INTO sellers VALUES (?,?)", [
                ("s1", "SP"), ("s2", "RJ"), ("s3", "MG"),
            ])
            conn.executemany("INSERT INTO products VALUES (?,?)", [
                ("p1", "cat_a"), ("p2", "cat_b"), ("p3", "cat_c"),
            ])
            conn.executemany("INSERT INTO category_translation VALUES (?,?)", [
                ("cat_a", "a"), ("cat_b", "b"), ("cat_c", "c"),
            ])
            conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", ORDERS)
            item_num = {}
            item_values = []
            for oid, pid, seller, price, freight in ITEMS:
                item_num[oid] = item_num.get(oid, 0) + 1
                item_values.append((oid, item_num[oid], pid, seller, price, freight))
            conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", item_values)
            pay_num = {}
            pay_values = []
            for oid, ptype, value in PAYMENTS:
                pay_num[oid] = pay_num.get(oid, 0) + 1
                pay_values.append((oid, pay_num[oid], ptype, value))
            conn.executemany("INSERT INTO order_payments VALUES (?,?,?,?)", pay_values)
            conn.commit()
    except Exception:
        db_path.unlink(missing_ok=True)
        raise
    return db_path
