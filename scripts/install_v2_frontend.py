"""Install a standalone /v2 demo without replacing the existing V1 homepage.

Run once: python scripts/install_v2_frontend.py
Safe to rerun; keeps original bytes in backups when changes are needed.
"""
from __future__ import annotations

from pathlib import Path

ROUTE_MARK = '@app.get("/v2", include_in_schema=False)'
ROUTE_TEMPLATE = (
    '\n\n@app.get("/v2", include_in_schema=False)\n'
    'def v2_demo_home():\n'
    '    return FileResponse(ROOT / "frontend" / "v2.html")\n'
)
LINK_MARK = 'data-datapilot-v2-entry'
LINK_HTML = (
    '    <a data-datapilot-v2-entry href="/v2" '
    'style="display:inline-block;margin-top:12px;padding:7px 13px;'
    'border:1px solid #7b98bb;border-radius:8px;color:#e3edff;'
    'text-decoration:none;font-size:13px">打开 V2 动态查询工作台</a>\n'
)


def _decode(raw: bytes) -> tuple[str, str, bytes]:
    bom = b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
    text = raw[len(bom):].decode('utf-8')
    eol = '\r\n' if '\r\n' in text else '\n'
    return text, eol, bom


def install(root: Path) -> str:
    main = root / 'app' / 'main.py'
    home = root / 'frontend' / 'index.html'
    v2 = root / 'frontend' / 'v2.html'
    if not (main.is_file() and home.is_file() and v2.is_file()):
        raise ValueError('Missing app/main.py, frontend/index.html or frontend/v2.html')
    main_raw = main.read_bytes()
    home_raw = home.read_bytes()
    main_text, main_eol, main_bom = _decode(main_raw)
    home_text, home_eol, home_bom = _decode(home_raw)
    route_found = ROUTE_MARK in main_text
    link_found = LINK_MARK in home_text
    if route_found and link_found:
        return 'V2 frontend already installed; nothing changed'
    if route_found or link_found:
        raise ValueError('Partial V2 frontend integration detected; inspect files manually')
    if 'from fastapi.responses import FileResponse' not in main_text or 'ROOT =' not in main_text:
        raise ValueError('Existing V1 FileResponse/ROOT markers not found')
    if main_text.count('app = FastAPI(') != 1 or home_text.count('</header>') != 1:
        raise ValueError('Unexpected V1 app/frontend structure; refused to edit')
    if not ('@app.get("/", include_in_schema=False)' in main_text and 'def home()' in main_text):
        raise ValueError('Existing V1 home route not found')
    main_backup = main.with_name('main.py.before_v2_frontend.bak')
    home_backup = home.with_name('index.html.before_v2_frontend.bak')
    if main_backup.exists() or home_backup.exists():
        raise ValueError('Frontend installer backup already exists; inspect manually before retry')
    route = ROUTE_TEMPLATE.replace('\n', main_eol)
    updated_main = main_text.rstrip('\r\n') + route
    link = LINK_HTML.replace('\n', home_eol)
    updated_home = home_text.replace('</header>', link + '</header>', 1)
    main_backup.write_bytes(main_raw)
    home_backup.write_bytes(home_raw)
    try:
        main.write_bytes(main_bom + updated_main.encode('utf-8'))
        home.write_bytes(home_bom + updated_home.encode('utf-8'))
    except OSError:
        main.write_bytes(main_raw)
        home.write_bytes(home_raw)
        raise
    return 'Installed /v2 frontend; two local backups saved'


if __name__ == '__main__':
    try:
        print(install(Path(__file__).resolve().parents[1]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
