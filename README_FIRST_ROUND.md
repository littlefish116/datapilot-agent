# DataPilot · Stage 1 / Round 1

Goal: audit the Olist files, build a relational SQLite database, verify fan-out risks,
and run four baseline read-only analytics queries. This starter **does not** contain
Olist data. Download the source under CC BY-NC-SA 4.0 from:
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

## Directory layout

```
project/
  data/raw/                  # put 9 original CSVs here (ignored by Git)
  data/processed/            # generated olist.db (ignored by Git)
  scripts/                   # 4 runnable scripts + config.py
  sql/schema.sql             # physical schema, foreign keys, indexes
  sql/queries/               # four analysis queries
  tests/test_sql_metrics.py  # four synthetic, independent tests
  reports/                   # output: audit, quality, SQL results
```

## Windows / PowerShell quick start

From the project ROOT, with Python 3.11 selected:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/01_audit_raw.py
python scripts/02_load_sqlite.py
python scripts/03_quality_checks.py
python scripts/04_run_queries.py
python -m unittest discover -s tests -v
```

`Activate.ps1` may be blocked by PowerShell execution policy. A simple alternative
is to run `.venv\Scripts\python.exe` rather than `python` for each command.

## Expected artifacts and interpretation

- `reports/raw_audit.json`: row/column counts, null counts, PK candidates.
- `data/processed/olist.db`: all 9 tables; source columns unchanged.
- `reports/db_quality.json`: duplicate review ID count, multiple payments/items,
  delivered orders with missing dates, sample incorrect JOIN vs correct sum.
- `reports/sql/*.csv`: four SQL outputs.
- Tests: `Ran 4 tests ... OK` on **synthetic** records. This does not prove the
  raw Olist data are valid; separately run audits and DB foreign-key checks.

## Metric definitions (must appear in your README for future interviewers)

- **delivered_item_value_brl** = `SUM(order_items.price)` for delivered orders,
  grouped by purchase month. Excludes freight. It is *not* cash receipts,
  payment amount or net/recognized revenue.
- **late_rate_pct** = delivered orders arriving on a later calendar day than
  the estimated delivery date divided by delivered orders with both dates.
- **orders_containing_category** = distinct order count within that category;
  summing it across categories does not yield unique orders.
- `customer_id` identifies one order-associated customer record;
  `customer_unique_id` is the cross-order buyer identity.
- Source monthly data is uneven at beginning/end; do not interpret incomplete
  historical months as real business declines.

## Pitfalls worth explaining in interviews

1. `order_items` is one-to-many with `orders`; `order_payments` is also one-to-many.
   Naively joining both and summing item values inflates totals.
2. `review_id` can repeat; the physical schema deliberately does not use it as PK.
   Before joining reviews to orders, define a deterministic deduplication rule.
3. Geolocation has many rows per ZIP prefix; use a ZIP-grain aggregated mapping
   before joining to customer/seller records.
4. Prices are REAL for this first SQL stage. For payment/reconciliation work,
   use integer cents or decimal arithmetic and explicitly define rounding.
5. All raw CSV files are kept unchanged, and SQLite is regenerated from scratch.
6. If strict FK import fails, inspect the data and report it; do not disable
   validation or silently delete rows.

## After Round 1

Write your own SQL queries 05–08: top states, average items per order,
payment-method mix (with correct denominator), and month-to-month growth.
For each, document the base table, row grain, metric definition, filtering and
potential join multiplicity. Review with an instructor before using in an Agent.
