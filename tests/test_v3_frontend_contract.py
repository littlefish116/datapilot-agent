"""Static UI contract checks; real-browser interaction requires a separate smoke."""
from pathlib import Path

import pytest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1] / "frontend"


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def html():
    parser = Markup()
    parser.feed((ROOT / "v3.html").read_text(encoding="utf-8"))
    return parser


def js():
    return (ROOT / "v3.js").read_text(encoding="utf-8")


def test_no_external_scripts_or_inline_code():
    doc = html()
    scripts = [attrs for tag, attrs in doc.tags if tag == "script"]
    assert len(scripts) == 1 and scripts[0].get("src") == "/v3/app.js"
    assert not any(tag == "style" for tag, attrs in doc.tags)
    assert all(not attr.startswith("on") for tag, attrs in doc.tags for attr in attrs)


@pytest.mark.parametrize("element_id", [
    "messages", "chat-form", "question", "send", "new-chat-side", "new-chat-top",
    "error-banner", "session-state", "composer-note",
])
def test_required_dom_ids(element_id):
    assert any(attrs.get("id") == element_id for tag, attrs in html().tags)


def test_chart_metrics_and_session_context_wiring():
    code = js()
    for contract in [
        'conversation_id', 'data.context_used', 'data.effective_question',
        'data.tool_result', 'data.automatic_repair', 'ev.recovery',
        'result.data.rows', 'data.truncated', 'Number.isFinite',
        "'line'", "'bar'", 'data.risk_score',
    ]:
        assert contract in code, contract


def test_sent_only_from_form_and_keyboard_guard():
    code = js()
    assert "form.addEventListener('submit'" in code
    assert "event.isComposing" in code
    assert "!event.shiftKey" in code
    assert "if (busy) return" in code
    assert "newChatButtons.forEach" in code


def test_no_script_injection_primitives():
    code = js()
    for danger in ('.innerHTML', 'eval(', 'new Function(', 'localStorage', 'document.write(', 'insertAdjacentHTML'):
        assert danger not in code
    assert "textContent" in code
    assert "document.createElementNS" in code


def test_prediction_shown_as_uncalibrated_score():
    code = js()
    assert '订单延期风险模型评分（非概率）' in code
    assert '未经概率校准' in code
    assert "data.risk_score.toFixed(6)" in code


def test_truncated_not_claimed_complete():
    code = js()
    assert '查询结果已截断' in code
    assert '数据可能不完整' in code
    assert "data.truncated" in code


def test_browser_only_calls_v3_api_not_v1_or_v2():
    code = js()
    assert "fetch('/api/v3/chat'" in code
    assert 'fetch("/api/chat"' not in code
    assert "fetch('/api/v2/query'" not in code


def test_new_chat_does_not_attempt_reuse_existing_id():
    code = js()
    assert 'conversationId = null;' in code
    assert "if (conversationId) payload.conversation_id = conversationId" in code


def test_chart_does_not_reaggregate_results():
    code = js()
    assert '直接绘制工具返回的数据，不进行重新统计' in code
    assert 'rows.slice(0, 24)' in code
