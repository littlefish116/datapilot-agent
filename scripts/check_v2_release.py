"""Read-only preflight: run after pinning all direct dependencies, before tag.

No Git writes, network calls, or model calls are made here.
"""
from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_sqlglot(requirements: str, installed_version: str | None) -> tuple[bool, str]:
    pins = re.findall(r"^\s*sqlglot\s*==\s*([^\s#;]+)", requirements, re.I | re.M)
    if not pins:
        return False, "requirements.txt 尚未精确固定 sqlglot；请用 python -m pip show sqlglot 查询本机版本后添加 sqlglot==实际版本"
    if len(pins) != 1:
        return False, "requirements.txt 中 sqlglot 版本声明存在重复"
    if installed_version is None:
        return False, "当前 Python 环境没有安装 sqlglot"
    if pins[0] != installed_version:
        return False, f"sqlglot 依赖版本 {pins[0]} 与实际安装版本 {installed_version} 不一致"
    return True, f"sqlglot=={installed_version} 已列入 requirements.txt，并与当前环境一致"


def main() -> int:
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8-sig")
    try:
        installed = importlib.metadata.version("sqlglot")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    ok, message = check_sqlglot(req, installed)
    print(("PASS: " if ok else "BLOCK: ") + message)
    if not ok:
        return 1
    print("注意：这只检查当前环境的依赖版本一致性，不代替全新环境重装与全量回归。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
