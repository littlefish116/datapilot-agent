"""No DeepSeek key, network, SQLite data, or model weights required."""
import json
import tempfile
import unittest
from pathlib import Path

from app.v2_schema_context import build_schema_context
from app.v2_text_to_sql import (
    MAX_QUESTION_CHARS,
    generate_sql_decision,
    parse_model_json,
    run_text_to_sql,
)
from evaluation.v2_text_to_sql_smoke import get_question


class FakeBackend:
    def __init__(self, content, usage=None, fail=False):
        self.content = content
        self.usage = usage
        self.fail = fail
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("simulated external error")
        return {"content": self.content, "usage": self.usage}


class V2TextToSQLTests(unittest.TestCase):
    def test_context_allows_actual_policy_tables(self):
        data = json.loads(build_schema_context())
        self.assertEqual(data["dialect"], "sqlite")
        self.assertIn("orders", data["allowed_tables"])
        self.assertIn("order_items", data["allowed_tables"])
        self.assertIn("order_payments", data["allowed_tables"])
        self.assertIn("customer_unique_id", data["allowed_tables"]["customers"]["columns"])
        self.assertNotIn("geolocation", data["allowed_tables"])
        self.assertNotIn("order_reviews", data["allowed_tables"])

    def test_context_filters_unauthorized_relationship(self):
        data = json.loads(build_schema_context())
        self.assertTrue(all(x["left"] in data["allowed_tables"] and x["right"] in data["allowed_tables"] for x in data["joins"]))
        self.assertEqual(len(data["metrics"]), 6)
        self.assertIn("订单商品、订单支付均是一对多", " ".join(data["hard_rules"]))

    def test_json_sql(self):
        obj = parse_model_json('{"status":"sql","sql":"SELECT COUNT(*) FROM orders"}')
        self.assertEqual(obj["status"], "sql")
        self.assertEqual(obj["sql"], "SELECT COUNT(*) FROM orders")

    def test_json_code_fence(self):
        obj = parse_model_json('```json\n{"status":"clarify","message":"需要哪一年？"}\n```')
        self.assertEqual(obj["status"], "clarify")

    def test_refuse_invalid_json_and_extra_fields(self):
        for content in ["SELECT 1", "{}", "[]", '{"status":"sql","sql":"SELECT 1","note":"x"}', '{"status":"sql","sql":" "}']:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    parse_model_json(content)

    def test_generate_once_with_schema_and_question(self):
        backend = FakeBackend('{"status":"sql","sql":"SELECT COUNT(*) AS n FROM orders"}', usage={"total_tokens": 88})
        result = generate_sql_decision("多少订单？", backend=backend)
        self.assertEqual(result["status"], "sql")
        self.assertEqual(result["usage"]["total_tokens"], 88)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1]["content"], "多少订单？")
        self.assertIn("customer_unique_id", backend.calls[0][0]["content"])

    def test_invalid_question_no_api_call(self):
        backend = FakeBackend("{}")
        for q in ["", " " * 5, "x" * (MAX_QUESTION_CHARS + 1)]:
            result = generate_sql_decision(q, backend=backend)
            self.assertEqual(result["error"]["code"], "invalid_question")
        self.assertEqual(backend.calls, [])

    def test_clarify_never_executes(self):
        backend = FakeBackend('{"status":"clarify","message":"请说明月份"}')
        def forbidden_executor(sql, path):
            self.fail("clarify 不应执行 SQL")
        result = run_text_to_sql("增长率？", "unused.db", backend=backend, executor=forbidden_executor)
        self.assertEqual(result["status"], "clarify")
        self.assertIsNone(result["execution"])

    def test_backend_failure_classified(self):
        backend = FakeBackend("", fail=True)
        result = run_text_to_sql("订单量", "unused.db", backend=backend)
        self.assertEqual(result["error"]["code"], "llm_api_error")
        self.assertIsNone(result["execution"])

    def test_malformed_model_output_rejected(self):
        backend = FakeBackend("I think you should run SELECT * FROM orders")
        result = run_text_to_sql("订单量", "unused.db", backend=backend)
        self.assertEqual(result["error"]["code"], "invalid_llm_output")
        self.assertIsNone(result["execution"])

    def test_pipeline_passes_sql_to_only_executor(self):
        seen = []
        backend = FakeBackend('{"status":"sql","sql":"SELECT COUNT(*) AS n FROM orders"}')
        def executor(sql, path):
            seen.append((sql, path))
            return {"status":"ok", "rows":[{"n":3}], "error": None}
        result = run_text_to_sql("订单有多少？", "some.db", backend=backend, executor=executor)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["execution"]["rows"], [{"n": 3}])
        self.assertEqual(seen, [("SELECT COUNT(*) AS n FROM orders", "some.db")])

    def test_executor_refusal_not_overridden(self):
        backend = FakeBackend('{"status":"sql","sql":"DELETE FROM orders"}')
        def executor(sql, path):
            return {"status":"error","error":{"code":"unsafe_sql","message":"blocked"}}
        result = run_text_to_sql("清空订单", "some.db", backend=backend, executor=executor)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "unsafe_sql")

    def test_catalog_failure_no_api_call(self):
        backend = FakeBackend('{"status":"sql","sql":"SELECT 1"}')
        with tempfile.TemporaryDirectory() as temp:
            result = generate_sql_decision("订单量", backend=backend, catalog_path=Path(temp)/"does_not_exist.json")
        self.assertEqual(result["error"]["code"], "invalid_catalog")
        self.assertEqual(backend.calls, [])

    def test_case_lookup(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "benchmarks/v1_cases.json"
        question = get_question("D01", path)
        self.assertIn("支付方式", question)
        with self.assertRaises(ValueError):
            get_question("F01", path)


if __name__ == "__main__":
    unittest.main()
