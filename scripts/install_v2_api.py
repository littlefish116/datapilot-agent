"""Idempotent, narrowly-scoped addition of the V2 router to app/main.py.

Run from project root: python scripts/install_v2_api.py
Never overwrites existing code wholesale. Backs up main.py on first edit.
"""
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = root / "app" / "main.py"
text = main.read_text(encoding="utf-8-sig")
import_line = "from app.v2_api import router as v2_router"
include_line = "app.include_router(v2_router)"

if import_line in text and include_line in text:
    print("V2 router already installed; nothing changed")
elif import_line in text or include_line in text:
    raise SystemExit("Partial V2 router integration detected; please inspect app/main.py manually")
else:
    marker = "app = FastAPI("
    if text.count(marker) != 1:
        raise SystemExit("Could not safely find the unique FastAPI app constructor")
    # Locate the app constructor's closing ')' without assuming its exact lines.
    # The current project's constructor has no nested calls or parens in strings.
    index = text.index(marker)
    close = text.find("\n)\n", index)
    if close < 0:
        raise SystemExit("Could not safely find end of FastAPI constructor")
    insert_at = close + len("\n)\n")
    updated = import_line + "\n" + text[:insert_at] + "\n" + include_line + "\n" + text[insert_at:]
    backup = main.with_name("main.py.before_v2_api.bak")
    if backup.exists():
        raise SystemExit(f"Backup already exists: {backup}. Inspect before proceeding")
    backup.write_text(text, encoding="utf-8")
    main.write_text(updated, encoding="utf-8")
    print("Installed V2 router into app/main.py")
    print("Saved local backup:", backup)
