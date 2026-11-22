from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / 'data' / 'raw'
DB_DIR = ROOT / 'data' / 'processed'
REPORT_DIR = ROOT / 'reports'
DB_FILE = DB_DIR / 'olist.db'

# Import in dependency order so declared foreign keys remain enabled.
FILES = {
    'category_translation': 'product_category_name_translation.csv',
    'customers': 'olist_customers_dataset.csv',
    'products': 'olist_products_dataset.csv',
    'sellers': 'olist_sellers_dataset.csv',
    'orders': 'olist_orders_dataset.csv',
    'order_items': 'olist_order_items_dataset.csv',
    'order_payments': 'olist_order_payments_dataset.csv',
    'order_reviews': 'olist_order_reviews_dataset.csv',
    'geolocation': 'olist_geolocation_dataset.csv',
}

KEYS = {
    'category_translation': ['product_category_name'],
    'customers': ['customer_id'],
    'products': ['product_id'],
    'sellers': ['seller_id'],
    'orders': ['order_id'],
    'order_items': ['order_id', 'order_item_id'],
    'order_payments': ['order_id', 'payment_sequential'],
    # Deliberately no enforced review_id or geo prefix PK.
}

ZIP_COLS = {
    'customers': 'customer_zip_code_prefix',
    'sellers': 'seller_zip_code_prefix',
    'geolocation': 'geolocation_zip_code_prefix',
}
