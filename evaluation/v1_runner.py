"""DataPilot V1 benchmark runner (stdlib-only; never executes model-generated SQL).

Modes:
  validate   Offline schema/fixture validation. No data, API or token usage.
  references Execute authored gold SQL against a LOCAL, read-only SQLite database.
  live       Call the existing /api/chat V1 endpoint. May incur LLM API charges.

This program does not claim that tool-choice matching proves answer correctness.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "benchmarks" / "v1_cases.json"
DEFAULT_DB = ROOT / "data" / "processed" / "olist.db"
DEFAULT_OUT = ROOT / "reports" / "agent_eval_v1"
GROUPS = {"fixed_report", "prediction", "dynamic_sql", "multi_turn", "ambiguous", "security"}
BEHAVIORS = {"answer", "score", "clarify", "unsupported", "avoid_causal_claim", "reject_unsafe"}
TOOLS = {"query_business_report", "predict_delivery_risk"}
REFERENCE_PREFIXES = ("sql/queries/", "benchmarks/gold_sql/")


def load_suite(path: Path = DEFAULT_SUITE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValueError("评测集必须是包含 cases 数组的 JSON 对象")
    return data


def validate_suite(data: dict[str, Any], root: Path = ROOT) -> dict[str, int]:
    cases = data["cases"]
    if not cases:
        raise ValueError("评测集不能为空")
    ids: set[str] = set()
    groups = {group: 0 for group in sorted(GROUPS)}
    total_turns = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case 应为对象")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError(f"case id 不合法或重复: {cid}")
        ids.add(cid)
        group = case.get("group")
        if group not in GROUPS:
            raise ValueError(f"{cid}: 未知类别 {group}")
        groups[group] += 1
        if not isinstance(case.get("purpose"), str) or not case["purpose"].strip():
            raise ValueError(f"{cid}: 缺少 purpose")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{cid}: 至少需要一个 turn")
        for i, turn in enumerate(turns, 1):
            if not isinstance(turn, dict) or not isinstance(turn.get("question"), str) or not turn["question"].strip():
                raise ValueError(f"{cid}/{i}: 缺少 question")
            if turn.get("expected_behavior") not in BEHAVIORS:
                raise ValueError(f"{cid}/{i}: expected_behavior 不合法")
            expected_tools = turn.get("expected_tools")
            if not isinstance(expected_tools, list) or any(t not in TOOLS for t in expected_tools):
                raise ValueError(f"{cid}/{i}: expected_tools 只能引用现有 V1 工具")
            if not isinstance(turn.get("score_tool_selection"), bool):
                raise ValueError(f"{cid}/{i}: score_tool_selection 必须是布尔值")
            total_turns += 1
        sql_path = case.get("reference_sql")
        if sql_path is not None:
            if not isinstance(sql_path, str) or not sql_path.startswith(REFERENCE_PREFIXES):
                raise ValueError(f"{cid}: 参考 SQL 路径不在允许目录中")
            f = (root / sql_path).resolve()
            if not f.is_relative_to(root.resolve()) or not f.is_file():
                raise ValueError(f"{cid}: 缺少参考 SQL 文件: {sql_path}")
            filt = case.get("reference_filter", {})
            if not isinstance(filt, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in filt.items()):
                raise ValueError(f"{cid}: reference_filter 必须为字符串字典")
        elif "reference_filter" in case:
            raise ValueError(f"{cid}: reference_filter 需要 reference_sql")
    return {"cases": len(cases), "turns": total_turns, **groups}


def tool_selection_match(expected: list[str], observed: list[str]) -> bool:
    """A set match, deliberately ignoring repeated calls of the SAME tool."""
    return set(expected) == set(observed)


def run_reference(case: dict[str, Any], db_path: Path = DEFAULT_DB, root: Path = ROOT) -> dict[str, Any] | None:
    path = case.get("reference_sql")
    if not path:
        return None
    if not db_path.is_file():
        return {"status": "unavailable", "reason": "本地 SQLite 数据库不存在"}
    sql = (root / path).read_text(encoding="utf-8")
    try:
        db_uri = db_path.resolve().as_uri() + "?mode=ro"
        # sqlite3.Connection 的上下文管理器只处理事务，不自动关闭连接。
        # Windows 下保留文件句柄会导致 TemporaryDirectory 清理失败。
        with closing(sqlite3.connect(db_uri, uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            cur = conn.execute(sql)
            all_rows = [dict(r) for r in cur.fetchall()]
        filters = case.get("reference_filter", {})
        filtered = [r for r in all_rows if all(str(r.get(k)) == value for k, value in filters.items())]
        # A reference result supports HUMAN judging; it is not an automatic natural-language score.
        return {"status": "ok", "sql_path": path, "filter": filters,
                "matched_rows": len(filtered), "rows": filtered[:30], "truncated": len(filtered) > 30}
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"status": "error", "sql_path": path, "error_type": type(exc).__name__, "message": str(exc)}


def request_json(url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"},
                      method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2_000_000)
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        error_message = exc.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_message}") from exc
    except URLError as exc:
        raise RuntimeError(f"连接失败: {exc.reason}") from exc


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL,
                                       text=True, timeout=3).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def select_cases(cases: list[dict[str, Any]], selected_ids: list[str], max_cases: int) -> list[dict[str, Any]]:
    known = {case["id"] for case in cases}
    unknown = sorted(set(selected_ids) - known)
    if unknown:
        raise ValueError(f"不存在这些 case_id: {', '.join(unknown)}")
    selected = [case for case in cases if not selected_ids or case["id"] in selected_ids]
    return selected[:max_cases] if max_cases > 0 else selected


def run_live(cases: list[dict[str, Any]], base_url: str, timeout: float, db_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_url = base_url.rstrip("/")
    health = request_json(base_url + "/health", None, min(5.0, timeout))
    if health.get("status") != "ok":
        raise RuntimeError("本地 /health 未返回 status=ok")
    records: list[dict[str, Any]] = []
    completed, matched, eligible, calls = 0, 0, 0, 0
    for case in cases:
        cid = case["id"]
        conversation_id = None   # New session per CASE; turns in same case reuse it.
        reference = run_reference(case, db_path)
        for index, turn in enumerate(case["turns"], 1):
            start = perf_counter()
            record = {"case_id": cid, "group": case["group"], "turn": index,
                      "question": turn["question"], "expected_behavior": turn["expected_behavior"],
                      "expected_tools": turn["expected_tools"], "score_tool_selection": turn["score_tool_selection"],
                      "reference": reference, "answer": None, "used_tools": [],
                      "tool_selection_match": None, "manual_answer_correct": None,
                      "manual_behavior_correct": None, "error": None, "latency_s": None}
            try:
                payload = {"question": turn["question"], "conversation_id": conversation_id}
                response = request_json(base_url + "/api/chat", payload, timeout)
                if not isinstance(response, dict):
                    raise RuntimeError("返回值不是 JSON 对象")
                cid_returned = response.get("conversation_id")
                if not isinstance(cid_returned, str) or not cid_returned:
                    raise RuntimeError("响应缺少 conversation_id")
                conversation_id = cid_returned
                record["answer"] = str(response.get("answer", ""))
                used = response.get("used_tools", [])
                if not isinstance(used, list) or not all(isinstance(x, str) for x in used):
                    raise RuntimeError("used_tools 格式错误")
                record["used_tools"] = used
                completed += 1
                calls += len(used)
                if turn["score_tool_selection"]:
                    result = tool_selection_match(turn["expected_tools"], used)
                    record["tool_selection_match"] = result
                    eligible += 1
                    matched += int(result)
            except (RuntimeError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
                record["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
                # Failed turn invalidates dependent follow-up context. Continue only for isolated cases.
                records.append(record)
                break
            finally:
                record["latency_s"] = round(perf_counter() - start, 3)
            records.append(record)
        print(f"{cid}: {sum(1 for x in records if x['case_id']==cid and x['error'] is None)}/{len(case['turns'])} turns succeeded")
    summary = {"evaluated_cases": len(cases), "planned_turns": sum(len(c["turns"]) for c in cases),
               "recorded_turns": len(records), "api_success_turns": completed,
               "tool_selection_eligible_turns": eligible, "tool_selection_correct_turns": matched,
               "tool_selection_accuracy": round(matched / eligible, 4) if eligible else None,
               "total_observed_tool_calls": calls,
               "answer_correctness": None, "behavior_correctness": None,
               "token_usage": None, "token_note": "当前 /api/chat 不返回用量，不能声称已测 Token 消耗",
               "warning": "used_tools仅包含工具名称，未含调用参数；工具命中率不能替代SQL或回答正确率。人工标注列仍需填写。"}
    latencies = [r["latency_s"] for r in records if r["error"] is None]
    summary["average_successful_turn_latency_s"] = round(sum(latencies)/len(latencies), 3) if latencies else None
    return records, summary


def write_artifacts(directory: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / "results.jsonl").open("w", encoding="utf-8") as file:
        for row in records:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (directory / "manual_review.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["case_id", "group", "turn", "question", "expected_behavior",
                                                       "expected_tools", "observed_tools", "tool_selection_match",
                                                       "reference_status", "answer", "answer_correct_0_1", "behavior_correct_0_1", "notes"])
        writer.writeheader()
        for row in records:
            writer.writerow({"case_id": row["case_id"], "group": row["group"], "turn": row["turn"],
                             "question": row["question"], "expected_behavior": row["expected_behavior"],
                             "expected_tools": ",".join(row["expected_tools"]),
                             "observed_tools": ",".join(row["used_tools"]),
                             "tool_selection_match": row["tool_selection_match"],
                             "reference_status": (row["reference"] or {}).get("status", "not_applicable"),
                             "answer": row["answer"], "answer_correct_0_1": "", "behavior_correct_0_1": "", "notes": row["error"] or ""})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataPilot V1 benchmark: validate / references / live")
    parser.add_argument("--mode", choices=["validate", "references", "live"], default="validate")
    parser.add_argument("--case-id", action="append", default=[], help="仅运行指定 ID，可重复传入")
    parser.add_argument("--max-cases", type=int, default=0, help="最多运行前 N 道题；0=全部")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="本地 FastAPI 服务根 URL")
    parser.add_argument("--timeout", type=float, default=90.0, help="单次 HTTP 请求最长秒数")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="本地 SQLite 路径")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT, help="本地评测产物目录")
    args = parser.parse_args(argv)
    if args.max_cases < 0 or args.timeout <= 0:
        parser.error("--max-cases 不能为负数，--timeout 必须大于 0")
    try:
        data = load_suite()
        counts = validate_suite(data)
        cases = select_cases(data["cases"], args.case_id, args.max_cases)
        if args.mode == "validate":
            print(json.dumps({"suite_version": data.get("version"), "suite_counts": counts,
                              "selected_case_ids": [x["id"] for x in cases],
                              "note": "仅校验题库和文件结构；没有调用 LLM，也没有验证自然语言回答。"}, ensure_ascii=False, indent=2))
            return 0
        if args.mode == "references" and not args.db.is_file():
            raise RuntimeError(f"找不到数据库: {args.db}。先按 README 重建 Olist 数据库。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = args.output_dir / f"{stamp}_{uuid4().hex[:6]}"
        if args.mode == "references":
            refs = [{"case_id": x["id"], "reference": run_reference(x, args.db)} for x in cases]
            dest.mkdir(parents=True)
            (dest / "references.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            failures = [r for r in refs if r["reference"] is not None and r["reference"]["status"] != "ok"]
            print(f"参考 SQL：{len(refs)-len(failures)} 个 case 结果已保存至 {dest / 'references.json'}；异常 {len(failures)} 个")
            return 2 if failures else 0
        print("注意：live 模式会真实调用本地 Agent，可能产生 DeepSeek API 费用。")
        records, summary = run_live(cases, args.base_url, args.timeout, args.db)
        summary.update({"suite_version": data.get("version"), "git_commit": git_commit(),
                        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                        "base_url": args.base_url, "mode": "live"})
        write_artifacts(dest, records, summary)
        print(f"结果保存于 {dest}；工具选择准确率(仅可自动评分题目)：{summary['tool_selection_accuracy']}")
        print("下一步：人工审核 manual_review.csv 的回答正确性和拒绝行为；不要把工具选择率当作最终准确率。")
        return 0 if summary["api_success_turns"] == summary["planned_turns"] else 2
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"评测失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
