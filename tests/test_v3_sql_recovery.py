"""V3.3 controlled recovery unit tests: no DeepSeek, DB writes or production API."""
from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

import pytest

from app.v3_sql_recovery import run_v3_sql_recovery
from scripts.install_v3_sql_recovery import install, PATCHES

ORIGINAL = "SELECT COUNT(*) AS n FROM orders WHERE order_status = 'delivered'"
CANDIDATE = "SELECT COUNT(*) AS n FROM orders WHERE 'delivered' = order_status"


class PolicyError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validator(sql):
    if sql == "SELECT BROKEN ( FROM orders":
        raise PolicyError("syntax_error")
    if "order_reviews" in sql:
        raise PolicyError("forbidden_table")
    if not sql.startswith("SELECT"):
        raise PolicyError("unsafe_sql")
    return sql


def error_first(sql=ORIGINAL, code="query_timeout"):
    return {"status": "error", "question": "订单数", "generated_sql": sql,
            "message": None, "execution": {"status": "error", "error": {"code": code}},
            "risk_review": {"status": "ok", "risk_count": 0, "disposition": "no_known_risk"},
            "error": {"code": code, "message": "查询超时"}, "automatic_repair": False}


def syntax_first(sql="SELECT BROKEN ( FROM orders"):
    return {"status": "error", "generated_sql": sql,
            "message": None, "execution": None, "risk_review": None,
            "error": {"code": "sql_validation_failed", "message": "检查失败"}}


def ok_second(sql=CANDIDATE, status="ok"):
    return {"status": status, "generated_sql": sql,
            "risk_review": {"status": "ok", "risk_count": 0, "disposition": "no_known_risk"},
            "execution": {"status": "ok", "sql": sql, "rows": [{"n": 5}],
                          "columns": ["n"], "returned_rows": 1,
                          "truncated": status == "partial"},
            "error": None, "automatic_repair": False}


class Backend:
    def __init__(self, candidate=CANDIDATE, malformed=False, error=None):
        self.calls = []
        self.candidate = candidate
        self.malformed = malformed
        self.error = error

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error and len(self.calls) == 2:
            raise self.error
        if len(self.calls) == 1:
            return {"content": json.dumps({"status": "sql", "sql": ORIGINAL})}
        if self.malformed:
            return {"content": "not json"}
        return {"content": json.dumps({"status": "sql", "sql": self.candidate}),
                "usage": {"total_tokens": 123}}


class Runner:
    def __init__(self, first=None, second=None):
        self.first = deepcopy(first if first is not None else error_first())
        self.second = deepcopy(second if second is not None else ok_second())
        self.calls = []

    def __call__(self, question, db_path, *, backend):
        self.calls.append((question, db_path, backend))
        backend.invoke([{"role": "user", "content": question}])
        return deepcopy(self.first if len(self.calls) == 1 else self.second)


def attempt(backend=None, runner=None, **kwargs):
    backend = backend or Backend()
    runner = runner or Runner()
    result = run_v3_sql_recovery("订单数", "unused.db", backend=backend,
                                 runner=runner, validator=validator,
                                 shape_guard=lambda a, b: True, **kwargs)
    return result, backend, runner


def test_timeout_one_model_repair_and_one_reexecution():
    result, backend, runner = attempt()
    assert result["status"] == "ok"
    assert result["automatic_repair"] is True
    assert result["recovery"]["outcome"] == "technical_success"
    assert result["recovery"]["reason"] == "query_timeout"
    assert result["recovery"]["attempt_count"] == 1
    assert result["recovery"]["semantic_equivalence_proven"] is False
    assert result["recovery"]["budget_increased"] is False
    assert result["recovery"]["repair_usage"] == {"total_tokens": 123}
    assert len(backend.calls) == 2 and len(runner.calls) == 2
    assert runner.calls[0][2] is backend and runner.calls[1][2] is not backend


def test_syntax_error_allows_repair():
    result, backend, runner = attempt(runner=Runner(first=syntax_first()))
    assert result["automatic_repair"] is True
    assert result["recovery"]["reason"] == "syntax_error"
    assert len(backend.calls) == 2


