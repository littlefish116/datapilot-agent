"""Regression tests for V3.2 monthly follow-ups. No external LLM calls."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from app.v3_answer_policy import targeted_monthly_report_answer as focus
from scripts.install_v3_targeted_answer import install


ROWS = [
    {"purchase_month": "2016-09", "delivered_with_dates": 1, "late_orders": 1, "late_rate_pct": 100.0},
    {"purchase_month": "2016-10", "delivered_with_dates": 265, "late_orders": 2, "late_rate_pct": 0.75},
    {"purchase_month": "2018-03", "delivered_with_dates": 7003, "late_orders": 1328, "late_rate_pct": 18.96},
    {"purchase_month": "2018-04", "delivered_with_dates": 6798, "late_orders": 306, "late_rate_pct": 4.50},
]


def result(*, status="ok", rows=None, truncated=False):
    return {"tool": "business_report", "status": status,
            "data": {"rows": deepcopy(ROWS if rows is None else rows),
                     "truncated": truncated}, "message": None, "error": None}


def test_march_single_month_uses_correct_row_not_first_five():
    data = result()
    before = deepcopy(data)
    answer = focus("查询2018年3月的订单延期率", data, report_id="monthly_late_delivery")
    assert "2018年3月" in answer and "7,003" in answer and "1,328" in answer
    assert "18.96%" in answer and "2016" not in answer
    assert data == before


def test_april_followup_correct_exact_month():
    answer = focus("查询2018年4月的订单延期率", result(), report_id="monthly_late_delivery")
    assert "4.50%" in answer and "6,798" in answer and "306" in answer
    assert "2016" not in answer and "18.96%" not in answer


def test_no_matching_month_full_report_no_fake_claim():
    answer = focus("查询2019年4月的订单延期率", result(), report_id="monthly_late_delivery")
    assert "没有" in answer and "不等于该月不存在订单" in answer


def test_no_matching_month_truncated_does_not_assert_no_data():
    answer = focus("查询2019年4月的订单延期率", result(status="partial", truncated=True),
                   report_id="monthly_late_delivery")
    assert "截断" in answer and "无法确认" in answer
    assert "没有" not in answer


def test_partial_matching_month_explicit_warning():
    answer = focus("查询2018年4月的延期率", result(status="partial", truncated=True),
                   report_id="monthly_late_delivery")
    assert "4.50%" in answer and "截断" in answer


@pytest.mark.parametrize("question", [
    "查询每个月的订单延期率", "2018年3月和4月的延期率", "比较2018年3月与2018年4月的延期率",
    "查询2018年3月的同比变化", "2018年3月至2018年4月的延期率", "3月的延期率",
])
def test_not_single_month_falls_back(question):
    assert focus(question, result(), report_id="monthly_late_delivery") is None


def test_other_report_not_focused():
    assert focus("查询2018年4月的延期率", result(), report_id="top_states") is None


def test_sql_analysis_not_modified():
    data = result()
    data["tool"] = "sql_analysis"
    assert focus("查询2018年4月的延期率", data, report_id="monthly_late_delivery") is None


def test_risky_result_never_claims_success():
    data = result(status="needs_review")
    data["data"] = None
    assert focus("查询2018年4月的延期率", data, report_id="monthly_late_delivery") is None


def test_malformed_rate_never_claims_success():
    rows = deepcopy(ROWS)
    rows[3]["late_rate_pct"] = 80.0
    answer = focus("查询2018年4月延期率", result(rows=rows), report_id="monthly_late_delivery")
    assert "不一致" in answer and "80.00%" not in answer


def test_duplicate_month_never_claims_success():
    rows = deepcopy(ROWS)
    rows.append(deepcopy(rows[-1]))
    answer = focus("查询2018年4月延期率", result(rows=rows), report_id="monthly_late_delivery")
    assert "重复" in answer and "4.50%" not in answer


def test_installer_inserts_only_expected_lines_and_idempotent(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "app" / "v3_routing.py")
    # Current repo may already be installed; synthetic pre-patch file exercises install.
    original = source.read_text(encoding="utf-8")
    from scripts.install_v3_targeted_answer import NEW_IMPORT, INTEGRATION, NEW_ANSWER, OLD_ANSWER
    if NEW_IMPORT in original:
        original = original.replace(NEW_IMPORT + "\n", "")
        original = original.replace(INTEGRATION, "")
        original = original.replace(NEW_ANSWER, OLD_ANSWER)
    p = tmp_path / "v3_routing.py"
    backup = tmp_path / "v3_routing.py.bak"
    p.write_text(original, encoding="utf-8")
    assert "Updated" in install(p, backup)
    assert "Already" in install(p, backup)
    assert backup.read_text(encoding="utf-8") == original
    revised = p.read_text(encoding="utf-8")
    assert revised.count(NEW_IMPORT) == 1
    assert revised.count(INTEGRATION) == 1


def test_installer_refuses_unexpected_structure(tmp_path):
    p = tmp_path / "wrong.py"
    p.write_text("def f():\n  pass\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        install(p, tmp_path / "wrong.bak")
    assert not (tmp_path / "wrong.bak").exists()


def test_installer_preserves_crlf(tmp_path):
    from scripts.install_v3_targeted_answer import NEW_IMPORT, INTEGRATION, NEW_ANSWER, OLD_ANSWER
    src = (Path(__file__).resolve().parents[1] / "app" / "v3_routing.py").read_text(encoding="utf-8")
    if NEW_IMPORT in src:
        src = src.replace(NEW_IMPORT + "\n", "").replace(INTEGRATION, "").replace(NEW_ANSWER, OLD_ANSWER)
    p = tmp_path / "v3_routing.py"
    p.write_bytes(src.replace("\n", "\r\n").encode("utf-8"))
    install(p, tmp_path / "v3_routing.py.bak")
    data = p.read_bytes()
    assert b"\r\n" in data and b"\r\n" + NEW_IMPORT.encode() in data


@pytest.mark.skipif(importlib.util.find_spec("langgraph") is None, reason="LangGraph only available in user's project .venv")
def test_real_langgraph_two_turn_answers_not_first_five():
    import json
    from app.v3_conversation import V3SessionStore, run_v3_conversation_turn

    class RouteBackend:
        def invoke(self, messages):
            return {"content": json.dumps({"status": "route", "tool": "business_report",
                                           "arguments": {"report_id": "monthly_late_delivery"}}),
                    "usage": {"total_tokens": 0}}

    class ReportService:
        def business_report(self, report_id):
            return {"tool": "business_report", "status": "ok",
                    "data": {"rows": deepcopy(ROWS), "truncated": False},
                    "message": None, "error": None, "evidence": {"report_id": report_id}}

    backend, service = RouteBackend(), ReportService()
    store = V3SessionStore()
    first = run_v3_conversation_turn("查询2018年3月的订单延期率", store=store,
                                     conversation_id=None, backend=backend, service=service)
    second = run_v3_conversation_turn("那4月呢？", store=store,
                                      conversation_id=first["conversation_id"],
                                      backend=backend, service=service)
    assert "18.96%" in first["answer"]
    assert "4.50%" in second["answer"] and "2016" not in second["answer"]
    assert second["effective_question"] == "查询2018年4月的订单延期率"
    assert second["context_used"] is True
    assert second["tool_result"]["data"]["rows"] == ROWS
