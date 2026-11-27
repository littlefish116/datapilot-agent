"""Offline tests. No network, no DeepSeek, no SQLGlot dependency."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evaluation import v2_holdout_benchmark as h


class HoldoutBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = h.load_suite()
        cls.byid = {c["id"]: c for c in cls.cases}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "fixture.db"
        con = sqlite3.connect(self.db)
        con.executescript("""
        CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT, order_status TEXT,
          order_purchase_timestamp TEXT, order_delivered_customer_date TEXT, order_estimated_delivery_date TEXT);
        CREATE TABLE customers(customer_id TEXT PRIMARY KEY, customer_unique_id TEXT, customer_state TEXT);
        CREATE TABLE order_items(order_id TEXT, order_item_id INTEGER, product_id TEXT, seller_id TEXT,
          price REAL, freight_value REAL);
        CREATE TABLE order_payments(order_id TEXT, payment_sequential INTEGER, payment_type TEXT, payment_value REAL);
        CREATE TABLE products(product_id TEXT PRIMARY KEY, product_category_name TEXT);
        CREATE TABLE category_translation(product_category_name TEXT, product_category_name_english TEXT);
        CREATE TABLE sellers(seller_id TEXT, seller_state TEXT);
        INSERT INTO customers VALUES ('c1','u1','SP'), ('c2','u2','RJ'), ('c3','u1','SP'), ('c4','u3','MG');
        INSERT INTO products VALUES ('p1','brinquedos'),('p2','esportes');
        INSERT INTO category_translation VALUES ('brinquedos','toys'),('esportes','sports');
        INSERT INTO sellers VALUES ('s1','SP');
        """)
        base = [
            ("nov", "c1", "2017-11-15", "2017-11-24", "2017-11-25", "credit_card", "p1", 10),
            ("dec", "c1", "2017-12-10", "2017-12-25", "2017-12-24", "boleto", "p2", 20),
            ("jan", "c2", "2018-01-16", "2018-01-24", "2018-01-25", "voucher", "p1", 30),
            ("feb", "c3", "2018-02-16", "2018-02-27", "2018-02-25", "credit_card", "p2", 40),
            ("mar", "c4", "2018-03-16", "2018-03-22", "2018-03-25", "boleto", "p1", 50),
            ("apr", "c1", "2018-04-16", "2018-04-29", "2018-04-25", "credit_card", "p1", 60),
            ("may", "c2", "2018-05-16", "2018-05-22", "2018-05-25", "credit_card", "p2", 70),
            ("jun", "c3", "2018-06-16", "2018-06-22", "2018-06-25", "voucher", "p1", 80),
        ]
        for id_, customer, purchased, delivered, estimated, payment, product, value in base:
            con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)", (id_, customer, "delivered", purchased, delivered, estimated))
            con.execute("INSERT INTO order_items VALUES (?,?,?,?,?,?)", (id_, 1, product, "s1", value, 3))
            con.execute("INSERT INTO order_payments VALUES (?,?,?,?)", (id_, 1, payment, value+3))
        # H08 requires >=300 orders in a single state; all also feed H06 and H07.
        con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", [
            (f"bulk{i}", "c1", "delivered", "2018-04-02", "2018-04-10", "2018-04-11") for i in range(310)
        ])
        con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", [
            (f"bulk{i}", 1, "p1", "s1", 1.0, 0.1) for i in range(310)
        ])
        con.commit()
        con.close()

    def _gold(self, cid: str) -> dict:
        return h.execute_gold(self.byid[cid], self.db)

    def _response(self, cid: str) -> dict:
        case = self.byid[cid]
        if case["category"] != "sql":
            return {"status": case["category"], "generated_sql": None, "execution": None,
                    "message": "请明确具体指标或时间范围" if case["category"] == "clarify" else "此字段未开放，无法查询"}
        gold = self._gold(cid)
        return {"status": "ok", "generated_sql": "SELECT COUNT(*) FROM orders", "execution": gold,
                "usage": {"total_tokens": 10}, "generation_ms": 100}

    def test_suite_frozen_and_12_cases(self):
        self.assertEqual(len(self.cases), 12)
        self.assertEqual([c["category"] for c in self.cases].count("sql"), 8)
        self.assertEqual([c["category"] for c in self.cases].count("clarify"), 2)
        self.assertEqual([c["category"] for c in self.cases].count("unsupported"), 2)

    def test_all_eight_gold_sql_execute(self):
        for i in range(1, 9):
            with self.subTest(case=i):
                gold = self._gold(f"H{i:02d}")
                self.assertTrue(gold["rows"])
                self.assertFalse(gold["truncated"])

    def test_gold_connection_closed_explicitly(self):
        """Windows must be able to delete the temporary fixture DB after Gold evaluation."""
        real_connect = sqlite3.connect
        captured = []

        def record_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            captured.append(connection)
            return connection

        with patch.object(h.sqlite3, "connect", side_effect=record_connect):
            self._gold("H01")
        self.assertEqual(len(captured), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

    def test_gold_sql_scalar(self):
        self.assertEqual(self._gold("H01")["rows"][0]["delivered_orders"], 1)

    def test_gold_sql_topk(self):
        self.assertEqual(self._gold("H08")["rows"][0]["customer_state"], "SP")
        self.assertEqual(self._gold("H08")["rows"][0]["delivered_orders"], 312)

    def test_all_eight_exact_gold_pass(self):
        for i in range(1, 9):
            cid = f"H{i:02d}"
            self.assertEqual(h.score_case(self.byid[cid], self._response(cid), self._gold(cid))["verdict"], "pass")

    def test_group_reordered_still_passes(self):
        response = self._response("H06")
        response["execution"]["rows"].reverse()
        self.assertEqual(h.score_case(self.byid["H06"], response, self._gold("H06"))["verdict"], "pass")

    def test_group_value_mismatch_fails(self):
        response = self._response("H06")
        response["execution"]["rows"][0]["late_rate_pct"] += 3
        self.assertEqual(h.score_case(self.byid["H06"], response, self._gold("H06"))["reason"], "value_mismatch")

    def test_missing_group_fails(self):
        response = self._response("H06")
        response["execution"]["rows"].pop()
        response["execution"]["returned_rows"] -= 1
        self.assertEqual(h.score_case(self.byid["H06"], response, self._gold("H06"))["reason"], "row_count_mismatch")

    def test_renamed_known_alias_passes(self):
        response = self._response("H01")
        value = response["execution"]["rows"][0].pop("delivered_orders")
        response["execution"]["rows"][0]["order_count"] = value
        response["execution"]["columns"] = ["order_count"]
        self.assertEqual(h.score_case(self.byid["H01"], response, self._gold("H01"))["verdict"], "pass")

    def test_unfamiliar_alias_requires_review(self):
        response = self._response("H01")
        value = response["execution"]["rows"][0].pop("delivered_orders")
        response["execution"]["rows"][0]["mysterious_name"] = value
        response["execution"]["columns"] = ["mysterious_name"]
        result = h.score_case(self.byid["H01"], response, self._gold("H01"))
        self.assertEqual(result["verdict"], "review")
        self.assertTrue(result["manual_review_required"])

    def test_truncation_fails(self):
        response = self._response("H01")
        response["execution"]["truncated"] = True
        self.assertEqual(h.score_case(self.byid["H01"], response, self._gold("H01"))["reason"], "truncated")

    def test_sql_execution_fail(self):
        response = self._response("H01")
        response["status"] = "error"
        self.assertEqual(h.score_case(self.byid["H01"], response, self._gold("H01"))["reason"], "execution_failure")

    def test_empty_or_bad_rows_fail(self):
        response = self._response("H01")
        response["execution"]["rows"] = "bad"
        self.assertEqual(h.score_case(self.byid["H01"], response, self._gold("H01"))["reason"], "invalid_rows")

    def test_clarify_format_pass_requires_human(self):
        result = h.score_case(self.byid["H09"], self._response("H09"))
        self.assertEqual(result["verdict"], "behavior_pass")
        self.assertTrue(result["manual_review_required"])

    def test_unsupported_format_pass_requires_human(self):
        result = h.score_case(self.byid["H12"], self._response("H12"))
        self.assertEqual(result["verdict"], "behavior_pass")
        self.assertTrue(result["manual_review_required"])

    def test_sql_instead_of_clarify_fails(self):
        res = self._response("H09")
        res["status"] = "ok"
        res["generated_sql"] = "SELECT 1"
        self.assertEqual(h.score_case(self.byid["H09"], res)["reason"], "wrong_behavior")

    def test_bool_vs_numeric_not_equal(self):
        self.assertFalse(h._eq(True, 1, 0))
        self.assertFalse(h._eq(float("nan"), 1, 0))

    def test_summary_separates_accuracy_and_behavior(self):
        records = []
        for c in self.cases:
            response = self._response(c["id"])
            score = h.score_case(c, response, self._gold(c["id"]) if c["category"] == "sql" else None)
            records.append({"category": c["category"], "response": response, "score": score})
        summary = h.summarize(records)
        self.assertEqual(summary["sql_correct"], 8)
        self.assertEqual(summary["behavior_format_correct_pending_reason_review"], 4)
        self.assertEqual(summary["needs_manual_review"], 4)

    def test_validate_has_no_api_calls(self):
        with io.StringIO() as output, redirect_stdout(output):
            code = h.main(["--mode", "validate"])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["llm_calls"], 0)
        self.assertEqual(result["distribution"], {"sql": 8, "clarify": 2, "unsupported": 2})

    def test_replay_offline_on_synthetic_db(self):
        root = Path(self.temp.name)
        (root / "benchmarks/holdout_gold_sql").mkdir(parents=True)
        (root / "benchmarks/v2_holdout_cases.json").write_bytes(h.SUITE.read_bytes())
        (root / "benchmarks/metric_catalog.json").write_text("{}", encoding="utf-8")
        for c in self.cases:
            if c["category"] == "sql":
                src = h.ROOT / c["gold_sql"]
                shutil.copy(src, root / c["gold_sql"])
        incoming = root / "old_responses"
        incoming.mkdir()
        for c in self.cases:
            h.json_save(incoming / f"{c['id']}.json", self._response(c["id"]))
        with patch.object(h, "ROOT", root):
            with io.StringIO() as output, redirect_stdout(output):
                code = h.main(["--mode", "replay", "--suite", str(root / "benchmarks/v2_holdout_cases.json"),
                               "--catalog", str(root / "benchmarks/metric_catalog.json"),
                               "--db", str(self.db), "--response-dir", str(incoming),
                               "--out", str(root / "reports")])
        self.assertEqual(code, 0)
        reports = list((root / "reports").glob("*/summary.json"))
        self.assertEqual(len(reports), 1)
        summary = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(summary["sql_correct"], 8)
        self.assertEqual(summary["behavior_format_correct_pending_reason_review"], 4)
        self.assertEqual(len(list((reports[0].parent / "responses").glob("*.json"))), 12)


if __name__ == "__main__":
    unittest.main()
