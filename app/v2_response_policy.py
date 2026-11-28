"""Narrow user-facing wording normalization for V2 unsupported responses.

This only changes an explanation for known closed query surfaces. It never
claims that unsupported data are absent from the underlying nine-table Olist DB.
It does not grant access, rewrite generated SQL, or call an LLM.
"""
from __future__ import annotations


REVIEW_UNSUPPORTED = (
    "当前 V2 的只读 SQL 查询权限未开放订单评论评分数据。"
    "原始 Olist 数据包含 order_reviews 表，但本接口尚未授权查询该表，"
    "因此无法统计评论评分。"
)


def normalize_unsupported_message(question: str, message: str | None) -> str | None:
    """Correct only review-related 'unsupported' explanations.

    This helper must be called only after the model's status is 'unsupported'.
    It does not decide whether a query should be permitted or executed.
    """
    q = question.lower() if isinstance(question, str) else ""
    m = message.lower() if isinstance(message, str) else ""
    if any(word in q for word in ("评论", "评价", "review")) or (
        ("review" in m or "评论" in m) and any(word in q for word in ("评分", "分数", "星级"))
    ):
        return REVIEW_UNSUPPORTED
    return message
