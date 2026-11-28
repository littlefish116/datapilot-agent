"""V3.1A tool-adapter tests: no live LLM calls, no API calls, no model files."""
import json
import sqlite3

import numpy as np
import pytest

from app.v3_tool_service import PREDICT_FIELDS, V3ToolService


class FakeSQLBackend:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return {"content": json.dumps(self.decision, ensure_ascii=False),
                "usage": {"total_tokens": 17}}


class FakeModel:
    def __init__(self, score=0.25):
        self.score = score
        self.calls = []

    def predict_proba(self, frame):
        self.calls.append(frame.copy())
        return np.array([[1 - self.score, self.score]])


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test_olist.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders (order_id TEXT, order_status TEXT, "
                     "order_delivered_customer_date TEXT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?)", [
            ("a", "delivered", "2018-02-02"),
            ("b", "delivered", "not-a-date"),
            ("c", "canceled", None),
        ])
    return path


@pytest.fixture
def predict_payload():
    return dict(purchase_month=7, purchase_weekday=2, item_count=2,
                item_value=199.9, customer_state="sp")


@pytest.fixture
def model_metadata():
    return {"model_name": "delivery_risk_hgb_v1", "features": list(PREDICT_FIELDS)}


def test_sql_real_v2_safe_execution_path(db):
    backend = FakeSQLBackend({"status": "sql", "sql": "SELECT COUNT(*) AS n FROM orders"})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis("多少订单？")
    assert output["status"] == "ok"
    assert output["tool"] == "sql_analysis"
    assert output["data"]["rows"] == [{"n": 3}]
    assert output["evidence"]["source"] == "v2_safe_sql_pipeline"
    assert output["evidence"]["automatic_repair"] is False
    assert backend.calls == 1


def test_sql_clarification_does_not_execute(db):
    backend = FakeSQLBackend({"status": "clarify", "message": "请明确统计口径"})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis("哪个最好")
    assert output["status"] == "clarify"
    assert output["data"] is None
    assert output["message"] == "请明确统计口径"
    assert backend.calls == 1


def test_sql_unsupported_does_not_execute(db):
    backend = FakeSQLBackend({"status": "unsupported", "message": "当前不支持"})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis("查询不开放的字段")
    assert output["status"] == "unsupported"
    assert output["data"] is None


def test_sql_semantic_review_no_execution(db):
    backend = FakeSQLBackend({"status": "sql", "sql": (
        "SELECT COUNT(*) AS n FROM orders "
        "WHERE order_delivered_customer_date IS NOT NULL")})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis("有效送达日期有多少？")
    assert output["status"] == "needs_review"
    assert output["data"] is None
    assert output["evidence"]["risk_review"]["risk_count"] == 1
    assert output["evidence"]["risk_review"]["risks"][0]["code"] == "DATE_VALIDITY_GAP"


def test_sql_unsafe_sql_refused(db):
    backend = FakeSQLBackend({"status": "sql", "sql": "DELETE FROM orders"})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis("删除全部订单")
    assert output["status"] == "error"
    assert output["data"] is None
    assert backend.calls == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3


def test_sql_bad_question_no_model_call(db):
    backend = FakeSQLBackend({"status": "sql", "sql": "SELECT COUNT(*) FROM orders"})
    output = V3ToolService(db_path=db, sql_backend=backend).sql_analysis(" " * 3)
    assert output["error"]["code"] == "invalid_question"
    assert backend.calls == 0


def test_sql_missing_db_no_model_call(tmp_path):
    backend = FakeSQLBackend({"status": "sql", "sql": "SELECT COUNT(*) FROM orders"})
    output = V3ToolService(db_path=tmp_path / "no.db", sql_backend=backend).sql_analysis("订单数量")
    assert output["error"]["code"] == "missing_database"
    assert backend.calls == 0


def test_sql_backend_missing(db):
    output = V3ToolService(db_path=db).sql_analysis("查询订单")
    assert output["error"]["code"] == "sql_backend_unavailable"


def test_sql_partial_keeps_truncation(db):
    backend = object()
    def fake_runner(question, path, *, backend):
        return {"status": "partial", "generated_sql": "SELECT...",
                "execution": {"status": "ok", "columns": ["n"],
                              "rows": [{"n": 1}], "returned_rows": 1,
                              "truncated": True, "duration_ms": 3.2}}
    result = V3ToolService(db_path=db, sql_backend=backend, sql_runner=fake_runner).sql_analysis("查数据")
    assert result["status"] == "partial"
    assert result["data"]["truncated"] is True


