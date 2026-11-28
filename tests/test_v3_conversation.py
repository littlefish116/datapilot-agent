"""V3.2 in-memory session tests: no LangGraph, network, Olist or HGB needed."""
import json
import threading
import time
from uuid import UUID

import pytest

from app.v3_conversation import (
    ConversationMissing, V3SessionStore, _month_rewrite,
    run_v3_conversation_turn,
)


class Backend:
    def __init__(self, decisions=None):
        self.decisions = list(decisions or [])
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if not self.decisions:
            raise AssertionError("unexpected context model invocation")
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return {"content": json.dumps(decision, ensure_ascii=False)}


class Runner:
    def __init__(self, status="ok", selected_tool="sql_analysis"):
        self.calls = []
        self.status = status
        self.selected_tool = selected_tool

    def __call__(self, question, *, backend, service):
        self.calls.append(question)
        data = {"rows": [{"n": 7}], "truncated": False}
        return {"status": self.status, "question": question,
                "selected_tool": self.selected_tool,
                "used_tools": [self.selected_tool],
                "answer": "查询结果 7", "tool_result": {
                    "tool": self.selected_tool, "status": self.status,
                    "data": data if self.status == "ok" else None,
                    "evidence": {"report_id": "monthly_late_delivery"}},
                "route_usage": {"total_tokens": 9}, "automatic_repair": False}


def turn(store, question, *, sid=None, backend=None, runner=None, reset=False):
    return run_v3_conversation_turn(
        question, store=store, conversation_id=sid,
        backend=backend or Backend(), service=object(),
        reset_context=reset, runner=runner or Runner(),
    )


def test_new_session_returns_uuid_and_preserves_v31_contract():
    store = V3SessionStore()
    runner = Runner()
    r = turn(store, "查询2018年3月的延期率", runner=runner)
    assert UUID(r["conversation_id"])
    assert r["status"] == "ok"
    assert r["selected_tool"] == "sql_analysis"
    assert r["question"] == "查询2018年3月的延期率"
    assert r["effective_question"] == r["question"]
    assert r["context_used"] is False
    assert r["session_turn"] == 1
    assert runner.calls == ["查询2018年3月的延期率"]


def test_month_followup_inherits_metric_and_year_without_llm_rewriter():
    store = V3SessionStore()
    runner = Runner()
    first = turn(store, "查询2018年3月的订单延期率", runner=runner)
    second = turn(store, "那 4 月呢？", sid=first["conversation_id"], runner=runner)
    assert second["question"] == "那 4 月呢？"
    assert second["effective_question"] == "查询2018年4月的订单延期率"
    assert second["context_used"] is True
    assert second["context_resolution"] == "month_rewrite"
    assert second["session_turn"] == 2
    assert runner.calls == ["查询2018年3月的订单延期率", "查询2018年4月的订单延期率"]


def test_followup_has_no_context_on_first_turn():
    runner = Runner()
    result = turn(V3SessionStore(), "那4月呢？", runner=runner)
    assert result["status"] == "clarify"
    assert result["selected_tool"] is None
    assert result["used_tools"] == []
    assert result["effective_question"] is None
    assert runner.calls == []


def test_month_without_year_after_multiyear_report_asks_clarify():
    runner = Runner(selected_tool="business_report")
    store = V3SessionStore()
    first = turn(store, "查询每个月的订单延期率", runner=runner)
    second = turn(store, "那4月呢？", sid=first["conversation_id"], runner=runner)
    assert second["status"] == "clarify"
    assert "年份" in second["answer"]
    assert len(runner.calls) == 1
    assert second["tool_result"] is None


def test_exact_year_after_multiyear_report_uses_metric():
    runner = Runner(selected_tool="business_report")
    store = V3SessionStore()
    first = turn(store, "查询每个月的订单延期率", runner=runner)
    second = turn(store, "那2018年4月呢？", sid=first["conversation_id"], runner=runner)
    assert second["effective_question"] == "查询2018年4月的订单延期率"
    assert len(runner.calls) == 2


