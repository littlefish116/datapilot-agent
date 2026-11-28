"""V3.1B deterministic routing tests: fake LLM, fake adapters, no network."""
import json

import pytest

from app.v3_routing import (
    RoutingError, build_v3_graph, decide_tool, parse_route_json, run_v3_turn,
)


class Backend:
    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"content": json.dumps(self.decision, ensure_ascii=False),
                "usage": {"total_tokens": 10}}


class Service:
    def __init__(self, *, status="ok"):
        self.calls = []
        self.status = status

    def sql_analysis(self, question):
        self.calls.append(("sql_analysis", question))
        return {"tool": "sql_analysis", "status": self.status,
                "data": {"rows": [{"total": 7003}], "truncated": False} if self.status == "ok" else None,
                "message": "需复核" if self.status == "needs_review" else None,
                "error": None, "evidence": {}}

    def business_report(self, report_id):
        self.calls.append(("business_report", report_id))
        return {"tool": "business_report", "status": "ok",
                "data": {"rows": [{"n": 96478}], "truncated": False},
                "message": None, "error": None, "evidence": {}}

    def delivery_risk_prediction(self, features):
        self.calls.append(("delivery_risk_prediction", features))
        required = {"purchase_month", "purchase_weekday", "item_count", "item_value", "customer_state"}
        if required - set(features):
            return {"tool": "delivery_risk_prediction", "status": "clarify", "data": None,
                    "message": "请补充预测必需的参数", "error": None, "evidence": {}}
        return {"tool": "delivery_risk_prediction", "status": "ok",
                "data": {"risk_score": 0.025889, "score_type": "uncalibrated_model_score"},
                "message": None, "error": None, "evidence": {}}


@pytest.mark.parametrize("decision", [
    {"status": "route", "tool": "sql_analysis", "arguments": {}},
    {"status": "route", "tool": "business_report", "arguments": {"report_id": "order_status"}},
    {"status": "route", "tool": "delivery_risk_prediction", "arguments": {"features": {"purchase_month": 7}}},
    {"status": "clarify", "message": "请明确口径"},
    {"status": "unsupported", "message": "暂不支持"},
])
def test_parse_valid(decision):
    assert parse_route_json(json.dumps(decision, ensure_ascii=False))["status"] == decision["status"]


@pytest.mark.parametrize("payload", [
    "not json", "[]", "{}",
    '{"status":"route","tool":"sql_analysis","arguments":{"sql":"DELETE FROM orders"}}',
    '{"status":"route","tool":"unknown","arguments":{}}',
    '{"status":"route","tool":"business_report","arguments":{"report_id":"reviews"}}',
    '{"status":"route","tool":"delivery_risk_prediction","arguments":{"features":{"order_id":"abc"}}}',
    '{"status":"route","tool":"delivery_risk_prediction","arguments":{"features":{"purchase_month":null}}}',
    '{"status":"route","tool":"sql_analysis","arguments":{},"extra":1}',
    '{"status":"clarify","message":""}',
])
def test_parse_invalid(payload):
    with pytest.raises(RoutingError):
        parse_route_json(payload)


def test_route_model_called_once():
    backend = Backend({"status": "route", "tool": "sql_analysis", "arguments": {}})
    decision = decide_tool("统计订单数", backend)
    assert decision["status"] == "route"
    assert decision["route_usage"]["total_tokens"] == 10
    assert len(backend.calls) == 1
    assert backend.calls[0][-1]["content"] == "统计订单数"


def test_missing_question_no_model_call():
    backend = Backend()
    decision = decide_tool("   ", backend)
    assert decision["error"]["code"] == "invalid_question"
    assert backend.calls == []


def test_router_model_network_failure_sanitized():
    backend = Backend(error=RuntimeError("API key is secret"))
    decision = decide_tool("订单有多少", backend)
    assert decision["status"] == "error"
    assert "secret" not in str(decision)


def test_router_malformed_no_tool():
    backend = Backend({"status": "route", "tool": "unknown", "arguments": {}})
    svc = Service()
    result = run_v3_turn("订单多少", backend=backend, service=svc)
    assert result["status"] == "error"
    assert result["used_tools"] == []
    assert svc.calls == []


@pytest.mark.parametrize("choice,question,expected", [
    ({"status": "route", "tool": "sql_analysis", "arguments": {}}, "2018年3月订单数", "sql_analysis"),
    ({"status": "route", "tool": "business_report", "arguments": {"report_id": "monthly_late_delivery"}}, "月度延期率", "business_report"),
    ({"status": "route", "tool": "delivery_risk_prediction", "arguments": {"features": {
        "purchase_month": 7, "purchase_weekday": 2, "item_count": 2,
        "item_value": 199.9, "customer_state": "SP"}}}, "预测一笔订单", "delivery_risk_prediction"),
])
def test_graph_single_tool(choice, question, expected):
    backend, svc = Backend(choice), Service()
    result = run_v3_turn(question, backend=backend, service=svc)
    assert result["status"] == "ok"
    assert result["selected_tool"] == expected
    assert result["used_tools"] == [expected]
    assert len(svc.calls) == len(backend.calls) == 1
    assert result["automatic_repair"] is False
    assert result["tool_result"]["tool"] == expected


