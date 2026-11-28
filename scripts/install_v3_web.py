"""Conservative V3.4 installer: only add a separate router to app/main.py.

Run once from the project root: python scripts/install_v3_web.py
Creates a .bak backup; does not change existing V1/V2/V3 route handlers.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
BACKUP = ROOT / "app" / "main.py.before_v3_web.bak"

OLD_IMPORT = b"from app.v3_api import router as v3_router"
NEW_IMPORT = b"from app.v3_web import router as v3_web_router"
OLD_INCLUDE = b"app.include_router(v3_router)"
NEW_INCLUDE = b"app.include_router(v3_web_router)"


def install(main: Path = MAIN, backup: Path = BACKUP) -> str:
    main, backup = Path(main), Path(backup)
    content = main.read_bytes()
    imported = NEW_IMPORT in content
    included = NEW_INCLUDE in content
    if imported and included:
        if content.count(NEW_IMPORT) != 1 or content.count(NEW_INCLUDE) != 1:
            raise RuntimeError("V3 web router registration duplicated; inspect manually")
        return f"Already installed: {main}"
    if imported != included:
        raise RuntimeError("Partial V3 web installation; do not edit automatically")
    newline = b"\r\n" if b"\r\n" in content else b"\n"
    if content.count(OLD_IMPORT + newline) != 1 or content.count(OLD_INCLUDE + newline) != 1:
        raise RuntimeError("Unexpected app/main.py layout; no files changed")
    if backup.exists():
        raise RuntimeError(f"Backup exists, refusing to overwrite: {backup}")
    updated = content.replace(
        OLD_IMPORT + newline,
        OLD_IMPORT + newline + NEW_IMPORT + newline,
        1,
    ).replace(
        OLD_INCLUDE + newline,
        OLD_INCLUDE + newline + NEW_INCLUDE + newline,
        1,
    )
    if updated == content:
        raise RuntimeError("No changes detected; refusing to install")
    backup.write_bytes(content)
    try:
        main.write_bytes(updated)
    except Exception:
        main.write_bytes(content)
        raise
    return f"Installed V3.4 web router: {main} (backup: {backup})"


if __name__ == "__main__":
    print(install())