def test_two_dated_scopes_do_not_guess_which_to_change():
    runner = Runner()
    store = V3SessionStore()
    first = turn(store, "比较2017年3月和2018年3月的订单量", runner=runner)
    result = turn(store, "那4月呢？", sid=first["conversation_id"], runner=runner)
    assert result["status"] == "clarify"
    assert len(runner.calls) == 1


def test_isolated_sessions_have_different_anchors():
    store = V3SessionStore()
    runner = Runner()
    x = turn(store, "查询2017年3月的订单数", runner=runner)
    y = turn(store, "查询2018年3月的延期率", runner=runner)
    rx = turn(store, "那4月呢？", sid=x["conversation_id"], runner=runner)
    ry = turn(store, "那4月呢？", sid=y["conversation_id"], runner=runner)
    assert rx["effective_question"] == "查询2017年4月的订单数"
    assert ry["effective_question"] == "查询2018年4月的延期率"
    assert x["conversation_id"] != y["conversation_id"]


def test_unknown_id_never_gets_new_session_silently():
    store = V3SessionStore()
    runner = Runner()
    with pytest.raises(ConversationMissing):
        turn(store, "那4月呢？", sid="00000000-0000-4000-8000-000000000009", runner=runner)
    assert runner.calls == []


def test_ttl_expiration():
    now = [10.0]
    store = V3SessionStore(clock=lambda: now[0], ttl_seconds=30)
    first = turn(store, "查询2018年3月的延期率")
    now[0] = 41
    with pytest.raises(ConversationMissing):
        turn(store, "那4月呢？", sid=first["conversation_id"])


def test_capacity_evicts_oldest_inactive_session():
    store = V3SessionStore(max_sessions=2)
    a = turn(store, "查询2018年1月数据")
    b = turn(store, "查询2018年2月数据")
    c = turn(store, "查询2018年3月数据")
    assert b["conversation_id"] != c["conversation_id"]
    with pytest.raises(ConversationMissing):
        store.get(a["conversation_id"])


def test_max_turn_limit_forgets_old_context():
    store = V3SessionStore(max_turns=1)
    first = turn(store, "查询2018年3月的延期率")
    second = turn(store, "那4月呢？", sid=first["conversation_id"])
    assert second["status"] == "clarify"
    assert second["context_resolution"] == "no_context"


def test_reset_context_returns_new_session_without_leak():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    reset = turn(store, "查询2017年3月的订单数", sid=first["conversation_id"], reset=True)
    assert reset["conversation_id"] != first["conversation_id"]
    assert reset["session_turn"] == 1
    with pytest.raises(ConversationMissing):
        store.get(first["conversation_id"])


def test_standalone_query_clears_old_anchor():
    store = V3SessionStore()
    runner = Runner()
    first = turn(store, "查询2018年3月的订单数", runner=runner)
    second = turn(store, "查询商品类别销量排行榜", sid=first["conversation_id"], runner=runner)
    assert second["context_used"] is False
    third = turn(store, "那4月呢？", sid=first["conversation_id"], runner=runner)
    assert third["status"] == "clarify"


def test_model_rewriter_uses_previous_question_not_rows():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    backend = Backend([{"status": "resolved", "question": "比较2018年3月的延期率和4月的延期率"}])
    runner = Runner()
    second = turn(store, "那和4月比呢？", sid=first["conversation_id"], backend=backend, runner=runner)
    assert second["status"] == "ok"
    assert second["context_resolution"] == "model_rewrite"
    assert second["context_used"] is True
    assert backend.calls
    raw = backend.calls[0][-1]["content"]
    assert "previous_question" in raw
    assert "rows" not in raw
    assert "7" not in raw  # business result is never put into the resolver prompt


