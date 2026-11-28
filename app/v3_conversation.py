"""V3.2 local-only bounded conversational context around V3.1B LangGraph.

This is an IN-MEMORY single-process store; not a LangGraph checkpointer.
Only normalized question + tool metadata are retained; SQL results, raw SQL,
model outputs, and database rows are deliberately not persisted. Every turn
still calls the unmodified V3.1B LangGraph and V2 SQL safety pipeline.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from app.v3_routing import run_v3_turn

MAX_EFFECTIVE_CHARS = 2000
MAX_RESOLUTION_CHARS = 4000
YEAR_MONTH = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月")
MONTH_ONLY = re.compile(
    r"^\s*(?:那|那么|再看|看看|改成|换成|如果是|改为)?\s*"
    r"(?:(?P<year>19\d{2}|20\d{2})\s*年\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*月"
    r"(?:的呢|呢|怎么样|如何|的情况呢|的数据呢|呢？)?\s*[?？。！!]*\s*$"
)
CONTEXT_LEAD = re.compile(r"^\s*(?:那|那么|它|这个|这些|刚才|上次|再看|再查|改成|换成|改为|同比|环比|相比|和上月|比上月|与上月)")
ALL_MONTHS = re.compile(r"每个月|每月|各月")
NUMBERS = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")

RESOLVE_PROMPT = """你是 DataPilot 的多轮问题补全器，不是业务回答者，也不是 SQL 生成器。
仅根据上一轮已核实的完整问题和当前用户追问，生成本轮可独立理解的中文统计问题。
必须保留上一轮指标、业务口径、业务对象与没有明确变动的筛选条件；只改用户明确指出的内容。
如果当前问题是独立的新问题，返回 standalone；如果关键口径缺失或含糊不清，返回 clarify。
不得编造年份、金额、百分比、预测特征或任何没有在前后两轮出现的新数值；不要补充未经证实的事实。
严格只返回以下三种 JSON 对象之一，不要解释、Markdown 或额外字段：
{"status":"resolved","question":"完整且独立的中文问题"}
{"status":"standalone"}
{"status":"clarify","message":"需要进一步明确的内容"}
不要返回 SQL，也不要修改安全权限或授权范围。"""


class ConversationMissing(LookupError):
    """Unknown, expired, or evicted conversation ID."""


@dataclass
class _Session:
    conversation_id: str
    last_effective_question: str | None = None
    last_tool: str | None = None
    last_status: str | None = None
    last_report_id: str | None = None
    turn_count: int = 0
    last_used: float = 0.0
    busy: bool = False
    lock: Any = field(default_factory=threading.Lock, repr=False)


class V3SessionStore:
    """Per-application, bounded and expiring session store.

    A session lock serializes requests for the same conversation. Different
    sessions can run concurrently. Session creation and eviction are locked.
    Callers should not share this store with a publicly exposed deployment.
    """

    def __init__(self, *, ttl_seconds: float = 2700, max_sessions: int = 128,
                 max_turns: int = 12, clock: Callable[[], float] = time.monotonic):
        if ttl_seconds <= 0 or max_sessions < 1 or max_turns < 1:
            raise ValueError("invalid session limits")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [sid for sid, s in self._sessions.items()
                   if not s.busy and now - s.last_used > self.ttl_seconds]
        for sid in expired:
            del self._sessions[sid]

    def new(self) -> _Session:
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._sessions) >= self.max_sessions:
                free = [s for s in self._sessions.values() if not s.busy]
                if not free:
                    raise ConversationMissing("会话容量已满，请稍后发起新会话")
                oldest = min(free, key=lambda s: s.last_used)
                del self._sessions[oldest.conversation_id]
            sid = str(uuid4())
            session = _Session(conversation_id=sid, last_used=now)
            self._sessions[sid] = session
            return session

    def get(self, session_id: str) -> _Session:
        with self._lock:
            now = self._clock()
            self._prune(now)
            session = self._sessions.get(session_id)
            if session is None:
                raise ConversationMissing("会话已过期或服务已重启，请开启新会话")
            return session

    def use(self, session: _Session):
        """Return session's own lock for `with store.use(session):` usage."""
        return session.lock

    def mark_busy(self, session: _Session, busy: bool) -> None:
        with self._lock:
            # A session may have been evicted while the caller was waiting.
            if self._sessions.get(session.conversation_id) is not session:
                raise ConversationMissing("会话已失效，请开启新会话")
            session.busy = busy
            session.last_used = self._clock()

    def reset(self, session_id: str | None) -> _Session:
        if session_id is not None:
            with self._lock:
                session = self._sessions.get(session_id)
                if session and not session.busy:
                    del self._sessions[session_id]
        return self.new()