def test_graph_prediction_missing_fields_clarifies():
    choice = {"status": "route", "tool": "delivery_risk_prediction", "arguments": {"features": {"purchase_month": 7}}}
    svc = Service()
    result = run_v3_turn("7月份预测", backend=Backend(choice), service=svc)
    assert result["status"] == "clarify"
    assert result["used_tools"] == ["delivery_risk_prediction"]
    assert svc.calls == [("delivery_risk_prediction", {"purchase_month": 7})]


def test_graph_clarify_skips_tools():
    svc = Service()
    result = run_v3_turn("哪个好", backend=Backend({"status": "clarify", "message": "什么指标？"}), service=svc)
    assert result["status"] == "clarify"
    assert result["answer"] == "什么指标？"
    assert result["used_tools"] == []
    assert svc.calls == []


def test_graph_unsupported_skips_tools():
    svc = Service()
    result = run_v3_turn("评论分析", backend=Backend({"status": "unsupported", "message": "未开放评论数据"}), service=svc)
    assert result["status"] == "unsupported"
    assert result["used_tools"] == []
    assert svc.calls == []


def test_graph_semantic_risk_blocks_business_data():
    svc = Service(status="needs_review")
    result = run_v3_turn("时间统计", backend=Backend({"status": "route", "tool": "sql_analysis", "arguments": {}}), service=svc)
    assert result["status"] == "needs_review"
    assert result["tool_result"]["data"] is None
    assert "7003" not in result["answer"]


def test_graph_does_not_mutate_sql_question():
    original = "2018年3月有多少订单？"
    svc = Service()
    result = run_v3_turn(original, backend=Backend({"status": "route", "tool": "sql_analysis", "arguments": {}}), service=svc)
    assert svc.calls == [("sql_analysis", original)]


def test_graph_returns_only_first_five_rows():
    class ManyRows(Service):
        def business_report(self, report_id):
            out = super().business_report(report_id)
            out["data"]["rows"] = [{"n": i} for i in range(20)]
            out["data"]["truncated"] = True
            out["status"] = "partial"
            return out
    out = run_v3_turn("状态", backend=Backend({"status": "route", "tool": "business_report", "arguments": {"report_id": "order_status"}}), service=ManyRows())
    assert out["status"] == "partial"
    assert "截断" in out["answer"]
    assert '"n": 6' not in out["answer"]
    assert len(out["tool_result"]["data"]["rows"]) == 20


def test_graph_tool_exception_is_sanitized():
    class Exploding(Service):
        def business_report(self, report_id):
            raise RuntimeError("secret/path")
    out = run_v3_turn("查询", backend=Backend({"status": "route", "tool": "business_report", "arguments": {"report_id": "order_status"}}), service=Exploding())
    assert out["status"] == "error"
    assert "secret" not in str(out)


def test_malformed_success_data_not_reported_as_success():
    class Broken(Service):
        def sql_analysis(self, question):
            return {"tool": "sql_analysis", "status": "ok", "data": None}
    out = run_v3_turn("数据多少", backend=Backend({"status": "route", "tool": "sql_analysis", "arguments": {}}), service=Broken())
    assert out["status"] == "error"
    assert "查询执行成功" not in out["answer"]


def test_wrong_prediction_score_is_blocked():
    class Broken(Service):
        def delivery_risk_prediction(self, features):
            return {"tool": "delivery_risk_prediction", "status": "ok", "data": {"risk_score": float("nan"), "score_type": "uncalibrated_model_score"}}
    out = run_v3_turn("预测", backend=Backend({"status": "route", "tool": "delivery_risk_prediction", "arguments": {"features": {}}}), service=Broken())
    assert out["status"] == "error"
    assert "评分" not in out["answer"]


def test_clarify_with_data_blocked():
    class Broken(Service):
        def sql_analysis(self, question):
            return {"tool": "sql_analysis", "status": "needs_review", "data": {"rows": [{"n": 3}]}}
    out = run_v3_turn("查询", backend=Backend({"status": "route", "tool": "sql_analysis", "arguments": {}}), service=Broken())
    assert out["status"] == "error"
    assert out["tool_result"]["data"] is None


def test_review_unsupported_says_not_permitted_not_absent():
    svc = Service()
    out = run_v3_turn("查询评论评分", backend=Backend({"status": "unsupported", "message": "数据库没有reviews表"}), service=svc)
    assert out["status"] == "unsupported"
    assert "未开放" in out["answer"]
    assert svc.calls == []