@pytest.mark.parametrize("initial", [
    {**error_first(), "status": "needs_review", "execution": None},
    {**error_first(), "status": "clarify", "execution": None},
    {**error_first(), "status": "unsupported", "execution": None},
    ok_second(),
    {**error_first(), "error": {"code": "forbidden_table"}},
    {**error_first(), "error": {"code": "forbidden_column"}},
    {**error_first(), "error": {"code": "unsafe_sql"}},
    {**error_first(), "error": {"code": "execution_error"}},
    {**error_first(), "error": {"code": "missing_database"}},
    {**error_first(), "risk_review": {"status": "ok", "risk_count": 1, "disposition": "needs_review"}},
    {**error_first(), "risk_review": None},
    {**error_first(), "execution": None},
    {**error_first(), "execution": {"status": "ok", "error": None}},
    syntax_first("DELETE FROM orders"),
    syntax_first("SELECT * FROM order_reviews WHERE x = 'a'"),
    syntax_first("SELECT COUNT(*) FROM orders; DROP TABLE orders"),
    syntax_first("SELECT COUNT(*) FROM orders UNION SELECT 1"),
])
def test_ineligible_never_retries(initial):
    backend, runner = Backend(), Runner(first=initial)
    result, _, _ = attempt(backend=backend, runner=runner)
    assert len(backend.calls) == 1
    assert len(runner.calls) == 1
    assert result["automatic_repair"] is False


def test_timeout_wrong_execution_error_code_does_not_retry():
    initial = error_first()
    initial["execution"]["error"]["code"] = "execution_error"
    result, backend, runner = attempt(runner=Runner(first=initial))
    assert len(backend.calls) == len(runner.calls) == 1


def test_syntax_error_cannot_repair_other_policy_fault():
    sql = "SELECT COUNT(*) FROM order_reviews"
    result, backend, runner = attempt(runner=Runner(first=syntax_first(sql)))
    assert len(backend.calls) == 1 and not result["automatic_repair"]


def test_repair_model_failure_is_sanitized():
    result, backend, runner = attempt(backend=Backend(error=RuntimeError("secret api key")))
    assert result["recovery"]["outcome"] == "repair_model_error"
    assert "secret" not in str(result)
    assert len(runner.calls) == 1


def test_repair_output_invalid_no_second_execution():
    result, backend, runner = attempt(backend=Backend(malformed=True))
    assert result["recovery"]["outcome"] == "invalid_repair_output"
    assert len(backend.calls) == 2 and len(runner.calls) == 1


def test_forbidden_candidate_never_executed():
    result, backend, runner = attempt(backend=Backend(candidate="SELECT COUNT(*) FROM order_reviews"))
    assert result["recovery"]["outcome"] == "candidate_policy_rejected"
    assert len(runner.calls) == 1


def test_non_select_candidate_never_executed():
    result, backend, runner = attempt(backend=Backend(candidate="DELETE FROM orders"))
    assert result["recovery"]["outcome"] == "candidate_policy_rejected"
    assert len(runner.calls) == 1


def test_identical_candidate_no_reexecution():
    result, backend, runner = attempt(backend=Backend(candidate=ORIGINAL))
    assert result["recovery"]["outcome"] == "unchanged_candidate"
    assert len(runner.calls) == 1


def test_shape_drift_for_timeout_blocks_reexecution():
    backend, runner = Backend(), Runner()
    result = run_v3_sql_recovery("订单数", "unused.db", backend=backend,
                                 runner=runner, validator=validator,
                                 shape_guard=lambda a, b: False)
    assert result["recovery"]["outcome"] == "candidate_shape_changed"
    assert len(runner.calls) == 1


def test_candidate_semantic_risk_cannot_be_called_success():
    second = {**ok_second(), "status": "needs_review", "execution": None,
              "risk_review": {"status": "ok", "risk_count": 1, "disposition": "needs_review"}}
    result, backend, runner = attempt(runner=Runner(second=second))
    assert result["status"] == "needs_review"
    assert result["automatic_repair"] is False
    assert result["recovery"]["outcome"] == "semantic_review"


def test_failed_candidate_not_hidden_as_success():
    second = error_first(sql=CANDIDATE)
    result, backend, runner = attempt(runner=Runner(second=second))
    assert result["status"] == "error"
    assert result["automatic_repair"] is False
    assert result["recovery"]["outcome"] == "candidate_failed"
    assert len(backend.calls) == 2


def test_invalid_success_envelope_fails_closed():
    second = {**ok_second(), "risk_review": {"status": "ok", "risk_count": 1, "disposition": "needs_review"}}
    result, backend, runner = attempt(runner=Runner(second=second))
    assert result["status"] == "error"
    assert result["recovery"]["outcome"] == "invalid_candidate_result"