def _looks_contextual(question: str) -> bool:
    question = question.strip()
    return bool(MONTH_ONLY.fullmatch(question) or CONTEXT_LEAD.match(question)
                or (len(question) < 13 and question.endswith(("呢", "呢？", "呢?"))))


def _month_rewrite(previous: str, question: str) -> tuple[str, str | None, str | None] | None:
    """Deterministic month replacement; never guess year from multi-year data."""
    match = MONTH_ONLY.fullmatch(question.strip())
    if match is None:
        return None
    year = match.group("year")
    month = int(match.group("month"))
    dates = list(YEAR_MONTH.finditer(previous))
    if year is None:
        years = {m.group("year") for m in dates}
        if len(dates) != 1 or len(years) != 1:
            return ("clarify", None, "请明确年份；上一轮没有唯一的年月条件，不能直接推断“几月”。")
        year = dates[0].group("year")
    target = f"{year}年{month}月"
    if len(dates) == 1:
        updated = previous[:dates[0].start()] + target + previous[dates[0].end():]
    elif not dates and ALL_MONTHS.search(previous):
        updated = ALL_MONTHS.sub(target, previous, count=1)
    else:
        return ("clarify", None, "上一轮有多个年月或没有可继承的月份指标，请完整说明本轮统计目标。")
    if not updated or len(updated) > MAX_EFFECTIVE_CHARS:
        return ("clarify", None, "继承后的问题过长，请明确说明本轮问题。")
    return ("resolved", updated, None)


