"""Interactive local Stage 2B test. Live LLM calls require explicit --live."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.v2_schema_context import build_schema_context
from app.v2_text_to_sql import DeepSeekSQLBackend, run_text_to_sql

ROOT = Path(__file__).resolve().parents[1]


def get_question(case_id: str, path: Path) -> str:
    suite = json.loads(path.read_text(encoding="utf-8-sig"))
    for case in suite["cases"]:
        if case["id"] == case_id:
            if case["group"] != "dynamic_sql" or len(case["turns"]) != 1:
                raise ValueError("目前仅支持 D01–D06 单轮动态 SQL 场景")
            return case["turns"][0]["question"]
    raise ValueError("未知 case ID")


def main() -> None:
    parser = argparse.ArgumentParser(description="DataPilot V2 Stage 2B Text-to-SQL smoke")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--show-context", action="store_true", help="仅打印业务语义字典，不联网")
    choice.add_argument("--case-id", help="D01–D06 的某个 case ID")
    choice.add_argument("--question", help="自定义自然语言问题")
    parser.add_argument("--live", action="store_true", help="显式允许一次真实 LLM 请求")
    parser.add_argument("--db", type=Path, default=ROOT / "data/processed/olist.db")
    parser.add_argument("--catalog", type=Path, default=ROOT / "benchmarks/metric_catalog.json")
    args = parser.parse_args()
    if args.show_context:
        print(build_schema_context(args.catalog))
        return
    if not args.live:
        parser.error("真实生成 SQL 需要显式加 --live；若只想核对上下文，请用 --show-context")
    try:
        question = get_question(args.case_id, ROOT / "benchmarks/v1_cases.json") if args.case_id else args.question
        if not args.db.is_file():
            parser.error("本地 SQLite 数据库不存在，请核对 --db")
        backend = DeepSeekSQLBackend()
        response = run_text_to_sql(question, args.db, backend=backend, catalog_path=args.catalog)
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