def test_partial_success_is_reported_partial_not_full():
    result, backend, runner = attempt(runner=Runner(second=ok_second(status="partial")))
    assert result["status"] == "partial" and result["automatic_repair"]


def test_invalid_first_result_fails_closed():
    backend = Backend()
    result = run_v3_sql_recovery("问", "db", backend=backend,
                                 runner=lambda q, db, backend: None,
                                 validator=validator)
    assert result["status"] == "error" and result["automatic_repair"] is False


def prepatch_source(name):
    """Derive the base source from tracked patched code; no .bak needed."""
    source_root = Path(__file__).resolve().parents[1]
    text = (source_root / "app" / name).read_text(encoding="utf-8")
    for old, new in reversed(PATCHES["app/" + name]):
        assert text.count(new) == 1
        text = text.replace(new, old, 1)
    return text.encode("utf-8")


def test_installer_idempotent_preserves_files_and_originals(tmp_path):
    for name in ("v3_tool_service.py", "v3_api.py", "v3_routing.py"):
        dest = tmp_path / "app" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(prepatch_source(name))
    updated = install(tmp_path)
    assert len(updated) == 3 and all("Updated" in s for s in updated)
    again = install(tmp_path)
    assert len(again) == 3 and all("Already" in s for s in again)
    for name in ("v3_tool_service.py", "v3_api.py", "v3_routing.py"):
        dest = tmp_path / "app" / name
        assert dest.with_name(name + ".before_v3_sql_recovery.bak").read_bytes() == prepatch_source(name)


def test_installer_rejects_unknown_source_without_partial_changes(tmp_path):
    for name in ("v3_tool_service.py", "v3_api.py", "v3_routing.py"):
        dest = tmp_path / "app" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(prepatch_source(name))
    (tmp_path / "app" / "v3_api.py").write_text("unexpected source", encoding="utf-8")
    with pytest.raises(RuntimeError):
        install(tmp_path)
    assert (tmp_path / "app" / "v3_tool_service.py").read_bytes() == prepatch_source("v3_tool_service.py")
    assert not (tmp_path / "app" / "v3_tool_service.py.before_v3_sql_recovery.bak").exists()


def test_installer_preserves_windows_crlf(tmp_path):
    for name in ("v3_tool_service.py", "v3_api.py", "v3_routing.py"):
        dest = tmp_path / "app" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(prepatch_source(name).replace(b"\n", b"\r\n"))
    install(tmp_path)
    assert b"\r\n" in (tmp_path / "app" / "v3_routing.py").read_bytes()
    assert b"\r\n" in (tmp_path / "app" / "v3_tool_service.py").read_bytes()


def test_candidate_generated_sql_mismatch_fails_closed():
    second = {**ok_second(), "generated_sql": "SELECT COUNT(*) FROM customers"}
    result, backend, runner = attempt(runner=Runner(second=second))
    assert result["status"] == "error"
    assert result["recovery"]["outcome"] == "candidate_sql_mismatch"


def test_candidate_execution_sql_mismatch_fails_closed():
    second = ok_second()
    second["execution"]["sql"] = "SELECT COUNT(*) FROM customers"
    result, backend, runner = attempt(runner=Runner(second=second))
    assert result["status"] == "error"
    assert result["recovery"]["outcome"] == "invalid_candidate_result"


@pytest.mark.skipif(__import__("importlib").util.find_spec("sqlglot") is None,
                    reason="SQLGlot installed only in user project environment")
@pytest.mark.parametrize("candidate,expected", [
    ("SELECT COUNT(*) AS n FROM (SELECT order_status FROM orders WHERE order_status = 'delivered') AS t", True),
    ("SELECT COUNT(*) AS n FROM orders WHERE order_status = 'canceled'", False),
    ("SELECT COUNT(*) AS n FROM customers WHERE customer_state = 'delivered'", False),
    ("SELECT COUNT(*) AS m FROM orders WHERE order_status = 'delivered'", False),
    ("SELECT COUNT(*) AS n FROM orders", False),
])
def test_parsed_timeout_shape_guard(candidate, expected):
    from app.v3_sql_recovery import _sql_shape_preserved
    assert _sql_shape_preserved(ORIGINAL, candidate) is expected