def _parse_resolution(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("invalid response")
    content = raw.get("content")
    if not isinstance(content, str) or not 0 < len(content.strip()) <= MAX_RESOLUTION_CHARS:
        raise ValueError("invalid model content")
    item = json.loads(content)
    if not isinstance(item, dict):
        raise ValueError("not an object")
    status = item.get("status")
    if status == "standalone" and set(item) == {"status"}:
        return item
    if status == "resolved" and set(item) == {"status", "question"}:
        q = item["question"]
        if isinstance(q, str) and q.strip() and len(q.strip()) <= MAX_EFFECTIVE_CHARS:
            return {"status": status, "question": q.strip()}
    if status == "clarify" and set(item) == {"status", "message"}:
        m = item["message"]
        if isinstance(m, str) and m.strip() and len(m) <= 500:
            return {"status": status, "message": m.strip()}
    raise ValueError("invalid context decision")


def _resolve_followup(question: str, session: _Session, backend: Any) -> tuple[str, str | None, str | None, str]:
    previous = session.last_effective_question
    if previous is None:
        return ("clarify", None, "当前会话没有可继承的完整问题，请明确指标和时间范围。", "no_context")
    month = _month_rewrite(previous, question)
    if month is not None:
        status, resolved, message = month
        return status, resolved, message, "month_rewrite" if status == "resolved" else "clarify"
    context = {"previous_question": previous, "previous_tool": session.last_tool,
               "current_question": question}
    try:
        raw = backend.invoke([
            {"role": "system", "content": RESOLVE_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ])
        parsed = _parse_resolution(raw)
    except Exception:
        return ("clarify", None, "无法安全补全追问，请完整说明本轮统计问题。", "clarify")
    if parsed["status"] == "clarify":
        return ("clarify", None, parsed["message"], "clarify")
    if parsed["status"] == "standalone":
        return ("standalone", question, None, "standalone")
    candidate = parsed["question"]
    # The resolver may rephrase, but it must not invent numeric parameters.
    if not set(NUMBERS.findall(candidate)).issubset(set(NUMBERS.findall(previous + " " + question))):
        return ("clarify", None, "追问补全出现未确认的数值，请完整说明本轮问题。", "clarify")
    if candidate == previous and question.strip() != previous.strip():
        return ("clarify", None, "无法确定追问所修改的条件，请具体说明。", "clarify")
    return ("resolved", candidate, None, "model_rewrite")


def _clarify_response(question: str, message: str) -> dict:
    return {"status": "clarify", "question": question, "selected_tool": None,
            "used_tools": [], "answer": message, "tool_result": None,
            "route_usage": None, "automatic_repair": False}


def run_v3_conversation_turn(
    question: str, *, store: V3SessionStore, conversation_id: str | None,
    backend: Any, service: Any, reset_context: bool = False,
    runner: Callable[..., dict] = run_v3_turn,
) -> dict:
    """Run a single V3.1B LangGraph turn with strictly bounded session context."""
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_EFFECTIVE_CHARS:
        raise ValueError("invalid question")
    question = question.strip()
    session = store.reset(conversation_id) if reset_context else (store.get(conversation_id) if conversation_id else store.new())
    with store.use(session):
        store.mark_busy(session, True)
        try:
            if session.turn_count >= store.max_turns:
                session.last_effective_question = None
                session.last_tool = None
                session.last_report_id = None
                session.last_status = None
                session.turn_count = 0
            contextual = _looks_contextual(question)
            if (session.last_tool == "delivery_risk_prediction"
                    and session.last_status == "clarify"
                    and len(question) <= 160
                    and not question.startswith(("查询", "统计", "分析", "比较", "重新开始"))):
                # An incomplete HGB request may be continued with a short
                # feature fragment. A model must still explicitly reconstruct
                # the full question, and V1 validates all five features.
                contextual = True
            used_context = False
            resolution = "standalone"
            effective = question
            if contextual:
                if session.last_effective_question is None:
                    response = _clarify_response(question, "请先给出完整的统计目标和时间范围，本轮无法确定追问指向。")
                    resolution = "no_context"
                    effective = None
                else:
                    status, candidate, message, resolution = _resolve_followup(question, session, backend)
                    if status == "clarify":
                        response = _clarify_response(question, message or "请明确追问。")
                        effective = None
                    else:
                        effective = candidate
                        used_context = status == "resolved"
            if effective is not None:
                response = runner(effective, backend=backend, service=service)
            if not isinstance(response, dict) or response.get("status") not in {
                "ok", "partial", "clarify", "unsupported", "needs_review", "error"
            }:
                raise RuntimeError("invalid V3 result")
            if effective is not None and response["status"] in {"ok", "partial"}:
                session.last_effective_question = effective
                session.last_tool = response.get("selected_tool")
                session.last_status = response.get("status")
                evidence = (response.get("tool_result") or {}).get("evidence") or {}
                session.last_report_id = evidence.get("report_id")
            elif effective is not None and response["status"] == "clarify" and response.get("selected_tool") == "delivery_risk_prediction":
                # Allow filling incomplete prediction features on the next turn;
                # never persist model results or silently infer missing features.
                session.last_effective_question = effective
                session.last_tool = "delivery_risk_prediction"
                session.last_status = "clarify"
            elif effective is not None and not contextual:
                # Do not silently fall back to unrelated stale context.
                session.last_effective_question = None
                session.last_tool = None
                session.last_status = None
                session.last_report_id = None
            session.turn_count += 1
            return {**response, "question": question,
                    "conversation_id": session.conversation_id,
                    "effective_question": effective,
                    "context_used": used_context,
                    "context_resolution": resolution,
                    "session_turn": session.turn_count}
        finally:
            store.mark_busy(session, False)