def test_model_rewriter_invented_numbers_fail_closed():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    backend = Backend([{"status": "resolved", "question": "查询2018年4月和2027年9月延期率"}])
    runner = Runner()
    second = turn(store, "那4月和上月比呢？", sid=first["conversation_id"], backend=backend, runner=runner)
    assert second["status"] == "clarify"
    assert len(runner.calls) == 0


def test_model_rewriter_invalid_json_fail_closed():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    backend = Backend([{"status":"route", "tool":"sql_analysis", "arguments":{}}])
    second = turn(store, "和上月比呢？", sid=first["conversation_id"], backend=backend)
    assert second["status"] == "clarify"


def test_model_rewriter_standalone_choice():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    backend = Backend([{"status": "standalone"}])
    runner = Runner()
    q = "那讲讲天气呢？"
    second = turn(store, q, sid=first["conversation_id"], backend=backend, runner=runner)
    assert second["context_used"] is False
    assert runner.calls == [q]


def test_bad_model_result_does_not_overwrite_last_confirmed_context():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    error_runner = Runner(status="error")
    failed = turn(store, "那4月呢？", sid=first["conversation_id"], runner=error_runner)
    assert failed["status"] == "error"
    success = turn(store, "那5月呢？", sid=first["conversation_id"])
    assert success["effective_question"] == "查询2018年5月的延期率"


def test_session_does_not_save_raw_tool_data():
    store = V3SessionStore()
    r = turn(store, "查询2018年3月的延期率")
    s = store.get(r["conversation_id"])
    assert not any(k in vars(s) for k in ("tool_result", "rows", "generated_sql", "messages"))


def test_one_session_serialized_across_two_threads():
    store = V3SessionStore()
    first = turn(store, "查询2018年3月的延期率")
    shared_runner = Runner()
    errors = []

    def worker():
        try:
            turn(store, "那4月呢？", sid=first["conversation_id"], runner=shared_runner)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    assert errors == []
    assert len(shared_runner.calls) == 2
    assert store.get(first["conversation_id"]).turn_count == 3


@pytest.mark.parametrize("previous,q,expected", [
    ("查询2018年3月的延期率", "那4月呢？", "查询2018年4月的延期率"),
    ("2017年12月订单数", "2018年1月呢", "2018年1月订单数"),
    ("查询每个月的延期率", "2018年4月呢", "查询2018年4月的延期率"),
])
def test_deterministic_month_rewrite_cases(previous, q, expected):
    assert _month_rewrite(previous, q) == ("resolved", expected, None)


def test_prediction_missing_feature_followup_uses_context_rewriter():
    store = V3SessionStore()

    def predicted(question, *, backend, service):
        if "SP州" not in question:
            return {"status": "clarify", "selected_tool": "delivery_risk_prediction",
                    "used_tools": ["delivery_risk_prediction"], "answer": "请补充州",
                    "tool_result": {"tool": "delivery_risk_prediction", "status": "clarify",
                                    "data": None, "evidence": {}},
                    "route_usage": {}, "automatic_repair": False}
        return {"status": "ok", "selected_tool": "delivery_risk_prediction",
                "used_tools": ["delivery_risk_prediction"], "answer": "未校准的风险评分",
                "tool_result": {"tool": "delivery_risk_prediction", "status": "ok",
                                "data": {"risk_score": 0.03}, "evidence": {}},
                "route_usage": {}, "automatic_repair": False}

    first = turn(store, "预测7月星期二购买2件商品金额199.9元的订单延期风险", runner=predicted)
    assert first["status"] == "clarify"
    backend = Backend([{"status": "resolved", "question":
                       "预测7月星期二购买2件商品金额199.9元SP州的订单延期风险"}])
    second = turn(store, "客户州是SP", sid=first["conversation_id"],
                  backend=backend, runner=predicted)
    assert second["status"] == "ok"
    assert second["context_used"] is True
    assert second["selected_tool"] == "delivery_risk_prediction"
