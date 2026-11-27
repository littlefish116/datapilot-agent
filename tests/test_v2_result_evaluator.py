"""Offline tests for development-case scoring. No API or SQLGlot required."""
import unittest

from evaluation.v2_result_evaluator import compare_task_result, summarize


def response(rows, *, status="ok", truncated=False):
    return {"status": status, "generated_sql": "SELECT ...", "generation_ms": 10,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "execution": {"status": status, "rows": rows, "returned_rows": len(rows),
                          "truncated": truncated, "duration_ms": 5}}


def gold(rows, *, truncated=False):
    return {"status": "ok", "rows": rows, "matched_rows": len(rows), "truncated": truncated}


class V2ResultEvaluatorTests(unittest.TestCase):
    def test_d01_extra_gold_columns_not_required(self):
        r = response([{"payment_type": "credit_card", "payment_amount_brl": 901397.72, "share_pct": 80.43}])
        g = gold([{"payment_type": "credit_card", "orders_using_type": 5509, "amount_brl": 901397.72, "amount_share_pct": 80.43}])
        self.assertEqual(compare_task_result("D01", r, g)["verdict"], "pass")

    def test_row_order_is_irrelevant(self):
        r = response([{"payment_type":"voucher","payment_amount_brl":20,"share_pct":20},
                      {"payment_type":"credit_card","payment_amount_brl":80,"share_pct":80}])
        g = gold([{"payment_type":"credit_card","amount_brl":80,"amount_share_pct":80},
                  {"payment_type":"voucher","amount_brl":20,"amount_share_pct":20}])
        self.assertEqual(compare_task_result("D01", r, g)["verdict"], "pass")

    def test_d03_rounding(self):
        self.assertEqual(compare_task_result("D03", response([{"average_items_per_order":1.144795087819506}]),
                                            gold([{"avg_item_rows_per_order":1.1448}]))["verdict"], "pass")

    def test_wrong_amount_fails(self):
        s = compare_task_result("D02", response([{"avg_freight_per_order":22.3}]),
                                gold([{"avg_freight_per_order_brl":23.74}]))
        self.assertEqual(s["verdict"], "fail")
        self.assertIn("actual=22.3", " ".join(s["issues"]))

    def test_missing_group_fails(self):
        s = compare_task_result("D06", response([{"seller_state":"SP","delivered_item_value_brl":100}]),
                                gold([{"seller_state":"SP","item_value_brl":100},
                                      {"seller_state":"RJ","item_value_brl":10}]))
        self.assertEqual(s["verdict"], "fail")

    def test_duplicate_key_fails(self):
        s = compare_task_result("D01", response([{"payment_type":"credit_card","payment_amount_brl":10,"share_pct":10},
                                                  {"payment_type":"credit_card","payment_amount_brl":10,"share_pct":10}]),
                                gold([{"payment_type":"credit_card","amount_brl":10,"amount_share_pct":10},
                                      {"payment_type":"voucher","amount_brl":10,"amount_share_pct":10}]))
        self.assertEqual(s["verdict"], "fail")

    def test_truncated_result_fails(self):
        s = compare_task_result("D04", response([{"repeat_buyer_count":2997}], truncated=True),
                                gold([{"buyers_with_at_least_two_orders":2997}]))
        self.assertEqual(s["verdict"], "fail")

    def test_execution_failure_fails(self):
        r = response([], status="error")
        self.assertEqual(compare_task_result("D04", r, gold([{"buyers_with_at_least_two_orders":2997}]))["reason"], "execution_failure")

    def test_missing_gold_not_scored(self):
        s = compare_task_result("D04", response([{"repeat_buyer_count":2997}]), {"status": "error"})
        self.assertEqual(s["verdict"], "not_scored")

    def test_missing_required_column_fails(self):
        s = compare_task_result("D02", response([{"something_else":23.74}]),
                                gold([{"avg_freight_per_order_brl":23.74}]))
        self.assertEqual(s["verdict"], "fail")

    def test_non_finite_not_equal(self):
        s = compare_task_result("D02", response([{"avg_freight_per_order":float("nan")}]),
                                gold([{"avg_freight_per_order_brl":23.74}]))
        self.assertEqual(s["verdict"], "fail")

    def test_summary(self):
        r = response([{"repeat_buyer_count":2997}])
        score = compare_task_result("D04", r, gold([{"buyers_with_at_least_two_orders":2997}]))
        s = summarize([{"score":score,"response":r}])
        self.assertEqual(s["development_case_pass_rate"],1)
        self.assertEqual(s["total_token_usage"]["total_tokens"],15)
        self.assertEqual(s["execution_ok_cases"],1)


if __name__ == "__main__":
    unittest.main()
