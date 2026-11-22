"""Scan all 9 source CSVs without changing any source rows."""
import json
from collections import Counter

import pandas as pd
from config import FILES, KEYS, RAW_DIR, REPORT_DIR, ZIP_COLS


def main() -> None:
    missing = [filename for filename in FILES.values() if not (RAW_DIR / filename).is_file()]
    if missing:
        raise FileNotFoundError(f'Missing {len(missing)} Olist CSV(s) under {RAW_DIR}: {missing}')

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    audit = {}
    for table, filename in FILES.items():
        path = RAW_DIR / filename
        rows = 0
        nulls = Counter()
        null_key_rows = 0
        key_counts = Counter()
        columns = None
        key_cols = KEYS.get(table)
        for chunk in pd.read_csv(path, dtype=str, chunksize=25000, encoding='utf-8'):
            if columns is None:
                columns = list(chunk.columns)
                if key_cols:
                    for col in key_cols:
                        if col not in columns:
                            raise ValueError(f'{filename} is missing key column {col}')
            rows += len(chunk)
            nulls.update({c: int(v) for c, v in chunk.isna().sum().items()})
            if key_cols:
                null_key_rows += int(chunk[key_cols].isna().any(axis=1).sum())
                # Tuple keys preserve 1-to-many granularity; 25k chunks bound memory.
                for key in chunk[key_cols].itertuples(index=False, name=None):
                    key_counts[key] += 1

        duplicate_keys = sum(v - 1 for v in key_counts.values() if v > 1)
        audit[table] = {
            'filename': filename,
            'rows': rows,
            'column_count': len(columns or []),
            'columns': columns,
            'null_counts': dict(nulls),
            'key_columns': key_cols,
            'duplicate_key_rows': duplicate_keys if key_cols else None,
            'null_key_rows': null_key_rows if key_cols else None,
            'zip_prefix_column': ZIP_COLS.get(table),
        }
        print(f'{table:21} rows={rows:>9,}  cols={len(columns or []):>2}  PK_extra={duplicate_keys if key_cols else "N/A"}')

    out = REPORT_DIR / 'raw_audit.json'
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\nWrote {out}')
    print('NOTE: differing Olist dataset versions may have different row counts.')


if __name__ == '__main__':
    main()
