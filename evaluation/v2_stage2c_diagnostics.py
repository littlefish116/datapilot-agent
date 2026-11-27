"""Reproducible, offline Stage 2C diagnostics for frozen Hard8 X08 and X03.

No DeepSeek calls and no changes to runtime SQL limits, first-run artifacts,
Gold SQL, or production data. Run only against a trusted local Olist DB.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any
from uuid import uuid4

from evaluation.v2_hard_fixture import create_hard_fixture

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "data/processed/olist.db"
RUN_DEFAULT = ROOT / "reports/agent_eval_v2_hard/20261127T094633Z_396342"
OUT_DEFAULT = ROOT / "reports/agent_eval_v2_stage2c"
FILTER_FIRST = ROOT / "evaluation/sql_candidates/X08_filter_first.sql"
GOLD_X08 = ROOT / "benchmarks/hard_gold_sql/X08.sql"
GOLD_X03 = ROOT / "benchmarks/hard_gold_sql/X03.sql"
SUITE = ROOT / "benchmarks/v2_hard_cases.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def verified_first_sql(run_dir: Path, case_id: str) -> str:
    """Verify frozen suite, Gold SQL, original response hash; never re-generate."""
    if case_id not in {"X03", "X08"}:
        raise ValueError("Only X03 and X08 are permitted")
    manifest = read_json(run_dir / "run_manifest.json")
    hashes = read_json(run_dir / "response_sha256.json")
    if manifest.get("mode") != "live":
        raise ValueError("Source must be a frozen first-run live evaluation")
    expected = manifest.get("hashes") or {}
    if sha256_file(SUITE) != expected.get("suite_sha256"):
        raise ValueError("Frozen suite SHA-256 mismatch")
    gold = GOLD_X03 if case_id == "X03" else GOLD_X08
    if sha256_file(gold) != (expected.get("gold_sql_sha256") or {}).get(case_id):
        raise ValueError("Frozen Gold SQL SHA-256 mismatch")
    path = run_dir / "responses" / f"{case_id}.json"
    if sha256_file(path) != hashes.get(case_id):
        raise ValueError(f"Original {case_id} response hash mismatch")
    data = read_json(path)
    sql = data.get("generated_sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError(f"Original {case_id} has no SQL")
    return sql


def _safe_imports():
    # Lazy import: schema/fixture tests can run without SQLGlot; runtime does
    # use the same AST validator and SQLite authorizer as Stage 2A.
    from app.v2_safe_sql import validate_sql, _authorizer
    from app.v2_sql_policy import SqlLimits
    return validate_sql, _authorizer, SqlLimits


def diagnose_query(
    sql: str, db: Path, *, vm_budget: int = 5_000_000,
    timeout_s: float = 3.0, vm_check_every: int = 1000,
    validator=None, authorizer=None,
) -> dict:
    """Read-only EXPLAIN QUERY PLAN plus observed VM-budget accounting.

    VM lower bound counts calls to sqlite progress handler; SQLite may execute
    up to vm_check_every-1 additional instructions between callbacks.
    """
    if validator is None or authorizer is None:
        val, auth, _ = _safe_imports()
        validator = validator or val
        authorizer = authorizer or auth
    if vm_budget <= 0 or timeout_s <= 0 or vm_check_every <= 0:
        raise ValueError("Resource limits must be positive")
    from app.v2_sql_policy import SqlLimits
    limits = SqlLimits(max_vm_steps=vm_budget, timeout_seconds=timeout_s,
                       vm_check_every=vm_check_every)
    normalized = validator(sql, limits)
    db = db.expanduser().resolve()
    if not db.is_file():
        raise FileNotFoundError(db)
    sql_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    result = {"status": "error", "sql_sha256": sql_digest,
              "plan": [], "elapsed_ms": None, "vm_progress_calls": 0,
              "vm_steps_lower_bound": 0, "interrupt_reason": None,
              "error_type": None, "rows": [], "row_count": 0}
    uri = db.as_uri() + "?mode=ro&cache=private"
    with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.set_authorizer(authorizer)
        try:
            plan = conn.execute("EXPLAIN QUERY PLAN " + normalized).fetchall()
            result["plan"] = [dict(row) for row in plan][:80]
        except sqlite3.DatabaseError as exc:
            result["error_type"] = "plan_error:" + type(exc).__name__
            return result

        calls = 0
        reason = None
        start = time.monotonic()
        def progress():
            nonlocal calls, reason
            calls += 1
            if calls * vm_check_every >= vm_budget:
                reason = "vm_step_budget"
                return 1
            if time.monotonic() - start > timeout_s:
                reason = "wall_time_budget"
                return 1
            return 0
        conn.set_progress_handler(progress, vm_check_every)
        try:
            cursor = conn.execute(normalized)
            # Only bounded aggregate results. No user IDs in SQL projections.
            rows = cursor.fetchmany(limits.max_rows + 1)
            if len(rows) > limits.max_rows:
                result["error_type"] = "result_truncated"
                result["interrupt_reason"] = "result_limit"
            else:
                result["status"] = "ok"
                result["rows"] = [dict(row) for row in rows]
                result["row_count"] = len(rows)
        except sqlite3.DatabaseError as exc:
            result["error_type"] = type(exc).__name__
            result["interrupt_reason"] = reason or "sqlite_runtime_error"
        finally:
            conn.set_progress_handler(None, 0)
            result["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
            result["vm_progress_calls"] = calls
            result["vm_steps_lower_bound"] = calls * vm_check_every
    return result


def normalize_x08_rows(rows: list[dict], variant: str) -> list[dict]:
    # These maps are for a SECONDARY diagnostic, not original benchmark grading.
    aliases = {
        "gold": ("purchase_month", "eligible_orders", "avg_item_amount_brl", "avg_payment_amount_brl"),
        "model": ("month", "delivered_orders", "avg_item_value_brl", "avg_payment_value_brl"),
        "filter_first": ("purchase_month", "eligible_orders", "avg_item_amount_brl", "avg_payment_amount_brl"),
    }
    if variant not in aliases:
        raise ValueError("Unknown X08 result format")
    cols = aliases[variant]
    result = []
    for row in rows:
        if not isinstance(row, dict) or not all(col in row for col in cols):
            raise ValueError(f"X08 expected columns absent for {variant}")
        vals = [row[col] for col in cols]
        if not isinstance(vals[0], str) or isinstance(vals[1], bool) or not isinstance(vals[1], int):
            raise ValueError("X08 unexpected month/count")
        result.append({"month": vals[0], "n": vals[1], "item": vals[2], "payment": vals[3]})
    return result


def compare_x08(rows: list[dict], variant: str, reference_rows: list[dict]) -> dict:
    """Check cardinality, month ordering, count, and both rounded currencies."""
    try:
        left = normalize_x08_rows(rows, variant)
        right = normalize_x08_rows(reference_rows, "gold")
        if [r["month"] for r in left] != [r["month"] for r in right]:
            return {"match": False, "reason": "months_or_order_mismatch"}
        for a, b in zip(left, right):
            if a["n"] != b["n"]:
                return {"match": False, "reason": "count_mismatch"}
            for col in ("item", "payment"):
                x, y = a[col], b[col]
                if (isinstance(x, bool) or isinstance(y, bool) or
                    not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or
                    not (abs(float(x) - float(y)) <= 0.011)):
                    return {"match": False, "reason": col + "_mismatch"}
        return {"match": True, "reason": "matched_month_count_and_both_amounts"}
    except (TypeError, ValueError) as exc:
        return {"match": False, "reason": type(exc).__name__}


def x03_adversarial_fixture(path: Path) -> Path:
    """New disposable DB. Original Hard8 fixture and scores stay unchanged."""
    create_hard_fixture(path)
    rows = [
        ("x03_valid_on_time", "c1", "delivered", "2019-12-02", "2019-12-18", "2019-12-20"),
        ("x03_valid_late", "c2", "delivered", "2019-12-03", "2019-12-23", "2019-12-20"),
        ("x03_invalid_actual", "c3", "delivered", "2019-12-04", "bad-date", "2019-12-20"),
        ("x03_invalid_expected", "c4", "delivered", "2019-12-05", "2019-12-24", "bad-date"),
        ("x03_null_actual", "c4", "delivered", "2019-12-06", None, "2019-12-20"),
    ]
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
    return path


def probe_x03(run_dir: Path) -> dict:
    from app.v2_safe_sql import execute_safe_sql
    original = verified_first_sql(run_dir, "X03")
    gold = GOLD_X03.read_text(encoding="utf-8-sig")
    with tempfile.TemporaryDirectory(prefix="datapilot_x03_") as temp:
        db = x03_adversarial_fixture(Path(temp) / "x03_stress.db")
        a = execute_safe_sql(gold, db)
        b = execute_safe_sql(original, db)
    if a["status"] != "ok" or b["status"] != "ok":
        return {"status": "error", "gold_error": a.get("error"), "model_error": b.get("error")}
    gold_values = a["rows"][0]
    model_values = b["rows"][0]
    expected = {"count": gold_values["eligible_orders"], "rate": gold_values["late_rate_pct"]}
    actual = {"count": model_values["delivered_orders"], "rate": model_values["late_delivery_rate_pct"]}
    return {"status": "ok", "reference": expected, "first_run_model": actual,
            "divergence": expected != actual,
            "explanation": "IS NOT NULL does not exclude invalid non-NULL date strings"}


def probe_x08(run_dir: Path, db: Path, *, extended: bool = False) -> dict:
    orig = verified_first_sql(run_dir, "X08")
    original_manifest = read_json(run_dir / "run_manifest.json")
    if sha256_file(db) != original_manifest.get("db_sha256"):
        raise ValueError("Olist database SHA-256 differs from first-run source")
    gold_record = read_json(run_dir / "gold_real" / "X08.json")
    if gold_record.get("status") != "ok":
        raise ValueError("First-run X08 Gold result unavailable")
    reference_rows = gold_record.get("rows")
    if not isinstance(reference_rows, list):
        raise ValueError("Gold result malformed")
    runs = {
        "first_run_model": (orig, "model"),
        "frozen_gold": (GOLD_X08.read_text(encoding="utf-8-sig"), "gold"),
        "filter_first_candidate": (FILTER_FIRST.read_text(encoding="utf-8-sig"), "filter_first"),
    }
    # Same limits as the deployed safe executor by default. The alternative
    # diagnostic budget must be explicitly enabled and never changes SqlLimits.
    budget = 25_000_000 if extended else 5_000_000
    seconds = 10.0 if extended else 3.0
    result = {"status": "ok", "extended_diagnostic_budget": extended,
              "vm_budget": budget, "wall_budget_seconds": seconds,
              "source_db_sha256": sha256_file(db), "queries": {}}
    for name, (sql, variant) in runs.items():
        res = diagnose_query(sql, db, vm_budget=budget, timeout_s=seconds)
        if res["status"] == "ok":
            res["gold_comparison"] = compare_x08(res["rows"], variant, reference_rows)
        else:
            res["gold_comparison"] = {"match": None, "reason": "query_did_not_complete"}
        result["queries"][name] = res
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Offline diagnostics for frozen Hard8 X08/X03")
    p.add_argument("--run-dir", type=Path, default=RUN_DEFAULT)
    p.add_argument("--db", type=Path, default=DB_DEFAULT)
    p.add_argument("--mode", choices=["all", "x08", "x03"], default="all")
    p.add_argument("--extended", action="store_true", help="RESEARCH only: 25M VM / 10s; never changes production limits")
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    a = p.parse_args(argv)
    try:
        run_dir = a.run_dir.resolve()
        # Always authenticate original source before running the diagnostics.
        ids = (["X03", "X08"] if a.mode == "all" else [a.mode.upper()])
        for cid in ids:
            verified_first_sql(run_dir, cid)
        result: dict[str, Any] = {
            "kind": "posthoc_offline_stage2c_diagnostics",
            "mode": a.mode, "llm_calls": 0,
            "first_run_dir": str(run_dir),
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if a.mode in ("all", "x03"):
            result["x03"] = probe_x03(run_dir)
        if a.mode in ("all", "x08"):
            result["x08"] = probe_x08(run_dir, a.db.resolve(), extended=a.extended)
        out = a.out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
        out.mkdir(parents=True, exist_ok=False)
        (out / "diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # Only a concise status printed; full plans and VM data in diagnostic.json.
        print(json.dumps({**result, "report_path": str(out / "diagnostic.json")}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as exc:
        print(f"Stage2C diagnostic failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
