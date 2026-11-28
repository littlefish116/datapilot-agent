"""Idempotently register the opt-in V3 router without rewriting V1/V2 logic."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
BACKUP = ROOT / "app" / "main.py.before_v3_api.bak"
IMPORT_ANCHOR = "from app.v2_api import router as v2_router"
INCLUDE_ANCHOR = "app.include_router(v2_router)"
NEW_IMPORT = "from app.v3_api import router as v3_router"
NEW_INCLUDE = "app.include_router(v3_router)"


def install(main_path: Path = MAIN, backup_path: Path = BACKUP) -> str:
    raw = main_path.read_bytes()
    text = raw.decode("utf-8-sig")
    has_import, has_include = NEW_IMPORT in text, NEW_INCLUDE in text
    if has_import and has_include:
        return "Already installed"
    if has_import != has_include:
        raise RuntimeError("V3 路由安装不完整，拒绝猜测修改")
    if text.count(IMPORT_ANCHOR) != 1 or text.count(INCLUDE_ANCHOR) != 1:
        raise RuntimeError("V1/V2 主文件结构与预期不同，拒绝自动修改")
    if backup_path.exists():
        raise RuntimeError("已有同名备份却未找到 V3 路由，拒绝覆盖备份")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    updated = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + newline + NEW_IMPORT, 1)
    updated = updated.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + newline + NEW_INCLUDE, 1)
    # Preserve original newline convention.
    updated = updated.replace("\r\n", "\n").replace("\n", newline)
    backup_path.write_bytes(raw)
    main_path.write_bytes(updated.encode("utf-8"))
    return f"Installed V3 router in {main_path} (backup: {backup_path})"


if __name__ == "__main__":
    print(install())
