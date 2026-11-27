"""Offline Hard8 tests; no model calls and no SQLGlot dependency.

The full safe executor is independently verified by Stage 2A and the user's
Windows end-to-end `--mode live` / `--mode replay` runs.
"""
from __future__ import annotations

from contextlib import closing, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from evaluation import v2_hard_benchmark as hard
from evaluation import v2_holdout_benchmark as holdout
from evaluation.v2_hard_fixture import create_hard_fixture


class HardBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = hard.load_cases()
        cls.byid = {c["id"]: c for c in cls.cases}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = create_hard_fixture(Path(self.temp.name) / "sample.db")
        self.out = Path(self.temp.name) / "out"

    def gold(self, cid):
        return holdout.execute_gold(self.byid[cid], self.db)

    def response(self, cid):
        case = self.byid[cid]
        return {"status": "ok", "question": case["question"],
                "generated_sql": (hard.ROOT / case["gold_sql"]).read_text(encoding="utf-8-sig"),
                "generation_ms": 50, "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                "execution": self.gold(cid), "error": None}

    def test_fixture_file_can_be_deleted_after_gold(self):
        self.gold("X01")
        p = self.db.with_name("renamed.db")
        self.db.rename(p)
        p.rename(self.db)

    def test_fixture_refuses_overwrite(self):
        with self.assertRaises(FileExistsError):
            create_hard_fixture(self.db)

    def test_suite_has_eight_new_sql_cases(self):
        self.assertEqual([c["id"] for c in self.cases], hard.IDS)
        self.assertTrue(all(c["category"] == "sql" for c in self.cases))

    def test_no_old_gold_path_is_reused(self):
        self.assertTrue(all("holdout_gold_sql" not in c["gold_sql"] for c in self.cases))

    def test_all_gold_execute_on_synthetic(self):
        for cid in hard.IDS:
            with self.subTest(cid=cid):
                result = self.gold(cid)
                self.assertEqual(result["status"], "ok")
                self.assertGreaterEqual(len(result["rows"]), 1)
                self.assertFalse(result["truncated"])

    def test_invalid_nonnull_dates_excluded(self):
        self.assertEqual(self.gold("X01")["rows"], [
            {"eligible_orders": 2, "late_rate_pct": 50.0}
        ])

    def test_fanout_gold_no_duplicate_sums(self):
        self.assertEqual(self.gold("X02")["rows"], [
            {"item_amount_brl": 214.0, "payment_amount_brl": 247.0}
        ])

    def test_zero_denominator_returns_null(self):
        self.assertEqual(self.gold("X03")["rows"], [
            {"eligible_orders": 0, "late_rate_pct": None}
        ])

    def test_unique_customer_key_across_customer_ids(self):
        self.assertEqual(self.gold("X04")["rows"], [
            {"repeat_buyers": 2, "repeat_buyer_orders": 7}
        ])

    def test_top_three_amount_and_all_types_denominator(self):
        self.assertEqual(self.gold("X05")["rows"], [
            {"payment_type": "credit_card", "payment_amount_brl": 65.0, "payment_share_pct": 59.09},
            {"payment_type": "boleto", "payment_amount_brl": 30.0, "payment_share_pct": 27.27},
            {"payment_type": "voucher", "payment_amount_brl": 15.0, "payment_share_pct": 13.64},
        ])

    def test_order_level_payment_count_exactly_two(self):
        self.assertEqual(self.gold("X07")["rows"], [
            {"two_payment_orders": 1, "avg_payment_per_order_brl": 50.0}
        ])

    def test_monthly_averages_preaggregated(self):
        self.assertEqual(self.gold("X08")["rows"], [
            {"purchase_month": "2018-04", "eligible_orders": 3, "avg_item_amount_brl": 31.67, "avg_payment_amount_brl": 36.67},
            {"purchase_month": "2018-05", "eligible_orders": 3, "avg_item_amount_brl": 23.0, "avg_payment_amount_brl": 26.33},
            {"purchase_month": "2018-06", "eligible_orders": 2, "avg_item_amount_brl": 25.0, "avg_payment_amount_brl": 29.0},
        ])

    def test_exact_gold_output_scores_pass_all_eight(self):
        for cid in hard.IDS:
            with self.subTest(cid=cid):
                self.assertEqual(hard.score(self.byid[cid], self.response(cid), self.gold(cid))["verdict"], "pass")

    def test_ordered_topk_reordered_fails(self):
        response = self.response("X05")
        response["execution"]["rows"].reverse()
        self.assertEqual(hard.score(self.byid["X05"], response, self.gold("X05"))["reason"], "order_mismatch")

    def test_unknown_alias_is_review_not_pass(self):
        response = self.response("X01")
        row = response["execution"]["rows"][0]
        row["unexpected_label"] = row.pop("eligible_orders")
        response["execution"]["columns"][0] = "unexpected_label"
        result = hard.score(self.byid["X01"], response, self.gold("X01"))
        self.assertEqual(result["verdict"], "review")

    def test_clean_pass_edge_fail_is_not_success(self):
        a, b = {"verdict": "pass"}, {"verdict": "fail"}
        self.assertEqual(hard._combined(a, b), ("fail", "clean_pass_edge_fail"))

    def test_review_never_counts_as_pass(self):
        self.assertEqual(hard._combined({"verdict": "review"}, {"verdict": "pass"})[0], "review")

    def test_validate_no_network_and_no_database_needed(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = hard.main(["--mode", "validate", "--db", str(self.db.with_name("missing.db"))])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["sql_count"], 8)

    def test_gold_mode_checks_both_database_versions(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = hard.main(["--mode", "gold", "--db", str(self.db), "--out", str(self.out)])
        self.assertEqual(result, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["gold_real_ok"], 8)
        self.assertEqual(summary["gold_edge_ok"], 8)
        self.assertTrue((Path(summary["run_dir"]) / "gold_edge" / "X01.json").is_file())

    def test_live_requires_explicit_consent_flag(self):
        with self.assertRaises(SystemExit) as err, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            hard.main(["--mode", "live", "--db", str(self.db)])
        self.assertEqual(err.exception.code, 2)

    def test_source_replay_rejects_tampered_suite_hash(self):
        resp = Path(self.temp.name) / "old" / "responses"
        resp.mkdir(parents=True)
        h = hard.manifest_hashes(self.cases, hard.SUITE, hard.CATALOG)
        old = {**h, "suite_sha256": "tampered"}
        (resp.parent / "run_manifest.json").write_text(
            json.dumps({"mode": "live", "hashes": old, "case_ids": hard.IDS}), encoding="utf-8")
        with self.assertRaises(ValueError):
            hard._verify_replay_source(resp, h)

    def test_source_replay_detects_modified_raw_response(self):
        resp = Path(self.temp.name) / "previous" / "responses"
        resp.mkdir(parents=True)
        hashes = hard.manifest_hashes(self.cases, hard.SUITE, hard.CATALOG)
        (resp.parent / "run_manifest.json").write_text(json.dumps({
            "mode": "live", "hashes": hashes, "case_ids": hard.IDS}), encoding="utf-8")
        recorded = {}
        for cid in hard.IDS:
            f = resp / f"{cid}.json"
            f.write_text(json.dumps(self.response(cid)), encoding="utf-8")
            recorded[cid] = hard._hash(f)
        (resp.parent / "response_sha256.json").write_text(json.dumps(recorded), encoding="utf-8")
        hard._verify_replay_source(resp, hashes)
        (resp / "X04.json").write_text('{"status":"hacked"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            hard._verify_replay_source(resp, hashes)

    def test_sql_generated_once_on_both_datasets_without_regeneration(self):
        source = self.response("X01")
        sql_seen = []
        def fake_execute(sql, db):
            sql_seen.append(sql)
            return self.gold("X01")
        module = types.SimpleNamespace(execute_safe_sql=fake_execute)
        with patch.dict(sys.modules, {"app.v2_safe_sql": module}):
            edge = hard._edge_response(source, self.db)
        self.assertEqual(sql_seen, [source["generated_sql"]])
        self.assertEqual(edge["status"], "ok")

    def test_summary_preserves_two_denominators(self):
        records = []
        for case in self.cases:
            raw = self.response(case["id"])
            records.append({"response": raw, "edge_response": raw,
                            "clean_score": {"verdict": "pass"}, "edge_score": {"verdict": "pass"},
                            "combined_verdict": "pass", "combined_reason": "both_datasets_match"})
        one = hard.summary_of(records)
        self.assertEqual(one["clean_auto_pass"], 8)
        self.assertEqual(one["edge_auto_pass"], 8)
        self.assertEqual(one["both_auto_pass"], 8)
        self.assertEqual(one["usage"]["total_tokens"], 8*140)


if __name__ == "__main__":
    unittest.main()
