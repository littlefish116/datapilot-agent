"""Offline installer and user-interface contract tests. No DeepSeek requests."""
from __future__ import annotations

import importlib.util
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.install_v2_frontend import install

ROOT = Path(__file__).resolve().parents[1]


class V2FrontendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for sub in ('app', 'frontend'):
            (self.root / sub).mkdir()
        (self.root / 'app' / 'main.py').write_text(
            'from fastapi import FastAPI\r\n'
            'from fastapi.responses import FileResponse\r\n'
            'from pathlib import Path\r\n'
            'ROOT = Path(__file__).resolve().parents[1]\r\n'
            'app = FastAPI()\r\n'
            '@app.get("/", include_in_schema=False)\r\n'
            'def home():\r\n'
            '    return FileResponse(ROOT / "frontend" / "index.html")\r\n',
            encoding='utf-8', newline=''
        )
        (self.root / 'frontend' / 'index.html').write_text(
            '<html><header><h1>V1</h1></header><body>V1 existing content</body></html>\r\n',
            encoding='utf-8', newline=''
        )
        shutil.copy2(ROOT / 'frontend' / 'v2.html', self.root / 'frontend' / 'v2.html')

    def tearDown(self):
        self.tmp.cleanup()

    def test_installs_standalone_route_and_link(self):
        self.assertIn('Installed', install(self.root))
        main = (self.root / 'app' / 'main.py').read_text(encoding='utf-8')
        index = (self.root / 'frontend' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('@app.get("/v2", include_in_schema=False)', main)
        self.assertIn('frontend" / "v2.html"', main)
        self.assertIn('data-datapilot-v2-entry', index)
        self.assertIn('V1 existing content', index)
        self.assertIn('def home():', main)

    def test_real_fastapi_get_routes(self):
        from fastapi.testclient import TestClient

        install(self.root)
        module_path = self.root / 'app' / 'main.py'
        spec = importlib.util.spec_from_file_location('isolated_v2_frontend_test_main', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TestClient(module.app) as client:
            original = client.get('/')
            new_page = client.get('/v2')
        self.assertEqual(original.status_code, 200)
        self.assertIn('V1 existing content', original.text)
        self.assertEqual(new_page.status_code, 200)
        self.assertIn('DataPilot V2', new_page.text)
        self.assertIn('生成并查询', new_page.text)

    def test_idempotent(self):
        install(self.root)
        main = (self.root / 'app' / 'main.py').read_bytes()
        index = (self.root / 'frontend' / 'index.html').read_bytes()
        self.assertIn('already installed', install(self.root))
        self.assertEqual(main, (self.root / 'app' / 'main.py').read_bytes())
        self.assertEqual(index, (self.root / 'frontend' / 'index.html').read_bytes())

    def test_backup_bytes_exact(self):
        a = (self.root / 'app' / 'main.py').read_bytes()
        b = (self.root / 'frontend' / 'index.html').read_bytes()
        install(self.root)
        self.assertEqual(a, (self.root / 'app' / 'main.py.before_v2_frontend.bak').read_bytes())
        self.assertEqual(b, (self.root / 'frontend' / 'index.html.before_v2_frontend.bak').read_bytes())

    def test_crlf_preserved_in_main(self):
        install(self.root)
        raw = (self.root / 'app' / 'main.py').read_bytes()
        self.assertIn(b'\r\n', raw)
        self.assertNotIn(b'\n\n@app.get', raw)

    def test_partial_install_refused(self):
        a = self.root / 'app' / 'main.py'
        a.write_text(a.read_text(encoding='utf-8') + '\n@app.get("/v2", include_in_schema=False)\n', encoding='utf-8')
        b = (self.root / 'frontend' / 'index.html').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Partial'):
            install(self.root)
        self.assertEqual(b, (self.root / 'frontend' / 'index.html').read_bytes())

    def test_existing_backup_refused(self):
        (self.root / 'app' / 'main.py.before_v2_frontend.bak').write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'backup'):
            install(self.root)

    def test_missing_main_marker_refused(self):
        a = self.root / 'app' / 'main.py'
        a.write_text('from fastapi.responses import FileResponse\nROOT = 1\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            install(self.root)

    def test_no_network_call_without_submission(self):
        html = (ROOT / 'frontend' / 'v2.html').read_text(encoding='utf-8')
        self.assertIn("form.addEventListener('submit'", html)
        self.assertIn("fetch('/api/v2/query'", html)
        self.assertNotIn('DOMContentLoaded', html)

    def test_renders_server_strings_as_text(self):
        html = (ROOT / 'frontend' / 'v2.html').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', html)
        self.assertIn('td.textContent', html)
        self.assertIn('detail.textContent', html)
        self.assertIn('sqlCode.textContent', html)

    def test_handles_all_statuses_and_truncation(self):
        html = (ROOT / 'frontend' / 'v2.html').read_text(encoding='utf-8')
        for code in ('ok', 'partial', 'needs_review', 'clarify', 'unsupported', 'error'):
            self.assertIn("state === '" + code + "'", html)
        self.assertIn('execution.truncated', html)

    def test_html_is_standalone_no_cdn(self):
        html = (ROOT / 'frontend' / 'v2.html').read_text(encoding='utf-8')
        self.assertNotIn('<script src=', html)
        self.assertNotIn('<link rel="stylesheet" href=', html)
        self.assertIn('<meta charset="utf-8">', html)

    def test_browser_guards_local_only_404_403(self):
        html = (ROOT / 'frontend' / 'v2.html').read_text(encoding='utf-8')
        self.assertIn('response.status === 404', html)
        self.assertIn('response.status === 403', html)


if __name__ == '__main__':
    unittest.main()
