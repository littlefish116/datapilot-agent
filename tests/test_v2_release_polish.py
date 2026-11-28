"""Offline release-polish regressions: no database, FastAPI or LLM needed."""
from __future__ import annotations

import unittest

from app.v2_response_policy import REVIEW_UNSUPPORTED, normalize_unsupported_message
from scripts.check_v2_release import check_sqlglot
from scripts.pin_v2_sqlglot import with_sqlglot_pin
from scripts.install_v2_release_polish import polish_readme, polish_runtime


class ReleasePolishTests(unittest.TestCase):
    def test_review_unsupported_corrected(self):
        msg = normalize_unsupported_message(
            "请统计各州平均评论评分", "无 reviews 表或评分字段"
        )
        self.assertEqual(msg, REVIEW_UNSUPPORTED)
        self.assertIn("order_reviews", msg)
        self.assertIn("未开放", msg)

    def test_review_english_question_corrected(self):
        self.assertEqual(normalize_unsupported_message("reviews rating", "No table"), REVIEW_UNSUPPORTED)

    def test_other_unsupported_preserved(self):
        self.assertEqual(normalize_unsupported_message("统计街道级定位", "未开放地理字段"), "未开放地理字段")

    def test_no_decision_conversion(self):
        self.assertEqual(normalize_unsupported_message("统计订单", None), None)

    def test_readme_idempotent(self):
        src = "# DataPilot\n\n## 1. 功能概览\n"
        first = polish_readme(src)
        self.assertIn("## 当前版本：V2 本地 Text-to-SQL 工作台", first)
        self.assertEqual(first, polish_readme(first))

    def test_runtime_injection_idempotent(self):
        src = ('from app.v2_text_to_sql import generate_sql_decision\n'
               'def f(decision, response):\n'
               '    if decision["status"] != "sql":\n'
               '        return response\n')
        changed = polish_runtime(src)
        self.assertIn('decision["status"] == "unsupported"', changed)
        self.assertEqual(changed, polish_runtime(changed))

    def test_runtime_refuses_unknown_structure(self):
        with self.assertRaises(ValueError):
            polish_runtime("unrelated module")

    def test_runtime_unsupported_path_without_llm_network(self):
        import json
        from app.v2_runtime import run_v2_query

        class FakeBackend:
            def invoke(self, messages):
                return {"content": json.dumps({
                    "status": "unsupported",
                    "message": "当前数据库没有 reviews 表",
                }, ensure_ascii=False), "usage": {"total_tokens": 5}}

        response = run_v2_query("统计各州订单评论评分", "unused.db", backend=FakeBackend())
        self.assertEqual(response["status"], "unsupported")
        self.assertEqual(response["message"], REVIEW_UNSUPPORTED)
        self.assertIsNone(response["execution"])
        self.assertFalse(response["automatic_repair"])

    def test_requirements_missing_sqlglot_pin(self):
        ok, _ = check_sqlglot("fastapi==0.165\n", "29.0.0")
        self.assertFalse(ok)

    def test_pin_new_dependency(self):
        self.assertEqual(with_sqlglot_pin("fastapi==1.0\n", "29.0.0"), "fastapi==1.0\nsqlglot==29.0.0\n")

    def test_pin_idempotent(self):
        existing = "fastapi==1.0\nsqlglot==29.0.0\n"
        self.assertEqual(with_sqlglot_pin(existing, "29.0.0"), existing)

    def test_pin_conflict_rejected(self):
        with self.assertRaises(ValueError):
            with_sqlglot_pin("sqlglot==28.0.0\n", "29.0.0")

    def test_requirements_accurate_pin(self):
        ok, _ = check_sqlglot("fastapi==0.165\nsqlglot==29.0.0\n", "29.0.0")
        self.assertTrue(ok)

    def test_requirements_wrong_pin(self):
        ok, _ = check_sqlglot("sqlglot==29.0.0\n", "30.0.0")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
