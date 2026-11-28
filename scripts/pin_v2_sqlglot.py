"""Pin the *installed, tested* sqlglot version in requirements.txt.

No network and no pip installation. Idempotent, refuses conflicting pins.
"""
from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"


def with_sqlglot_pin(source: str, version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
        raise ValueError("sqlglot 安装版本无效")
    lines = [line for line in source.splitlines()
             if re.match(r"^\s*sqlglot\b", line, re.I)]
    if lines:
        if len(lines) == 1 and re.fullmatch(r"\s*sqlglot\s*==\s*" + re.escape(version) + r"\s*(?:#.*)?", lines[0], re.I):
            return source
        raise ValueError("requirements.txt 已有不同的 sqlglot 声明；请人工审核，拒绝覆盖")
    return source.rstrip("\n") + "\nsqlglot==" + version + "\n"


def main() -> None:
    version = importlib.metadata.version("sqlglot")
    previous = REQ.read_text(encoding="utf-8-sig")
    updated = with_sqlglot_pin(previous, version)
    if previous == updated:
        print(f"Unchanged: requirements.txt (sqlglot=={version})")
        return
    backup = REQ.with_name("requirements.txt.before_v2_release.bak")
    if backup.exists():
        raise FileExistsError(f"备份已存在，拒绝覆盖: {backup}")
    backup.write_text(previous, encoding="utf-8")
    REQ.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Pinned sqlglot=={version} (backup: {backup.name})")


if __name__ == "__main__":
    main()
