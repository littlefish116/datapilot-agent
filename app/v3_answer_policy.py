"""Evidence-only rendering of a *single month* from the existing fixed report.

V3.2 does not change the SQL, report runner, tool result, or V1/V2 endpoints.
This is presentation logic, not a new query or a business-semantic verifier.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

_YEAR_MONTH = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月")
_ANY_MONTH = re.compile(r"(?<!\d)(?:1[0-2]|0?[1-9])\s*月")
_MULTI_MONTH_CUES = ("每个月", "每月", "各月", "所有月份", "全年", "同比", "环比", "比较", "对比", "相比", "和上月", "与上月", "去年同期")


def _single_month(question: str) -> tuple[str, str] | None:
    """Only focus clearly specified *one* YYYY-MM, not ranges/comparisons."""
    if not isinstance(question, str) or any(word in question for word in _MULTI_MONTH_CUES):
        return None
    matches = list(_YEAR_MONTH.finditer(question))
    if len(matches) != 1 or len(_ANY_MONTH.findall(question)) != 1:
        return None
    match = matches[0]
    year, month = match.group("year"), int(match.group("month"))
    return f"{year}-{month:02d}", f"{year}年{month}月"


def targeted_monthly_report_answer(
    question: str, result: Mapping[str, Any], *, report_id: str | None,
) -> str | None:
    """Return a grounded monthly answer, or None for the existing renderer.

    Leave `tool_result.data.rows` untouched for auditability. A missing row in a
    truncated report is *not* evidence that the month has no matching orders.
    """
    if report_id != "monthly_late_delivery" or result.get("tool") != "business_report":
        return None
    if result.get("status") not in {"ok", "partial"}:
        return None
    target = _single_month(question)
    if target is None:
        return None
    key, label = target
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return "固定报表结果格式异常，无法核实指定月份的延期率。"
    rows = [r for r in data["rows"] if isinstance(r, dict) and r.get("purchase_month") == key]
    truncated = result["status"] == "partial" or data.get("truncated") is True
    if not rows:
        if truncated:
            return f"固定报表返回结果已截断，无法确认{label}的延期率；请调整查询方式。"
        return f"固定报表中没有{label}对应的统计行；这不等于该月不存在订单。"
    if len(rows) != 1:
        return f"固定报表中{label}存在重复统计行，无法可靠回答，需核验统计口径。"
    row = rows[0]
    total, late, rate = (row.get("delivered_with_dates"), row.get("late_orders"),
                         row.get("late_rate_pct"))
    if (isinstance(total, bool) or not isinstance(total, int) or total < 0
            or isinstance(late, bool) or not isinstance(late, int) or late < 0
            or late > total or isinstance(rate, bool)
            or not isinstance(rate, (int, float)) or not math.isfinite(rate)
            or not 0 <= rate <= 100
            or (total > 0 and abs(100 * late / total - rate) > 0.02)
            or (total == 0 and (late != 0 or rate != 0))):
        return f"固定报表中{label}的统计字段不一致，无法可靠回答，需核验数据。"
    text = (f"{label}，按固定月度延期率报表口径，有效日期的已交付订单共"
            f"{total:,}笔，其中延期{late:,}笔，延期率为{rate:.2f}%。")
    if truncated:
        text += " 注意：本次完整报表结果有截断，仅展示已匹配月份的统计行。"
    return text
