"""Idempotent, narrow V3.3 wiring; leaves V1/V2 endpoints untouched."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = {
    "app/v3_tool_service.py": [
        (
            "                 report_runner: Callable[[str], dict] | None = None):",
            "                 report_runner: Callable[[str], dict] | None = None,\n"
            "                 allow_sql_repair: bool = False):",
        ),
        (
            "        self._report_runner = report_runner or existing_report_runner",
            "        self._report_runner = report_runner or existing_report_runner\n"
            "        self.allow_sql_repair = bool(allow_sql_repair)",
        ),
        (
            "            raw = self._sql_runner(question.strip(), self.db_path, backend=self.sql_backend)",
            "            if self.allow_sql_repair:\n"
            "                from app.v3_sql_recovery import run_v3_sql_recovery\n"
            "                raw = run_v3_sql_recovery(\n"
            "                    question.strip(), self.db_path, backend=self.sql_backend,\n"
            "                    runner=self._sql_runner,\n"
            "                )\n"
            "            else:\n"
            "                raw = self._sql_runner(question.strip(), self.db_path, backend=self.sql_backend)",
        ),
        (
            '            "automatic_repair": False,',
            '            "automatic_repair": raw.get("automatic_repair") is True,\n'
            '            "recovery": raw.get("recovery") if isinstance(raw.get("recovery"), dict) else None,',
        ),
    ],
    "app/v3_api.py": [
        (
            '    service = V3ToolService(model=model, metadata=metadata, sql_backend=backend)',
            '    allow_repair = os.getenv("DATAPILOT_V3_SQL_REPAIR_ENABLED", "0").strip().lower() in {"1", "true", "yes"}\n'
            '    service = V3ToolService(\n'
            '        model=model, metadata=metadata, sql_backend=backend,\n'
            '        allow_sql_repair=allow_repair,\n'
            '    )',
        ),
    ],
    "app/v3_routing.py": [
        (
            '            "route_usage": usage, "automatic_repair": False,',
            '            "route_usage": usage,\n'
            '            "automatic_repair": (result["status"] in {"ok", "partial"}\n'
            '                                 and result.get("evidence", {}).get("automatic_repair") is True),',
        ),
    ],
}


def install(root: Path = ROOT) -> list[str]:
    """Validate ALL replacements first; backup and write only after validation."""
    edits = []
    results = []
    for filename, substitutions in PATCHES.items():
        path = root / filename
        if not path.is_file():
            raise RuntimeError(f"Missing: {filename}")
        original = path.read_bytes()
        newline = b"\r\n" if b"\r\n" in original else b"\n"
        raw = original.replace(b"\r\n", b"\n")
        text = raw.decode("utf-8-sig")
        changed = text
        for old, new in substitutions:
            if changed.count(new) == 1:
                continue
            if changed.count(old) != 1:
                raise RuntimeError(f"Unexpected source structure: {filename} / {old[:55]}")
            changed = changed.replace(old, new, 1)
        if changed == text:
            results.append(f"Already installed: {filename}")
        else:
            edits.append((path, original, changed.encode("utf-8").replace(b"\n", newline)))
            results.append(f"Updated: {filename}")
    for path, original, revised in edits:
        backup = path.with_name(path.name + ".before_v3_sql_recovery.bak")
        if not backup.exists():
            backup.write_bytes(original)
        path.write_bytes(revised)
    return results


if __name__ == "__main__":
    for message in install():
        print(message)
