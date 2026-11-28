"""Idempotent narrow integration of V3 targeted monthly answer into routing.

Run at project root: python scripts/install_v3_targeted_answer.py
Changes only app/v3_routing.py, preserving original in an untracked .bak.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "v3_routing.py"
BACKUP = ROOT / "app" / "v3_routing.py.before_v3_targeted_answer.bak"

IMPORT_ANCHOR = "from app.v2_response_policy import normalize_unsupported_message"
NEW_IMPORT = "from app.v3_answer_policy import targeted_monthly_report_answer"
RETURN_ANCHOR = '        return {"response": {\n            "status": result["status"], "question": question,'
INTEGRATION = (
    '        report_id = choice["arguments"]["report_id"] if tool == "business_report" else None\n'
    '        focused_answer = targeted_monthly_report_answer(question, result, report_id=report_id)\n'
)
OLD_ANSWER = '            "answer": _answer_from_result(result), "tool_result": result,'
NEW_ANSWER = (
    '            "answer": (focused_answer if focused_answer is not None\n'
    '                       else _answer_from_result(result)), "tool_result": result,'
)


def install(target: Path = TARGET, backup: Path = BACKUP) -> str:
    original = target.read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    normalized = original.replace(b"\r\n", b"\n").decode("utf-8-sig")
    already = (NEW_IMPORT in normalized and INTEGRATION in normalized
               and NEW_ANSWER in normalized and OLD_ANSWER not in normalized)
    if already:
        return f"Already installed: {target}"
    if (normalized.count(IMPORT_ANCHOR) != 1
            or NEW_IMPORT in normalized
            or normalized.count(RETURN_ANCHOR) != 1
            or normalized.count(OLD_ANSWER) != 1
            or 'focused_answer =' in normalized):
        raise RuntimeError("V3 routing structure differs from expected V3.1B; no changes applied")
    changed = normalized.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + NEW_IMPORT, 1)
    changed = changed.replace(RETURN_ANCHOR, INTEGRATION + RETURN_ANCHOR, 1)
    changed = changed.replace(OLD_ANSWER, NEW_ANSWER, 1)
    if changed == normalized:
        raise RuntimeError("patch had no effect")
    output = changed.encode("utf-8").replace(b"\n", newline)
    if not backup.exists():
        backup.write_bytes(original)
    target.write_bytes(output)
    return f"Updated: {target} (backup: {backup.name})"


if __name__ == "__main__":
    print(install())
