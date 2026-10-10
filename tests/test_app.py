"""Offline tests for the dashboard server (reviewring.app) and its static page.

A real server is started on a free loopback port and queried over HTTP on
127.0.0.1. No request leaves the machine, and no saved response, identifier,
or API key is used.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reviewring import app, live  # noqa: E402

MARKER_BUSINESS = "0x00000000000000aa:0x00000000000000a1"
MARKER_REVIEWER = "100000000000000000001"


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmp.name) / "raw"
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self._tmp.cleanup()

    def start(self) -> None:
        def refuse(*args, **kwargs):
            raise AssertionError("SerpApi must not be reached from these tests")

        state = live.LiveState(search=refuse, is_cached=refuse, key_configured=lambda: False)
        self.server = app.create_server(port=0, raw_dir=self.raw_dir, live=state)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def get(self, path: str):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read().decode("utf-8")

    def save_review_page(self) -> None:
        self.raw_dir.mkdir()
        page = {
            "search_parameters": {"engine": "google_maps_reviews", "data_id": MARKER_BUSINESS},
            "reviews": [{"review_id": "a-1", "rating": 5.0, "user": {"contributor_id": MARKER_REVIEWER}}],
        }
        (self.raw_dir / "google_maps_reviews_bbbbbbbbbbbbbbbb.json").write_text(json.dumps(page), encoding="utf-8")

    def test_starts_on_loopback_and_serves_the_page_and_assets(self):
        self.start()
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path, content_type in (("/", "text/html"), ("/static/app.css", "text/css"), ("/static/app.js", "text/javascript")):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(content_type))
                self.assertTrue(body)

    def test_every_response_carries_the_security_headers(self):
        self.start()
        for path in ("/", "/api/view?mode=synthetic", "/nope"):
            with self.subTest(path=path):
                _, headers, _ = self.get(path)
                self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_favicon_request_is_answered_with_no_content(self):
        self.start()
        status, headers, body = self.get("/favicon.ico")
        self.assertEqual((status, body), (204, ""))
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    def test_synthetic_view_is_served_without_any_saved_data(self):
        self.start()  # raw_dir does not exist
        status, _, body = self.get("/api/view?mode=synthetic")
        view = json.loads(body)
        self.assertEqual((status, view["mode"], view["synthetic"], view["available"]), (200, "synthetic", True, True))

    def test_missing_saved_data_is_an_explicit_state_not_an_error(self):
        self.start()
        status, _, body = self.get("/api/view?mode=real")
        view = json.loads(body)
        self.assertEqual((status, view["available"]), (200, False))
        self.assertNotIn(self._tmp.name, body)

    def test_real_view_is_aggregated_and_is_the_default_mode(self):
        self.save_review_page()
        self.start()
        for path in ("/api/view?mode=real", "/api/view"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                view = json.loads(body)
                self.assertEqual((status, view["mode"], view["network"]["aggregated"]), (200, "real", True))
                for private in (MARKER_BUSINESS, MARKER_REVIEWER, "a-1", "bbbbbbbb", ".json"):
                    self.assertNotIn(private, body)

    def test_unknown_mode_and_unknown_paths_are_rejected(self):
        self.save_review_page()
        self.start()
        self.assertEqual(self.get("/api/view?mode=other")[0], 400)
        for path in ("/nope", "/static/app.py", "/static/../app.py", "/.env", "/data/raw/", "/static/"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(MARKER_BUSINESS, body)

    def test_a_failure_while_building_a_view_is_reported_without_details(self):
        self.start()
        secret = "PRIVATE-DETAIL-0001"
        original = app.synthetic_view
        app.synthetic_view = lambda: (_ for _ in ()).throw(RuntimeError(secret))
        try:
            status, _, body = self.get("/api/view?mode=synthetic")
        finally:
            app.synthetic_view = original
        self.assertEqual(status, 500)
        self.assertNotIn(secret, body)


class StaticPageTests(unittest.TestCase):
    """The page must load nothing from outside and build no markup from data."""

    def setUp(self):
        self.sources = {name: (app.WEB_DIR / name).read_text(encoding="utf-8") for name in ("index.html", "app.css", "app.js")}

    def test_no_external_resource_is_referenced(self):
        for name, text in self.sources.items():
            with self.subTest(name=name):
                urls = set(re.findall(r"https?://[^\s\"'`)]+", text))
                # The SVG namespace is an identifier, not a request.
                self.assertLessEqual(urls, {"http://www.w3.org/2000/svg"})
                self.assertNotIn("@import", text)
                self.assertNotRegex(text, r"url\(")

    def test_page_has_no_inline_script_or_style_and_inserts_text_only(self):
        html, script = self.sources["index.html"], self.sources["app.js"]
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"\sstyle=|\son\w+=")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(unsafe, script)
        # One fetch call, in the request wrapper, and every path it is given is
        # a same-origin API path.
        self.assertEqual(re.findall(r"fetch\(([^)]*)\)", script), ["path, options"])
        paths = re.findall(r"(?:request|post)\(\s*[`\"]([^`\"]*)", script)
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            self.assertTrue(path.startswith("/api/"), path)
        for storage in ("localStorage", "sessionStorage", "document.cookie", "sendBeacon", "WebSocket"):
            self.assertNotIn(storage, script)

    def test_footer_states_network_use_accurately_for_each_mode(self):
        script = self.sources["app.js"]
        self.assertIn('id="network-use"', self.sources["index.html"])
        self.assertNotIn("makes no outside request", self.sources["index.html"])
        self.assertIn("makes no outside request", script)
        self.assertIn("the local ReviewRing server contacts SerpApi, and only after you confirm", script)
        self.assertIn('mode === "investigate" ? NETWORK_USE.investigate : NETWORK_USE.offline', script)

    def test_collected_sample_banner_says_it_combines_unrelated_saved_data(self):
        self.assertIn("from all searches and investigations, which may be unrelated", self.sources["app.js"])

    def test_overlap_panel_renders_evidence_and_limits_from_the_server(self):
        script = self.sources["app.js"]
        self.assertIn('ind.evidence.assessed ? "No group found." : "Overlap not assessed."', script)
        for expected in ("What was observed", "What this does not show", "ind.evidence.observed_statements", "ind.evidence.limit_statements"):
            self.assertIn(expected, script)
        # Member lists and the highlight button exist only for groups the server chose to describe.
        self.assertIn("if (group.members)", script)

    def test_page_count_control_is_bounded_and_explained(self):
        html, script = self.sources["index.html"], self.sources["app.js"]
        self.assertIn('id="pages" name="pages" type="number" min="1" max="4" value="1"', html)
        self.assertIn("A further page is requested only if the previous page provides a next-page token", html)
        self.assertIn("history_limit, pages }", script)
        self.assertIn("c.page_statement", script)

    def test_synthetic_mode_is_conspicuously_labelled(self):
        self.assertIn("SYNTHETIC DEMO", self.sources["app.js"])
        self.assertIn("fictional", self.sources["app.js"])

    def test_viewing_code_never_imports_the_serpapi_client_or_loads_a_key(self):
        # Only reviewring.live (live investigations) reaches the client.
        for module in ("app.py", "views.py", "loader.py", "indicators.py", "synthetic.py"):
            with self.subTest(module=module):
                text = (app.WEB_DIR.parent / module).read_text(encoding="utf-8")
                for forbidden in ("import serpapi_client", "from reviewring.serpapi_client", "httpx", "SERPAPI_API_KEY", "load_dotenv"):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
