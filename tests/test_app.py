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

    def get(self, path: str, headers: dict | None = None):
        url = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}{path}", headers=headers or {})
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

    def test_synthetic_expansion_steps_are_served_locally_and_validated(self):
        self.start()
        status, _, body = self.get("/api/view?mode=synthetic&expansions=1")
        view = json.loads(body)
        self.assertEqual((status, view["synthetic"], view["expansion"]["steps"], view["metrics"]["reviewers"]), (200, True, 1, 17))
        self.assertEqual(json.loads(self.get("/api/view?mode=synthetic&expansions=0")[2]), json.loads(self.get("/api/view?mode=synthetic")[2]))
        for bad in ("9", "-1", "abc", "1.5", "01"):
            with self.subTest(expansions=bad):
                self.assertEqual(self.get(f"/api/view?mode=synthetic&expansions={bad}")[0], 400)
        # The parameter belongs to the fictional demo only.
        status, _, body = self.get("/api/view?mode=real&expansions=1")
        self.assertEqual((status, "expansion" in json.loads(body)), (200, False))

    def test_config_reports_whether_a_key_is_configured_and_nothing_else(self):
        self.start()  # this server's state reports no key
        status, headers, body = self.get("/api/config")
        self.assertEqual((status, json.loads(body)), (200, {"hosted_demo": False, "key_configured": False}))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, _, body = self.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertNotIn("key_configured", body)

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

    def test_expansion_is_a_separate_form_with_its_own_cost_check(self):
        html, script = self.sources["index.html"], self.sources["app.js"]
        self.assertIn('id="expansion-form"', html)
        self.assertIn('id="expansion-count" name="expansion-count" type="number" min="1" max="2" value="1"', html)
        self.assertIn('post("/api/expansions/plan", { investigation, businesses })', script)
        self.assertIn('post("/api/expansions", { plan: plan.plan, investigation })', script)
        # Expansion goes through the same confirmation prompt; nothing is sent from the plan step.
        self.assertRegex(script, r"expansions/plan[\s\S]{0,120}askConfirm\(plan")
        self.assertIn("expansion.findings", script)
        self.assertIn("expansion.limits", script)

    def test_synthetic_demo_has_an_explicit_expansion_action(self):
        html, script = self.sources["index.html"], self.sources["app.js"]
        self.assertIn('id="demo-expand" class="primary">Expand network</button>', html)
        self.assertIn("Fictional data. No request is sent and no credit is used.", html)
        self.assertIn('id="demo-reset"', html)
        self.assertIn("/api/view?mode=synthetic&expansions=${steps}", script)
        # Shown for the synthetic view only, and it never posts or spends anything.
        self.assertIn("const expansion = view.synthetic ? view.expansion : null;", script)
        start = script.index("async function loadDemoStep")
        self.assertNotIn("post(", script[start:start + 800])

    def test_synthetic_mode_is_conspicuously_labelled(self):
        self.assertIn('el("strong", {}, "Fictional data")', self.sources["app.js"])
        self.assertIn("fictional", self.sources["app.js"])
        html = self.sources["index.html"]
        # The label is on the strip, the network and the expansion panel, with detail one click away.
        self.assertEqual(html.count('chip chip-fictional'), 2)
        self.assertIn("About the demo data", html)
        self.assertIn("Nothing was collected from SerpApi or Google Maps", html)

    def test_network_selection_has_a_persistent_and_a_temporary_layer(self):
        html, script, css = self.sources["index.html"], self.sources["app.js"], self.sources["app.css"]
        # Hover never changes the pin, and what is shown is the hover or else the pin.
        self.assertIn("const shown = hover || pinned;", script)
        body = script[script.index("function preview(next)"):script.index("function clearSelection()")]
        self.assertNotIn("pinned =", body)
        # A new view starts from the full network.
        self.assertRegex(script, r"current = view;[\s\S]{0,160}pinned = null;\s+hover = null;")
        # Escape and a visible control both clear; the control says what it will do.
        self.assertIn('if (event.key === "Escape" && (pinned || hover)) clearSelection();', script)
        self.assertIn('pinned && pinned.group ? "Show full network" : "Clear selection"', script)
        # Selection is stated in words and drawn as an extra outline, not only as a colour.
        self.assertIn('shown === hover ? "Preview" : shown.group ? "Group focus" : "Selected"', script)
        self.assertIn(".node.selected .pin { display: block; }", css)
        self.assertIn('"aria-pressed": "false"', script)
        self.assertIn('id="group-focus"', html)

    def test_expansion_feedback_comes_from_server_figures_and_moves_only_once(self):
        html, script, css = self.sources["index.html"], self.sources["app.js"], self.sources["app.css"]
        self.assertIn('id="demo-update" class="update" role="status" aria-live="polite"', html)
        self.assertIn("expansion.new_links.length", script)
        self.assertIn("fresh.has(node.id)", script)
        self.assertIn('expansion.available ? "" : " (final step)"', script)
        # The entrance plays only for the drawing made by an expansion step, never on a redraw.
        self.assertRegex(script, r"entering = advanced;\s+show\(view\);\s+entering = false;")
        self.assertIn('if (entering) root.setAttribute("class", "entering");', script)
        # All motion sits behind the reduced-motion preference.
        motion = css[css.index("@media (prefers-reduced-motion: no-preference)"):]
        self.assertEqual(css.count("animation:"), motion.count("animation:"))
        self.assertEqual(css.count("@keyframes"), motion.count("@keyframes"))
        self.assertNotIn("transition", css)
        self.assertNotIn("infinite", css)

    def test_group_focus_exists_only_for_groups_the_server_described(self):
        script = self.sources["app.js"]
        self.assertIn("view.indicators.groups.filter((group) => group.node_ids)", script)
        self.assertIn("ids: new Set(group.node_ids)", script)
        # Emphasis follows drawn lines only: both ends shown, and touching the focused node if there is one.
        self.assertIn("ids.has(link.dataset.source) && ids.has(link.dataset.target)", script)
        self.assertNotIn("force", script.lower().replace("reinforce", ""))
        for moving in ("requestAnimationFrame", "setInterval", "drag", "wheel"):
            self.assertNotIn(moving, script)

    def test_tabs_are_three_independent_workspaces_in_the_agreed_order(self):
        html, script = self.sources["index.html"], self.sources["app.js"]
        order = [html.index(f'id="mode-{name}"') for name in ("investigate", "synthetic", "real")]
        self.assertEqual(order, sorted(order))
        for label in (">Investigate</button>", ">Explore Demo</button>", ">Collected Data</button>"):
            self.assertIn(label, html)
        self.assertIn("It is not one investigation.", script)
        for word in ("Case history", "case management", "Cases"):
            self.assertNotIn(word, html + script)

    def test_landing_tab_follows_the_servers_answer_not_a_guess(self):
        script = self.sources["app.js"]
        self.assertIn('startMode = !hosted && config.key_configured === true ? "investigate" : "synthetic";', script)
        self.assertIn("return MODES.includes(mode) ? mode : startMode;", script)
        # The page never handles a key: the only mention is the instruction naming the variable.
        self.assertNotIn("api_key", script)
        self.assertNotIn("SERPAPI_API_KEY", script)

    def test_the_permanent_caveat_is_never_inside_a_collapsed_section(self):
        html = self.sources["index.html"]
        caveat = "Observed overlap is a pattern to look into. It does not establish coordination, fraud, or any wrongdoing."
        self.assertEqual(html.count(caveat), 1)
        before = html[:html.index(caveat)]
        self.assertEqual(before.count("<details"), before.count("</details>"))
        for collapsed in ("How to read the network", "How to read these figures", "View connections as a table", "What this does not show"):
            self.assertIn(collapsed, html)
        self.assertNotIn("<details open", html)

    def test_viewing_code_never_imports_the_serpapi_client_or_loads_a_key(self):
        # Only reviewring.live (live investigations) reaches the client.
        for module in ("app.py", "views.py", "loader.py", "indicators.py", "synthetic.py"):
            with self.subTest(module=module):
                text = (app.WEB_DIR.parent / module).read_text(encoding="utf-8")
                for forbidden in ("import serpapi_client", "from reviewring.serpapi_client", "httpx", "SERPAPI_API_KEY", "load_dotenv"):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
