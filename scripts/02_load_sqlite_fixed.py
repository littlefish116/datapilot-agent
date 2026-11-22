"""Load nine source CSVs into SQLite with declared keys and foreign keys."""
import os
import sqlite3
from contextlib import closing

import pandas as pd
from config import DB_FILE, DB_DIR, FILES, RAW_DIR, ROOT, ZIP_COLS


def main() -> None:
    missing = [f for f in FILES.values() if not (RAW_DIR / f).is_file()]
    if missing:
        raise FileNotFoundError(f'Place all nine CSV files in {RAW_DIR}: missing {missing}')
    DB_DIR.mkdir(parents=True, exist_ok=True)
    temp = DB_DIR / 'olist_building.db'
    if temp.exists():
        temp.unlink()

    try:
        with closing(sqlite3.connect(temp)) as conn:
            with conn:
                conn.execute('PRAGMA foreign_keys = ON')
                conn.executescript((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
                for table, filename in FILES.items():
                    count = 0
                    for df in pd.read_csv(RAW_DIR / filename, dtype=str, chunksize=25000):
                        zip_col = ZIP_COLS.get(table)
                        if zip_col:
                            # Important: do not turn e.g. "01037" into integer 1037.
                            df[zip_col] = df[zip_col].str.zfill(5)
                        df.to_sql(table, conn, if_exists='append', index=False, method='multi', chunksize=100)
                        count += len(df)
                    print(f'Imported {table:21}: {count:,} rows')
                # Build indexes after bulk loading to keep the import relatively fast.
                conn.executescript('''
                    CREATE INDEX idx_orders_purchase ON orders(order_purchase_timestamp);
                    CREATE INDEX idx_orders_status ON orders(order_status);
                    CREATE INDEX idx_products_category ON products(product_category_name);
                    CREATE INDEX idx_reviews_order ON order_reviews(order_id);
                    CREATE INDEX idx_geo_zip ON geolocation(geolocation_zip_code_prefix);
                ''')
                errors = conn.execute('PRAGMA foreign_key_check').fetchall()
                if errors:
                    raise ValueError(f'Foreign-key violations, first 10: {errors[:10]}')
        os.replace(temp, DB_FILE)
    except Exception:
        if temp.exists():
            try:
                temp.unlink()
            except PermissionError:
                # Do not mask the original exception when another process holds the file.
                pass
        raise
    print(f'\nCreated {DB_FILE}')


if __name__ == '__main__':
    main()
