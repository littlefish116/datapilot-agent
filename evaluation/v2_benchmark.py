"""Reproducible development-case benchmark for DataPilot Stage 2B.

validate: offline, no DB/API calls
live:     exactly one LLM attempt per requested case, ONLY with --live
replay:   rescore saved JSON outputs, no LLM calls
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from evaluation.v1_runner import run_reference
from evaluation.v2_result_evaluator import SPECS, compare_task_result, summarize

ROOT = Path(__file__).resolve().parents[1]


def load_cases(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    result = []
    for case in raw["cases"]:
        if case.get("id") in SPECS:
            if case.get("group") != "dynamic_sql" or len(case.get("turns", [])) != 1 or not case.get("reference_sql"):
                raise ValueError(f"Case 格式不符合动态查询要求: {case.get('id')}")
            sql_path = (ROOT / case["reference_sql"]).resolve()
            if not sql_path.is_relative_to(ROOT.resolve()) or not sql_path.is_file():
                raise ValueError(f"Gold SQL 文件不存在或路径越界: {case['id']}")
            result.append(case)
    if {c["id"] for c in result} != set(SPECS):
        raise ValueError("开发集的 D01–D06 不完整")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DataPilot V2 Stage 2B development SQL benchmark")
    p.add_argument("--mode", choices=["validate", "live", "replay"], default="validate")
    p.add_argument("--case-id", choices=sorted(SPECS), action="append", help="只运行选中的 case；可重复传入")
    p.add_argument("--live", action="store_true", help="显式授权真实 DeepSeek API 调用（可能收费）")
    p.add_argument("--db", type=Path, default=ROOT / "data/processed/olist.db")
    p.add_argument("--suite", type=Path, default=ROOT / "benchmarks/v1_cases.json")
    p.add_argument("--catalog", type=Path, default=ROOT / "benchmarks/metric_catalog.json")
    p.add_argument("--response-dir", type=Path, help="replay 模式读取保存的 <case_id>.json 文件所在目录")
    p.add_argument("--reference-snapshot", type=Path, help="使用 V1 references.json 快照评分（适合无 API、无数据库的 replay）")
    p.add_argument("--out", type=Path, default=ROOT / "reports/agent_eval_v2")
    args = p.parse_args(argv)
    try:
        cases = load_cases(args.suite)
        cases = [c for c in cases if not args.case_id or c["id"] in args.case_id]
        if args.mode == "validate":
            print(json.dumps({"mode": "validate", "cases": [c["id"] for c in cases],
                              "llm_calls": 0, "note": "只验证 case / Gold SQL 文件存在；不评测计算结果"}, ensure_ascii=False, indent=2))
            return 0
        if args.mode == "live" and not args.live:
            p.error("live 模式必须额外传入 --live 才会调用 DeepSeek")
        if args.mode == "replay" and not args.response_dir:
            p.error("replay 模式需要 --response-dir 指定之前保存的响应目录")
        if (args.mode == "live" or not args.reference_snapshot) and not args.db.is_file():
            p.error(f"本地 Olist DB 不存在: {args.db}")
        snapshot = None
        if args.reference_snapshot:
            raw_refs = json.loads(args.reference_snapshot.read_text(encoding="utf-8-sig"))
            if not isinstance(raw_refs, list):
                raise ValueError("reference-snapshot 必须是 V1 references.json 数组")
            snapshot = {r["case_id"]: r["reference"] for r in raw_refs}
            if not set(c["id"] for c in cases).issubset(snapshot):
                raise ValueError("reference-snapshot 缺少当前选择的案例")
        backend = None
        if args.mode == "live":
            from app.v2_text_to_sql import DeepSeekSQLBackend
            backend = DeepSeekSQLBackend()
            print(f"注意：将顺序执行 {len(cases)} 次实时模型调用；每题只调用一次，可能收费。", flush=True)
        # live requires a fresh run dir; replay also stores independent scoring version.
        run_dir = args.out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6])
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "responses").mkdir()
        records = []
        for case in cases:
            cid = case["id"]
            reference = snapshot[cid] if snapshot is not None else run_reference(case, args.db)
            if reference is None or reference.get("status") != "ok" or reference.get("truncated"):
                raise RuntimeError(f"{cid}: Gold SQL 未正确运行: {reference}")
            if args.mode == "live":
                from app.v2_text_to_sql import run_text_to_sql
                response = run_text_to_sql(case["turns"][0]["question"], args.db, backend=backend, catalog_path=args.catalog)
            else:
                response = json.loads((args.response_dir / (cid + ".json")).read_text(encoding="utf-8-sig"))
            if not isinstance(response, dict):
                raise ValueError(f"{cid}: 响应 JSON 必须为对象")
            (run_dir / "responses" / (cid + ".json")).write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            score = compare_task_result(cid, response, reference)
            records.append({"case_id": cid, "score": score, "response": response})
            print(f"{cid}: {score['verdict']} ({score['reason']})", flush=True)
        summary = summarize(records)
        summary.update({"mode": args.mode, "case_ids": [c["id"] for c in cases],
                        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "gold_source": "v1_reference_snapshot" if snapshot is not None else "local_gold_sql_database", "output_dir": str(run_dir)})
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (run_dir / "scores.json").write_text(json.dumps([r["score"] for r in records], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed_cases"] == summary["case_count"] else 2
    except (ValueError, OSError, RuntimeError, KeyError, json.JSONDecodeError) as e:
        print(f"评测未完成: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