def test_sql_malformed_runner_result_fails_closed(db):
    def fake_runner(question, path, *, backend):
        return {"status": "ok", "execution": None}
    result = V3ToolService(db_path=db, sql_backend=object(), sql_runner=fake_runner).sql_analysis("查询")
    assert result["error"]["code"] == "invalid_sql_result"


def test_report_success_direct_no_http():
    calls = []
    def runner(report_id):
        calls.append(report_id)
        return {"report_id": report_id, "rows": [{"n": 9}],
                "returned_rows": 1, "truncated": False}
    output = V3ToolService(report_runner=runner).business_report("order_status")
    assert output["status"] == "ok"
    assert output["data"]["rows"] == [{"n": 9}]
    assert output["evidence"]["source"] == "v1_fixed_report"
    assert calls == ["order_status"]


def test_report_unsupported_not_called():
    def fail(*args):
        raise AssertionError("must not run")
    output = V3ToolService(report_runner=fail).business_report("reviews")
    assert output["status"] == "unsupported"
    assert output["data"] is None


def test_report_partial_preserved():
    def runner(report_id):
        return {"report_id": report_id, "rows": [{"n": 1}],
                "returned_rows": 1, "truncated": True}
    output = V3ToolService(report_runner=runner).business_report("top_states")
    assert output["status"] == "partial"
    assert "截断" in output["message"]


def test_report_exception_sanitized():
    def runner(report_id):
        raise sqlite3.OperationalError("secret/local/path")
    output = V3ToolService(report_runner=runner).business_report("order_status")
    assert output["error"]["code"] == "report_unavailable"
    assert "secret" not in str(output)


def test_report_malformed_result_rejected():
    output = V3ToolService(report_runner=lambda _: {"rows": [1]}).business_report("order_status")
    assert output["error"]["code"] == "invalid_report_result"


def test_prediction_success_v1_schema(model_metadata, predict_payload):
    model = FakeModel(0.025889)
    output = V3ToolService(model=model, metadata=model_metadata).delivery_risk_prediction(predict_payload)
    assert output["status"] == "ok"
    assert output["data"]["risk_score"] == pytest.approx(0.025889)
    assert output["data"]["score_type"] == "uncalibrated_model_score"
    assert output["evidence"]["input_features"]["customer_state"] == "SP"
    assert list(model.calls[0].columns) == list(PREDICT_FIELDS)


def test_prediction_missing_fields_clarification_without_model():
    output = V3ToolService().delivery_risk_prediction({"purchase_month": 7})
    assert output["status"] == "clarify"
    assert "item_value" in output["message"]
    assert output["data"] is None


@pytest.mark.parametrize("name,value", [
    ("purchase_month", 15),
    ("purchase_weekday", 7),
    ("item_count", 0),
    ("item_value", -5),
    ("customer_state", "NOT_A_STATE"),
])
def test_prediction_invalid_fields_refused(model_metadata, predict_payload, name, value):
    payload = {**predict_payload, name: value}
    model = FakeModel()
    output = V3ToolService(model=model, metadata=model_metadata).delivery_risk_prediction(payload)
    assert output["error"]["code"] == "invalid_features"
    assert model.calls == []


def test_prediction_extra_field_refused(model_metadata, predict_payload):
    model = FakeModel()
    output = V3ToolService(model=model, metadata=model_metadata).delivery_risk_prediction(
        {**predict_payload, "order_id": "arbitrary"})
    assert output["error"]["code"] == "invalid_features"
    assert model.calls == []


def test_prediction_model_missing(predict_payload):
    output = V3ToolService().delivery_risk_prediction(predict_payload)
    assert output["error"]["code"] == "model_unavailable"


def test_prediction_metadata_mismatch(model_metadata, predict_payload):
    model_metadata["features"] = list(reversed(model_metadata["features"]))
    model = FakeModel()
    output = V3ToolService(model=model, metadata=model_metadata).delivery_risk_prediction(predict_payload)
    assert output["error"]["code"] == "model_schema_mismatch"
    assert model.calls == []


def test_prediction_nonfinite_score_rejected(model_metadata, predict_payload):
    output = V3ToolService(model=FakeModel(float("nan")), metadata=model_metadata).delivery_risk_prediction(predict_payload)
    assert output["error"]["code"] == "prediction_failed"


def test_prediction_model_exception_sanitized(model_metadata, predict_payload):
    class BrokenModel:
        def predict_proba(self, frame):
            raise RuntimeError("secret local model path")
    output = V3ToolService(model=BrokenModel(), metadata=model_metadata).delivery_risk_prediction(predict_payload)
    assert output["error"]["code"] == "prediction_failed"
    assert "secret" not in str(output)
