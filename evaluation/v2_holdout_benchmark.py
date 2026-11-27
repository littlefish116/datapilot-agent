"""Frozen first-attempt DataPilot V2 holdout experiment (8 SQL + 4 behavior).

Modes:
  validate - fixture/schema/hash validation, no DB or network
  gold     - execute trusted reference SQL on the local Olist DB, no network
  live     - explicitly gated DeepSeek calls; save each raw response before scoring
  replay   - rescore saved responses against current local reference results

This runner NEVER supplies the reference SQL, expected output, or scoring spec to
DeepSeek. Only the user question and the existing metric catalog are supplied.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks/v2_holdout_cases.json"
DB = ROOT / "data/processed/olist.db"
CATALOG = ROOT / "benchmarks/metric_catalog.json"
OUT = ROOT / "reports/agent_eval_v2_holdout"
CATEGORIES = {"sql": 8, "clarify": 2, "unsupported": 2}
MAX_REFERENCE_ROWS = 50


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def load_suite(path: Path = SUITE, *, root: Path = ROOT) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = raw.get("cases")
    if raw.get("status") != "frozen-before-first-live-run" or not isinstance(cases, list):
        raise ValueError("缺少冻结版 holdout cases")
    seen = set()
    counts = dict.fromkeys(CATEGORIES, 0)
    for c in cases:
        if not isinstance(c, dict):
            raise ValueError("case 必须是 JSON 对象")
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.startswith("H") or cid in seen:
            raise ValueError(f"非法或重复的 case ID: {cid}")
        seen.add(cid)
        category = c.get("category")
        if category not in CATEGORIES:
            raise ValueError(f"不允许的分类: {category}")
        counts[category] += 1
        if not isinstance(c.get("question"), str) or not c["question"].strip():
            raise ValueError(f"{cid}: 缺少问题")
        if category == "sql":
            rel = c.get("gold_sql")
            if not isinstance(rel, str) or not rel.startswith("benchmarks/holdout_gold_sql/"):
                raise ValueError(f"{cid}: Gold SQL 路径不合法")
            file = (root / rel).resolve()
            if not file.is_relative_to(root.resolve()) or not file.is_file():
                raise ValueError(f"{cid}: Gold SQL 不存在或越界")
            fields = c.get("fields")
            if not isinstance(fields, list) or not fields:
                raise ValueError(f"{cid}: 缺少评分字段")
            gold_fields = set()
            claimed_aliases = set()
            for f in fields:
                if not isinstance(f, dict) or not isinstance(f.get("gold"), str):
                    raise ValueError(f"{cid}: 评分字段格式错误")
                name = f["gold"].lower()
                if name in gold_fields:
                    raise ValueError(f"{cid}: 重复 Gold 字段")
                gold_fields.add(name)
                al = f.get("aliases")
                if not isinstance(al, list) or not al or name not in [a.lower() for a in al if isinstance(a, str)]:
                    raise ValueError(f"{cid}: 字段缺失标准别名")
                for a in al:
                    if not isinstance(a, str) or not a.strip() or a.lower() in claimed_aliases:
                        raise ValueError(f"{cid}: 空/重复别名（避免歧义）")
                    claimed_aliases.add(a.lower())
                tolerance = f.get("tolerance", 0)
                if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError(f"{cid}: 非法误差容限")
            if c.get("key") is not None and c["key"].lower() not in gold_fields:
                raise ValueError(f"{cid}: 分组键不在评分字段里")
        else:
            if "gold_sql" in c or "fields" in c or c.get("key") is not None:
                raise ValueError(f"{cid}: 澄清/拒绝类不得含参考 SQL 或评分字段")
    if counts != CATEGORIES or len(cases) != 12:
        raise ValueError(f"应固定为8 SQL + 2 clarify + 2 unsupported，实际为 {counts}")
    if seen != {f"H{i:02d}" for i in range(1, 13)}:
        raise ValueError("冻结题集必须包含 H01–H12")
    return cases


def execute_gold(case: dict, db: Path, root: Path = ROOT) -> dict:
    """Read-only, trusted human-authored reference query, never model generated."""
    sql_path = root / case["gold_sql"]
    sql = sql_path.read_text(encoding="utf-8-sig").strip()
    if not db.is_file():
        raise ValueError(f"Olist SQLite 数据库不存在: {db}")
    if not sql or not sql.lstrip("- \n\r\t").strip():
        raise ValueError(f"{case['id']}: 空 Gold SQL")
    start = time.monotonic()
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            cursor = conn.execute(sql)
            if cursor.description is None:
                raise ValueError("Gold SQL 无返回值")
            columns = [col[0] for col in cursor.description]
            rows = [dict(r) for r in cursor.fetchmany(MAX_REFERENCE_ROWS + 1)]
    except sqlite3.Error as exc:
        raise ValueError(f"{case['id']}: Gold SQL 执行失败 ({type(exc).__name__}): {exc}") from exc
    if not rows or len(rows) > MAX_REFERENCE_ROWS:
        raise ValueError(f"{case['id']}: Gold SQL 必须返回1–{MAX_REFERENCE_ROWS}行")
    if len(columns) != len(set(columns)):
        raise ValueError(f"{case['id']}: Gold SQL 列名重复")
    required = {f["gold"] for f in case["fields"]}
    if not required.issubset(columns):
        raise ValueError(f"{case['id']}: Gold SQL 缺少字段 {sorted(required-set(columns))}")
    return {"status": "ok", "columns": columns, "rows": rows,
            "returned_rows": len(rows), "truncated": False,
            "duration_ms": round((time.monotonic() - start) * 1000, 2)}


def _eq(actual: Any, gold: Any, tolerance: float) -> bool:
    if isinstance(actual, bool) or isinstance(gold, bool):
        return actual is gold
    if isinstance(actual, (int, float)) and isinstance(gold, (int, float)):
        return math.isfinite(actual) and math.isfinite(gold) and abs(actual-gold) <= tolerance
    return actual == gold


def _mapped_columns(case: dict, observed: list[str]) -> dict[str, str] | None:
    mapping = {}
    if any(not isinstance(s, str) for s in observed):
        return None
    lowered = [s.lower() for s in observed]
    if len(set(lowered)) != len(lowered):
        return None
    for field in case["fields"]:
        matches = [observed[i] for i, name in enumerate(lowered) if name in {a.lower() for a in field["aliases"]}]
        if len(matches) != 1:
            return None
        mapping[field["gold"]] = matches[0]
    if len(set(mapping.values())) != len(mapping):
        return None
    return mapping


def _keymap(rows: list[dict], name: str) -> dict | None:
    result = {}
    for row in rows:
        value = row.get(name)
        if not isinstance(value, str) or not value or value in result:
            return None
        result[value] = row
    return result


def score_case(case: dict, response: dict, gold: dict | None = None) -> dict:
    """Conservative automatic scoring; unfamiliar aliases require human review."""
    cid, category = case["id"], case["category"]
    base = {"case_id": cid, "category": category, "verdict": "fail", "reason": None,
            "issues": [], "manual_review_required": False}
    if not isinstance(response, dict):
        return {**base, "reason": "invalid_response"}
    if category != "sql":
        expected = category
        matched = (response.get("status") == expected
                   and not response.get("generated_sql")
                   and response.get("execution") is None
                   and isinstance(response.get("message"), str)
                   and bool(response["message"].strip()))
        return {**base, "verdict": "behavior_pass" if matched else "fail",
                "reason": "expected_behavior" if matched else "wrong_behavior",
                "manual_review_required": matched,
                "issues": ["仅验证澄清/不支持行为形式；理由是否恰当仍需人工复核"] if matched else ["模型未作出正确的非SQL决策"]}
    ex = response.get("execution")
    if response.get("status") != "ok" or not isinstance(ex, dict) or ex.get("status") != "ok":
        return {**base, "reason": "execution_failure", "issues": [str(response.get("error") or (ex or {}).get("error"))]}
    if not response.get("generated_sql"):
        return {**base, "reason": "missing_generated_sql"}
    if ex.get("truncated") is not False:
        return {**base, "reason": "truncated"}
    rows = ex.get("rows")
    columns = ex.get("columns")
    if not isinstance(rows, list) or not isinstance(columns, list) or any(not isinstance(r, dict) for r in rows):
        return {**base, "reason": "invalid_rows"}
    if ex.get("returned_rows") != len(rows):
        return {**base, "reason": "row_count_metadata_mismatch"}
    if not isinstance(gold, dict) or gold.get("status") != "ok":
        return {**base, "verdict": "review", "reason": "missing_gold", "manual_review_required": True}
    golden = gold["rows"]
    if len(rows) != len(golden):
        return {**base, "reason": "row_count_mismatch", "issues": [f"actual={len(rows)}, gold={len(golden)}"]}
    mapped = _mapped_columns(case, columns)
    if mapped is None:
        return {**base, "verdict": "review", "reason": "column_mapping_unresolved",
                "manual_review_required": True,
                "issues": ["模型使用了预登记别名之外的列名，需检查首次输出，不应事后修改评分规则"],
                "observed_columns": columns, "gold_columns": [f["gold"] for f in case["fields"]]}
    if case["key"]:
        key = case["key"]
        actual_map = _keymap(rows, mapped[key])
        gold_map = _keymap(golden, key)
        if actual_map is None or gold_map is None:
            return {**base, "reason": "invalid_group_keys"}
        if actual_map.keys() != gold_map.keys():
            return {**base, "reason": "group_key_mismatch",
                    "issues": [f"missing={sorted(gold_map.keys()-actual_map.keys())}, extra={sorted(actual_map.keys()-gold_map.keys())}"]}
        pairs = [(k, actual_map[k], gold_map[k]) for k in sorted(gold_map)]
    else:
        if len(rows) != 1:
            return {**base, "reason": "expected_scalar"}
        pairs = [("scalar", rows[0], golden[0])]
    issues = []
    for label, actual, expected in pairs:
        for f in case["fields"]:
            name = f["gold"]
            if name == case["key"]:
                continue
            observed = actual.get(mapped[name])
            value = expected.get(name)
            if not _eq(observed, value, f.get("tolerance", 0)):
                issues.append(f"{label}/{name}: actual={observed!r}, gold={value!r}")
    return {**base, "verdict": "fail" if issues else "pass",
            "reason": "value_mismatch" if issues else "matched_gold_requested_metrics",
            "issues": issues[:25], "field_mapping": mapped, "checked_rows": len(rows)}


def summarize(records: list[dict]) -> dict:
    sql_rows = [r for r in records if r["category"] == "sql"]
    behavioral_rows = [r for r in records if r["category"] != "sql"]
    usages = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    generate_times, execution_times = [], []
    for r in records:
        response = r["response"]
        u = response.get("usage") or {}
        if isinstance(u, dict):
            for k in usages:
                n = u.get(k)
                if isinstance(n, (int, float)) and not isinstance(n, bool):
                    usages[k] += n
        a = response.get("generation_ms")
        if isinstance(a, (int, float)) and math.isfinite(a):
            generate_times.append(a)
        ex = response.get("execution")
        if isinstance(ex, dict):
            b = ex.get("duration_ms")
            if isinstance(b, (int, float)) and math.isfinite(b):
                execution_times.append(b)
    sql_pass = sum(r["score"]["verdict"] == "pass" for r in sql_rows)
    behavior_pass = sum(r["score"]["verdict"] == "behavior_pass" for r in behavioral_rows)
    review = sum(r["score"]["verdict"] == "review" for r in records)
    return {
        "case_count": len(records), "sql_count": len(sql_rows), "behavior_count": len(behavioral_rows),
        "sql_correct": sql_pass, "behavior_format_correct_pending_reason_review": behavior_pass,
        "needs_manual_review": review + behavior_pass,
        "sql_first_attempt_accuracy_strict": round(sql_pass / len(sql_rows), 4) if sql_rows else None,
        "behavior_format_match_rate": round(behavior_pass / len(behavioral_rows), 4) if behavioral_rows else None,
        "sql_generated_cases": sum(bool(r["response"].get("generated_sql")) for r in sql_rows),
        "sql_executed_ok_cases": sum((r["response"].get("execution") or {}).get("status") == "ok" for r in sql_rows),
        "total_token_usage": usages,
        "mean_generation_ms": round(sum(generate_times)/len(generate_times), 2) if generate_times else None,
        "mean_execution_ms": round(sum(execution_times)/len(execution_times), 2) if execution_times else None,
        "note": "8 SQL题的首次结果以固定别名及全部Gold行判分；新别名待复核，4条澄清/拒绝题只自动检测行为形式。仅为这套未见题的首次单次调用结果。",
    }


def json_save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataPilot V2 frozen unseen-question benchmark")
    parser.add_argument("--mode", choices=["validate", "gold", "live", "replay"], default="validate")
    parser.add_argument("--live", action="store_true", help="真实调用 DeepSeek；仅 live 模式允许，可能收费")
    parser.add_argument("--case-id", action="append", help="选做部分题目；首次完整评测不要使用")
    parser.add_argument("--suite", type=Path, default=SUITE)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--response-dir", type=Path, help="replay: 之前运行生成的 responses 文件夹")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    try:
        cases = load_suite(args.suite)
        known = {c["id"] for c in cases}
        if args.case_id and not set(args.case_id).issubset(known):
            raise ValueError(f"未知题号: {sorted(set(args.case_id)-known)}")
        cases = [c for c in cases if not args.case_id or c["id"] in args.case_id]
        hashes = {"suite_sha256": sha256(args.suite),
                  "gold_sql_sha256": {c["id"]: sha256(ROOT / c["gold_sql"]) for c in cases if c["category"] == "sql"},
                  "catalog_sha256": sha256(args.catalog) if args.catalog.is_file() else None}
        if args.mode == "validate":
            print(json.dumps({"mode": "validate", "case_ids": [c["id"] for c in cases],
                              "distribution": {category: sum(c["category"] == category for c in cases) for category in CATEGORIES},
                              "llm_calls": 0, **hashes}, ensure_ascii=False, indent=2))
            return 0
        if args.mode == "live" and not args.live:
            parser.error("live 模式必须显式同时传入 --live")
        if args.mode != "live" and args.live:
            parser.error("--live 仅允许用于 --mode live")
        if args.mode == "replay" and not args.response_dir:
            parser.error("replay 模式必须传入 --response-dir")
        if not args.db.is_file():
            raise ValueError(f"Olist 数据库不存在: {args.db}")
        run_dir = args.out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "responses").mkdir()
        (run_dir / "gold").mkdir()
        metadata = {"mode": args.mode, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "case_ids": [c["id"] for c in cases], "hashes": hashes,
                    "db_size_bytes": args.db.stat().st_size, "db_mtime_ns": args.db.stat().st_mtime_ns,
                    "warning": "数据库仅记录大小与修改时间，不是密码学内容哈希；复现时需使用相同版本数据库。"}
        json_save(run_dir / "run_manifest.json", metadata)
        backend = None
        if args.mode == "live":
            from app.v2_text_to_sql import DeepSeekSQLBackend
            backend = DeepSeekSQLBackend()
            print(f"将执行 {len(cases)} 次首次模型调用（每题仅一次，可能收费）；原始响应会立即保存。", flush=True)
        records = []
        for c in cases:
            cid = c["id"]
            if args.mode == "gold" and c["category"] != "sql":
                print(f"{cid}: behavior-only, no SQL Gold", flush=True)
                continue
            if args.mode == "gold":
                gold = execute_gold(c, args.db)
                json_save(run_dir / "gold" / f"{cid}.json", gold)
                print(f"{cid}: gold ok, {len(gold['rows'])} rows", flush=True)
                continue
            if args.mode == "live":
                from app.v2_text_to_sql import run_text_to_sql
                try:
                    response = run_text_to_sql(c["question"], args.db, backend=backend, catalog_path=args.catalog)
                except Exception as exc:
                    # Preserve a failed first attempt as evidence. No automatic retries.
                    response = {"status": "error", "question": c["question"], "generated_sql": None,
                                "message": None, "generation_ms": None, "usage": None,
                                "execution": None, "error": {"code": "runner_error", "message": type(exc).__name__}}
            else:
                source = args.response_dir / f"{cid}.json"
                response = json.loads(source.read_text(encoding="utf-8-sig"))
                if not isinstance(response, dict):
                    raise ValueError(f"{cid}: 响应必须是 JSON 对象")
            # Save the raw first-attempt response before reading/comparing Gold.
            json_save(run_dir / "responses" / f"{cid}.json", response)
            gold = execute_gold(c, args.db) if c["category"] == "sql" else None
            if gold is not None:
                json_save(run_dir / "gold" / f"{cid}.json", gold)
            score = score_case(c, response, gold)
            records.append({"case_id": cid, "category": c["category"], "score": score, "response": response})
            json_save(run_dir / "scores_partial.json", [r["score"] for r in records])
            print(f"{cid}: {score['verdict']} ({score['reason']})", flush=True)
        if args.mode == "gold":
            json_save(run_dir / "summary.json", {"mode": "gold", "gold_checked": sum(c["category"] == "sql" for c in cases),
                                                  "llm_calls": 0, "output_dir": str(run_dir), "hashes": hashes})
            print(f"Gold SQL 验证完成，结果目录：{run_dir}")
            return 0
        summary = summarize(records)
        summary.update({"mode": args.mode, "run_dir": str(run_dir), "hashes": hashes,
                        "evaluated_utc": datetime.now(timezone.utc).isoformat()})
        json_save(run_dir / "scores.json", [r["score"] for r in records])
        json_save(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        # 'review' is explicitly not a pass; fail/review return nonzero for visibility.
        return 0 if all(r["score"]["verdict"] in {"pass", "behavior_pass"} for r in records) else 2
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"实验未完成: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
