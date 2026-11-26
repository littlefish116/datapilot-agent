"""Offline V1 benchmark tests: standard library only, no DeepSeek or Olist CSV needed."""
from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest

from evaluation.v1_runner import (load_suite, run_reference, select_cases,
                                  tool_selection_match, validate_suite)


class BenchmarkDefinitionTests(unittest.TestCase):
    def test_case_count_and_groups(self):
        stats = validate_suite(load_suite())
        self.assertEqual(stats["cases"], 24)
        self.assertEqual(stats["turns"], 27)
        self.assertEqual(stats["fixed_report"], 8)
        self.assertEqual(stats["dynamic_sql"], 6)

    def test_reject_duplicate_ids(self):
        suite = copy.deepcopy(load_suite())
        suite["cases"][1]["id"] = suite["cases"][0]["id"]
        with self.assertRaisesRegex(ValueError, "重复"):
            validate_suite(suite)

    def test_reject_illegal_reference_path(self):
        suite = copy.deepcopy(load_suite())
        suite["cases"][0]["reference_sql"] = "../secret.sql"
        with self.assertRaisesRegex(ValueError, "允许目录"):
            validate_suite(suite)

    def test_selection_and_unknown_case(self):
        cases = load_suite()["cases"]
        self.assertEqual([x["id"] for x in select_cases(cases, ["F03", "D01"], 0)], ["F03", "D01"])
        self.assertEqual(len(select_cases(cases, [], 3)), 3)
        with self.assertRaisesRegex(ValueError, "不存在"):
            select_cases(cases, ["NOT_A_CASE"], 0)

    def test_tool_selection_does_not_score_answer(self):
        self.assertTrue(tool_selection_match(["query_business_report"], ["query_business_report"]))
        self.assertTrue(tool_selection_match(["query_business_report"], ["query_business_report", "query_business_report"]))
        self.assertFalse(tool_selection_match(["query_business_report"], ["predict_delivery_risk"]))
        self.assertFalse(tool_selection_match([], ["query_business_report"]))

    def test_reference_is_filtered_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sql_dir = root / "sql" / "queries"
            sql_dir.mkdir(parents=True)
            (sql_dir / "gold.sql").write_text("SELECT label, val FROM numbers ORDER BY label", encoding="utf-8")
            database = root / "toy.db"
            # closing 确保释放文件句柄；内层 with conn 负责提交事务。
            with closing(sqlite3.connect(database)) as conn:
                with conn:
                    conn.execute("CREATE TABLE numbers (label TEXT, val INTEGER)")
                    conn.executemany("INSERT INTO numbers VALUES (?,?)", [("a", 3), ("b", 5)])
            case = {"reference_sql": "sql/queries/gold.sql", "reference_filter": {"label": "b"}}
            result = run_reference(case, database, root)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["matched_rows"], 1)
            self.assertEqual(result["rows"], [{"label": "b", "val": 5}])

    def test_reference_missing_database_does_not_fabricate(self):
        case = {"reference_sql": "sql/queries/01_order_status.sql"}
        result = run_reference(case, Path("a_database_that_does_not_exist.sqlite"))
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
