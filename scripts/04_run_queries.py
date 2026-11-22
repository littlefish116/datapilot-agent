"""Run prewritten, read-only analytics SQL and export results to reports/sql/."""
import sqlite3
import pandas as pd
from config import DB_FILE, REPORT_DIR, ROOT


def main() -> None:
    if not DB_FILE.exists():
        raise FileNotFoundError(f'Missing {DB_FILE}. Load the database first.')
    output = REPORT_DIR / 'sql'
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        for path in sorted((ROOT / 'sql' / 'queries').glob('*.sql')):
            sql = path.read_text(encoding='utf-8')
            statement = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--')).strip()
            if not statement.upper().startswith(('SELECT', 'WITH')):
                # SQL files in this starter have one SELECT and no untrusted dynamic input.
                raise ValueError(f'Expect one SELECT query in {path.name}')
            df = pd.read_sql_query(sql, conn)
            file = output / f'{path.stem}.csv'
            df.to_csv(file, index=False, encoding='utf-8-sig')
            print(f'\n{path.name}: {len(df)} rows -> {file}')
            print(df.head(8).to_string(index=False))


if __name__ == '__main__':
    main()
