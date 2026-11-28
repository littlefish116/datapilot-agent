"""Advisory semantic checks for DataPilot V2 Text-to-SQL.

WARNING: A risk signal is NOT a proof of incorrectness. Absence of signals is
NOT a proof of correctness. The Stage 2A SQL validator / SQLite authorizer
remain the only SQL permission boundary.

This module never mutates SQL and never calls an LLM.
"""
from __future__ import annotations

import re
from typing import Callable

_DATE_FIELDS = (
    'order_delivered_customer_date',
    'order_estimated_delivery_date',
)
_NAME_PREFIX = r'(?:[A-Za-z_][A-Za-z0-9_]*\.)?'


def _raw_non_null(sql: str, field: str) -> bool:
    name = _NAME_PREFIX + re.escape(field)
    return bool(re.search(r'\b' + name + r'\s+IS\s+NOT\s+NULL\b', sql, re.I)
                or re.search(r'\bNOT\s+' + name + r'\s+IS\s+NULL\b', sql, re.I))


def _parsed_date_non_null(sql: str, field: str) -> bool:
    date_expr = r'DATE\s*\(\s*' + _NAME_PREFIX + re.escape(field) + r'\s*\)'
    return bool(re.search(date_expr + r'\s+IS\s+NOT\s+NULL\b', sql, re.I)
                or re.search(r'\bNOT\s+' + date_expr + r'\s+IS\s+NULL\b', sql, re.I))


def inspect_semantic_risks(sql: str, *, validator: Callable[[str], str] | None = None) -> dict:
    """Normalize with the Stage 2A AST validator before heuristic risk checks.

    Signals should prompt inspection, never automatic SQL rewriting or rejection.
    The optional validator is for isolated unit tests; production must use the
    default Stage 2A validator.
    """
    if validator is None:
        from app.v2_safe_sql import validate_sql
        validator = validate_sql
    if not isinstance(sql, str):
        return {'status': 'invalid', 'risk_count': 0, 'risks': [], 'disposition': 'invalid_sql'}
    try:
        normalized = validator(sql)
    except ValueError:
        return {'status': 'invalid', 'risk_count': 0, 'risks': [], 'disposition': 'invalid_sql'}
    if not isinstance(normalized, str):
        return {'status': 'invalid', 'risk_count': 0, 'risks': [], 'disposition': 'invalid_sql'}
    risks = []
    missing = [f for f in _DATE_FIELDS if _raw_non_null(normalized, f)
               and not _parsed_date_non_null(normalized, f)]
    if missing:
        risks.append({
            'code': 'DATE_VALIDITY_GAP',
            'severity': 'review',
            'columns': missing,
            'detail': '使用原始日期字符串非空判断，不能排除非空但无法被 DATE() 解析的日期',
        })
    # Narrow canary for raw line-item/payment aggregation in the same SELECT.
    # False positives possible with nested CTEs; the flag requires manual review.
    has_both = (re.search(r'\b(?:FROM|JOIN)\s+order_items\b', normalized, re.I)
                and re.search(r'\b(?:FROM|JOIN)\s+order_payments\b', normalized, re.I))
    has_raw_sum = re.search(r'\bSUM\s*\(\s*' + _NAME_PREFIX +
                            r'(?:price|payment_value)\s*\)', normalized, re.I)
    if has_both and has_raw_sum:
        risks.append({
            'code': 'POTENTIAL_JOIN_FANOUT',
            'severity': 'review',
            'columns': ['price', 'payment_value'],
            'detail': '同一查询存在商品与支付明细并汇总金额，请人工检查是否先按订单聚合以避免乘法膨胀',
        })
    return {
        'status': 'ok',
        'risk_count': len(risks),
        'risks': risks,
        'disposition': 'needs_review' if risks else 'no_known_risk',
        'policy_enforced_elsewhere': True,
        'semantics_proven': False,
    }
