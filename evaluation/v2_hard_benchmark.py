"""Hard8 pre-registered semantic robustness benchmark.

8 targeted SQL tasks, two evaluation datasets (real Olist and fully synthetic
edge fixture), ONE first-attempt DeepSeek generation per case. No retries.
Existing Holdout12 case files/scoring/artifacts are never modified.

Do not present results as an unbiased estimate of general-purpose accuracy:
questions were deliberately constructed around previously observed risks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from uuid import uuid4

from evaluation import v2_holdout_benchmark as holdout
from evaluation.v2_hard_fixture import create_hard_fixture

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks/v2_hard_cases.json"
DB = ROOT / "data/processed/olist.db"
CATALOG = ROOT / "benchmarks/metric_catalog.json"
OUT = ROOT / "reports/agent_eval_v2_hard"
IDS = [f"X{i:02d}" for i in range(1, 9)]


def _json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    holdout.json_save(path, data)


def _hash(path: Path) -> str:
    return holdout.sha256(path)


def load_cases(suite: Path = SUITE, root: Path = ROOT) -> list[dict]:
    """Enforce the pre-live schema and that SQL paths cannot escape the repo."""
    raw = json.loads(suite.read_text(encoding="utf-8-sig"))
    cases = raw.get("cases")
    if (raw.get("version") != "v2-hard8-1.0" or raw.get("status") != "pre-live-frozen"
            or not isinstance(cases, list) or len(cases) != 8):
        raise ValueError("Hard8: 未找到固定的8题与版本")
    if [c.get("id") for c in cases] != IDS:
        raise ValueError("Hard8: 缺题、重复或题序被修改")
    root_resolved = root.resolve()
    for c in cases:
        if c.get("category") != "sql" or not isinstance(c.get("question"), str) or not c["question"].strip():
            raise ValueError(f"{c['id']}: 非法问题或类型")
        rel = c.get("gold_sql")
        if not isinstance(rel, str) or not rel.startswith("benchmarks/hard_gold_sql/"):
            raise ValueError(f"{c['id']}: 非法Gold路径")
        f = (root / rel).resolve()
        if not f.is_relative_to(root_resolved) or not f.is_file():
            raise ValueError(f"{c['id']}: Gold SQL 不存在或越界")
        fields = c.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{c['id']}: 评分字段为空")
        names, aliases = set(), set()
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("gold"), str):
                raise ValueError(f"{c['id']}: 评分字段格式不合法")
            gold_name = field["gold"].lower()
            if gold_name in names:
                raise ValueError(f"{c['id']}: 评分字段重复")
            names.add(gold_name)
            known = field.get("aliases")
            if not isinstance(known, list) or gold_name not in [a.lower() for a in known if isinstance(a, str)]:
                raise ValueError(f"{c['id']}: 缺少标准字段别名")
            for alias in known:
                if not isinstance(alias, str) or not alias or alias.lower() in aliases:
                    raise ValueError(f"{c['id']}: 存在重复字段别名")
                aliases.add(alias.lower())
            tol = field.get("tolerance", 0)
            if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not (0 <= tol < float("inf")):
                raise ValueError(f"{c['id']}: 非法误差容限")
        key = c.get("key")
        if key is not None and (not isinstance(key, str) or key.lower() not in names):
            raise ValueError(f"{c['id']}: 无效分组键")
        if c.get("ordered", False) not in (False, True):
            raise ValueError(f"{c['id']}: 无效排序要求")
        if c.get("ordered") and not key:
            raise ValueError(f"{c['id']}: 有排序要求却无分组键")
    return cases


def manifest_hashes(cases: list[dict], suite: Path, catalog: Path) -> dict:
    if not catalog.is_file():
        raise ValueError(f"业务指标字典不存在: {catalog}")
    return {
        "suite_sha256": _hash(suite),
        "gold_sql_sha256": {c["id"]: _hash(ROOT / c["gold_sql"]) for c in cases},
        "catalog_sha256": _hash(catalog),
    }


def _order_check(case: dict, response: dict, gold: dict, score: dict) -> dict:
    """Additional exact TopK/month order check; legacy scorer is order-agnostic."""
    if score.get("verdict") != "pass" or not case.get("ordered"):
        return score
    mapping = score.get("field_mapping") or {}
    key = case["key"]
    result = [row.get(mapping.get(key)) for row in response["execution"]["rows"]]
    expected = [row.get(key) for row in gold["rows"]]
    if result != expected:
        return {**score, "verdict": "fail", "reason": "order_mismatch",
                "issues": [f"result_order={result!r} != gold_order={expected!r}"]}
    return score


def score(case: dict, response: dict, gold: dict) -> dict:
    return _order_check(case, response, gold, holdout.score_case(case, response, gold))


def _edge_response(raw: dict, fixture: Path) -> dict:
    """Replay EXACTLY the first generated SQL on the edge DB; no LLM call."""
    if not isinstance(raw, dict) or not isinstance(raw.get("generated_sql"), str) or not raw["generated_sql"].strip():
        return {"status": "error", "generated_sql": None, "execution": None,
                "error": {"code": "no_sql", "message": "首次生成未返回SQL"}}
    from app.v2_safe_sql import execute_safe_sql
    executed = execute_safe_sql(raw["generated_sql"], fixture)
    return {"status": "ok" if executed.get("status") == "ok" else "error",
            "generated_sql": raw["generated_sql"], "execution": executed,
            "error": executed.get("error")}


def _combined(clean: dict, edge: dict) -> tuple[str, str]:
    a, b = clean["verdict"], edge["verdict"]
    if a == b == "pass":
        return "pass", "both_datasets_match"
    if a == "pass" and b == "fail":
        return "fail", "clean_pass_edge_fail"
    if a == "fail" and b == "pass":
        return "fail", "clean_fail_edge_pass"
    if "fail" in (a, b):
        return "fail", "at_least_one_dataset_fail"
    return "review", "unresolved_column_mapping_or_gold"


def summary_of(records: list[dict]) -> dict:
    def count(where):
        return sum(where(rec) for rec in records)
    tokens = {k: 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    generation_times, clean_times, edge_times = [], [], []
    for record in records:
        res = record["response"]
        usage = res.get("usage") or {}
        for k in tokens:
            n = usage.get(k) if isinstance(usage, dict) else None
            if isinstance(n, (int, float)) and not isinstance(n, bool):
                tokens[k] += n
        v = res.get("generation_ms")
        if isinstance(v, (int, float)):
            generation_times.append(v)
        ex = res.get("execution") or {}
        if isinstance(ex, dict) and isinstance(ex.get("duration_ms"), (int, float)):
            clean_times.append(ex["duration_ms"])
        ed = record.get("edge_response", {}).get("execution") or {}
        if isinstance(ed, dict) and isinstance(ed.get("duration_ms"), (int, float)):
            edge_times.append(ed["duration_ms"])
    def mean(vals):
        return round(sum(vals) / len(vals), 2) if vals else None
    n = len(records)
    return {
        "case_count": n, "llm_calls_expected": n,
        "clean_auto_pass": count(lambda r: r["clean_score"]["verdict"] == "pass"),
        "edge_auto_pass": count(lambda r: r["edge_score"]["verdict"] == "pass"),
        "both_auto_pass": count(lambda r: r["combined_verdict"] == "pass"),
        "combined_fail": count(lambda r: r["combined_verdict"] == "fail"),
        "needs_manual_review": count(lambda r: r["combined_verdict"] == "review"),
        "clean_pass_edge_fail": count(lambda r: r["combined_reason"] == "clean_pass_edge_fail"),
        "clean_execution_ok": count(lambda r: (r["response"].get("execution") or {}).get("status") == "ok"),
        "edge_execution_ok": count(lambda r: (r["edge_response"].get("execution") or {}).get("status") == "ok"),
        "both_auto_pass_rate": round(count(lambda r: r["combined_verdict"] == "pass") / n, 4) if n else None,
        "usage": tokens,
        "mean_generation_ms": mean(generation_times),
        "mean_clean_execution_ms": mean(clean_times),
        "mean_edge_execution_ms": mean(edge_times),
        "note": "针对已知风险设计的Hard8压力测试；首次SQL仅生成一次，在两份不同数据上执行。字段未知=review不计入通过；结果不代表总体准确率。"
    }


def _new_run_dir(out: Path) -> Path:
    folder = out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    for name in ("responses", "edge_responses", "gold_real", "gold_edge"):
        (folder / name).mkdir()
    return folder


def _trusted_gold(cases: list[dict], db: Path, fixture: Path, run_dir: Path) -> tuple[dict, dict]:
    real_gold, edge_gold = {}, {}
    for c in cases:
        cid = c["id"]
        real_gold[cid] = holdout.execute_gold(c, db)
        edge_gold[cid] = holdout.execute_gold(c, fixture)
        _json(run_dir / "gold_real" / f"{cid}.json", real_gold[cid])
        _json(run_dir / "gold_edge" / f"{cid}.json", edge_gold[cid])
    return real_gold, edge_gold


def _verify_replay_source(response_dir: Path, expected_hashes: dict) -> None:
    if response_dir.name != "responses" or not response_dir.is_dir():
        raise ValueError("replay 需指定既有运行目录下的 responses 子目录")
    manifest = response_dir.parent / "run_manifest.json"
    if not manifest.is_file():
        raise ValueError("原始运行缺少 run_manifest.json，拒绝未核验重放")
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if data.get("hashes") != expected_hashes or data.get("mode") != "live":
        raise ValueError("题集/Gold/字典哈希不一致，或不是首次 live 实验")
    if data.get("case_ids") != IDS:
        raise ValueError("原始响应不是固定的8题完整运行")
    hashes_file = response_dir.parent / "response_sha256.json"
    if not hashes_file.is_file():
        raise ValueError("首次运行缺少 response_sha256.json，无法校验原始响应")
    recorded = json.loads(hashes_file.read_text(encoding="utf-8-sig"))
    if set(recorded) != set(IDS):
        raise ValueError("原始响应哈希清单不完整")
    for cid in IDS:
        source = response_dir / f"{cid}.json"
        if not source.is_file():
            raise ValueError(f"缺少首次运行的原始响应: {cid}")
        if _hash(source) != recorded[cid]:
            raise ValueError(f"首次响应哈希校验失败: {cid}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataPilot V2 Hard8 semantic robustness benchmark")
    parser.add_argument("--mode", choices=["validate", "gold", "live", "replay"], default="validate")
    parser.add_argument("--live", action="store_true", help="启用收费模型调用；每题最多调用一次")
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--suite", type=Path, default=SUITE)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--response-dir", type=Path, help="replay: first-run目录/responses")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.suite)
        hashes = manifest_hashes(cases, args.suite, args.catalog)
        if args.mode == "validate":
            print(json.dumps({"mode": "validate", "case_ids": IDS, "sql_count": 8,
                              "llm_calls": 0, "hashes": hashes}, ensure_ascii=False, indent=2))
            return 0
        if args.mode != "live" and args.live:
            parser.error("--live 只能配合 --mode live 使用")
        if args.mode == "live" and not args.live:
            parser.error("真实调用必须显式指定 --mode live --live")
        if args.mode == "replay" and not args.response_dir:
            parser.error("replay 必须指定 --response-dir")
        if args.mode == "replay":
            _verify_replay_source(args.response_dir, hashes)
        if not args.db.is_file():
            raise ValueError(f"Olist 数据库文件不存在: {args.db}")
        db_digest = _hash(args.db)
        if args.mode == "replay":
            source_manifest = json.loads((args.response_dir.parent / "run_manifest.json").read_text(encoding="utf-8-sig"))
            if source_manifest.get("db_sha256") != db_digest:
                raise ValueError("当前Olist数据库哈希与首次运行不一致，拒绝对不同数据重放")
        run_dir = _new_run_dir(args.out)
        fixture = create_hard_fixture(run_dir / "synthetic_fixture.db")
        _json(run_dir / "run_manifest.json", {
            "mode": args.mode, "case_ids": IDS, "hashes": hashes,
            "fixture_sha256": _hash(fixture),
            "db_size_bytes": args.db.stat().st_size,
            "db_mtime_ns": args.db.stat().st_mtime_ns,
            "db_sha256": db_digest,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "gold_exposure": "Gold SQL/data only used by evaluator, NEVER passed to model",
            "reference_note": "原始Olist数据库通过SHA256固定；清单保存于本次run_dir。",
        })
        # Execute ALL trusted Gold SQL before a single paid LLM call. Fail fast
        # if the fixture or a real-data Gold query is broken.
        real_gold, edge_gold = _trusted_gold(cases, args.db, fixture, run_dir)
        if args.mode == "gold":
            payload = {"mode": "gold", "gold_real_ok": 8, "gold_edge_ok": 8,
                       "synthetic_fixture_sha256": _hash(fixture), "llm_calls": 0,
                       "run_dir": str(run_dir),
                       "synthetic_examples": {
                           "X01": edge_gold["X01"]["rows"],
                           "X02": edge_gold["X02"]["rows"],
                           "X03": edge_gold["X03"]["rows"],
                           "X07": edge_gold["X07"]["rows"]}}
            _json(run_dir / "summary.json", payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.mode == "live":
            from app.v2_text_to_sql import DeepSeekSQLBackend
            backend = DeepSeekSQLBackend()
            print("Hard8首次实验：8次模型调用，失败不重试；原始响应立即落盘。", flush=True)
        else:
            backend = None
        records = []
        response_hashes = {}
        for case in cases:
            cid = case["id"]
            if args.mode == "live":
                from app.v2_text_to_sql import run_text_to_sql
                try:
                    raw = run_text_to_sql(case["question"], args.db, backend=backend, catalog_path=args.catalog)
                except Exception as exc:
                    raw = {"status": "error", "generated_sql": None, "execution": None,
                           "error": {"code": "runner_error", "type": type(exc).__name__},
                           "usage": None, "generation_ms": None}
            else:
                raw = json.loads((args.response_dir / f"{cid}.json").read_text(encoding="utf-8-sig"))
                if not isinstance(raw, dict):
                    raise ValueError(f"{cid}: 原始响应不是JSON对象")
            response_file = run_dir / "responses" / f"{cid}.json"
            _json(response_file, raw)
            response_hashes[cid] = _hash(response_file)
            _json(run_dir / "response_sha256_partial.json", response_hashes)
            clean_score = score(case, raw, real_gold[cid])
            edge = _edge_response(raw, fixture)
            _json(run_dir / "edge_responses" / f"{cid}.json", edge)
            edge_score = score(case, edge, edge_gold[cid])
            verdict, reason = _combined(clean_score, edge_score)
            record = {"case_id": cid, "clean_score": clean_score, "edge_score": edge_score,
                      "combined_verdict": verdict, "combined_reason": reason,
                      "response": raw, "edge_response": edge}
            records.append(record)
            _json(run_dir / "scores_partial.json", [
                {k: r[k] for k in ("case_id", "clean_score", "edge_score", "combined_verdict", "combined_reason")}
                for r in records])
            print(f"{cid}: clean={clean_score['verdict']} edge={edge_score['verdict']} → {verdict} ({reason})", flush=True)
        _json(run_dir / "response_sha256.json", response_hashes)
        result = summary_of(records)
        result.pop("llm_calls_expected", None)
        result.update({"llm_calls_this_run": len(records) if args.mode == "live" else 0,
                       "mode": args.mode, "run_dir": str(run_dir), "hashes": hashes,
                       "evaluated_at_utc": datetime.now(timezone.utc).isoformat()})
        _json(run_dir / "scores.json", [
            {k: r[k] for k in ("case_id", "clean_score", "edge_score", "combined_verdict", "combined_reason")}
            for r in records])
        _json(run_dir / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(r["combined_verdict"] == "pass" for r in records) else 2
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"困难实验未完成: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
