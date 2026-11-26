"""DataPilot V2 defensive, read-only SQL analytics executor.

Not wired into FastAPI or an LLM yet: validate with tests before adding endpoints.
The database authorizer is a second boundary after SQLGlot AST checks.  This
is intended for local analytics against a trusted SQLite file, not a public
multi-tenant service or a replacement for authentication and database isolation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from sqlglot import ErrorLevel, exp, parse
from sqlglot.errors import ParseError

from app.v2_sql_policy import (
    IDENTIFIER_COLUMNS, SQLITE_FUNCTIONS, TABLE_COLUMNS, SqlLimits,
)

logger = logging.getLogger(__name__)

# SQLGlot names for statement/side-effect constructs.  A top-level SELECT is
# required in addition to this recursive check, so errors fail closed.
BLOCKED_AST_NAMES = frozenset({
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Attach",
    "Detach", "Pragma", "Command", "Merge", "Truncate", "Transaction",
    "Commit", "Rollback", "Copy", "Into", "Execute", "Set", "Use",
})


class SqlPolicyError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fails(code: str, detail: str) -> None:
    raise SqlPolicyError(code, detail)


def validate_sql(sql: str, limits: SqlLimits | None = None) -> str:
    """Return normalized SQLite SQL only for a narrow, single-statement SELECT.

    Notes: This lightweight AST pass is *not* a schema-linking or SQL semantics
    verifier.  Runtime sqlite3 authorizer checks actual table/column accesses.
    """
    limits = limits or SqlLimits()
    if not isinstance(sql, str) or not sql.strip() or len(sql) > limits.max_sql_chars:
        _fails("invalid_sql", "SQL 为空或长度超出限制")

    try:
        parsed = parse(sql, read="sqlite", error_level=ErrorLevel.RAISE)
    except (ParseError, ValueError) as exc:
        logger.info("SQL parse error: %s", exc)
        _fails("syntax_error", "SQL 无法解析")

    if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
        _fails("unsafe_sql", "仅允许单条 SELECT 查询")

    tree = parsed[0]
    nodes = list(tree.walk())
    if len(nodes) > limits.max_ast_nodes:
        _fails("unsafe_sql", "SQL 结构过于复杂")

    for node in nodes:
        if type(node).__name__ in BLOCKED_AST_NAMES:
            _fails("unsafe_sql", "SQL 包含不允许的操作")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            _fails("unsafe_sql", "不允许递归 CTE")
        if isinstance(node, (exp.Union, exp.Except, exp.Intersect)):
            _fails("unsafe_sql", "暂不支持 UNION / EXCEPT / INTERSECT")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            _fails("unsafe_sql", "不允许 SELECT * 或表通配符")

    cte_names = {
        node.alias_or_name.lower() for node in tree.find_all(exp.CTE)
        if node.alias_or_name
    }
    if len(cte_names) != len(list(tree.find_all(exp.CTE))):
        _fails("unsafe_sql", "CTE 名称必须唯一且非空")
    if cte_names.intersection(TABLE_COLUMNS):
        _fails("unsafe_sql", "CTE 不能覆盖真实数据表名称")

    base_tables = set()
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:
            _fails("forbidden_table", "不允许跨数据库或附加数据库访问")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in TABLE_COLUMNS:
            _fails("forbidden_table", "SQL 引用了未开放的表")
        base_tables.add(name)
    if not base_tables:
        _fails("unsafe_sql", "查询必须访问已授权的业务数据表")

    # Quick AST typo screen. Some CTE/derived-column aliases are legal; actual
    # physical column references are checked against per-table policy in SQLite.
    allowed_names = set().union(*TABLE_COLUMNS.values())
    allowed_names.update(
        (node.alias_or_name or "").lower()
        for node in tree.find_all(exp.Alias)
    )
    allowed_names.update(
        (projection.alias_or_name or "").lower()
        for select in tree.find_all(exp.Select)
        for projection in select.expressions
    )
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if name not in allowed_names or name in {"rowid", "oid", "_rowid_"}:
            _fails("forbidden_column", "SQL 引用了未开放的字段")

    def is_inside_count(column: exp.Column, boundary: exp.Expression) -> bool:
        node = column.parent
        while node is not None:
            if isinstance(node, exp.Count):
                return True
            if node is boundary:
                return False
            node = node.parent
        return False

    # Track simple alias propagation of sensitive identifiers across CTEs.
    # This is deliberately conservative, not a full SQL data-lineage solution.
    tainted = set(IDENTIFIER_COLUMNS)
    alias_expressions = list(tree.find_all(exp.Alias))
    for _ in range(len(alias_expressions) + 1):
        changed = False
        for alias in alias_expressions:
            if not alias.alias:
                continue
            for c in alias.this.find_all(exp.Column):
                if c.name.lower() not in tainted:
                    continue
                if not is_inside_count(c, alias.this):
                    if alias.alias.lower() not in tainted:
                        tainted.add(alias.alias.lower())
                        changed = True
        if not changed:
            break

    # Disallow raw key columns in the final result, including simple aliases.
    # Joins and COUNT(DISTINCT key) remain supported. Do NOT infer privacy
    # guarantees from this limited check; local analytics only.
    for projection in tree.expressions:
        expr = projection.this if isinstance(projection, exp.Alias) else projection
        for col in expr.find_all(exp.Column):
            if col.name.lower() in tainted:
                if not is_inside_count(col, expr):
                    _fails("unsafe_sql", "不允许在最终结果直接返回原始业务标识符")

    try:
        return tree.sql(dialect="sqlite")
    except Exception as exc:
        logger.warning("SQL render error: %s", exc)
        _fails("syntax_error", "SQL 无法转换为 SQLite 方言")


def _authorizer(action: int, first: str | None, second: str | None,
                _dbname: str | None, _source: str | None) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        table = (first or "").lower()
        column = (second or "").lower()
        # SQLite may send an empty column name for COUNT(*) on a known table.
        if table in TABLE_COLUMNS and (column in TABLE_COLUMNS[table] or column == ""):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (second or first or "").lower()
        if function_name in SQLITE_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def execute_safe_sql(
    sql: str, db_path: Path | str, limits: SqlLimits | None = None,
) -> dict:
    """Validate, execute read-only SQL, and return a bounded JSON-compatible result.

    Exposes a limited public error category, not detailed SQLite exceptions.
    """
    limits = limits or SqlLimits()
    start = time.monotonic()
    response = {
        "status": "error", "sql": None, "columns": [], "rows": [],
        "returned_rows": 0, "truncated": False, "duration_ms": 0.0,
        "error": None,
    }
    def fail(code: str, message: str):
        response["error"] = {"code": code, "message": message}
        response["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
        return response

    try:
        validated_sql = validate_sql(sql, limits)
    except SqlPolicyError as exc:
        return fail(exc.code, exc.detail)
    response["sql"] = validated_sql

    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return fail("missing_database", "业务数据库不存在")

    instruction_count = 0
    timed_out = False
    try:
        uri = path.as_uri() + "?mode=ro&cache=private"
        with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            # Configure before installing the deny-by-default authorizer.
            conn.execute("PRAGMA query_only=ON")
            conn.set_authorizer(_authorizer)

            def progress() -> int:
                nonlocal instruction_count, timed_out
                instruction_count += limits.vm_check_every
                timed_out = (
                    time.monotonic() - start > limits.timeout_seconds
                    or instruction_count >= limits.max_vm_steps
                )
                return 1 if timed_out else 0

            conn.set_progress_handler(progress, limits.vm_check_every)
            cursor = conn.execute(validated_sql)
            if cursor.description is None:
                return fail("unsafe_sql", "仅允许返回结果集的查询")
            columns = [col[0] for col in cursor.description]
            rows = [dict(row) for row in cursor.fetchmany(limits.max_rows + 1)]
            response["columns"] = columns
            response["truncated"] = len(rows) > limits.max_rows
            response["rows"] = rows[:limits.max_rows]
            response["returned_rows"] = len(response["rows"])
            serialized = json.dumps(response["rows"], ensure_ascii=False, default=str)
            if len(serialized.encode("utf-8")) > limits.max_result_bytes:
                response["rows"] = []
                response["columns"] = []
                response["returned_rows"] = 0
                response["truncated"] = False
                return fail("result_too_large", "结果体积超出限制")
            response["status"] = "ok"
            response["error"] = None
            response["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
            return response
    except sqlite3.DatabaseError as exc:
        logger.warning("Safe SQL blocked or failed: %s", exc)
        return fail(
            "query_timeout" if timed_out else "execution_error",
            "查询超时或超过执行预算" if timed_out else "SQL 执行失败或访问未授权资源",
        )
    except OSError as exc:
        logger.warning("Safe SQL IO error: %s", exc)
        return fail("database_error", "无法访问业务数据库")
