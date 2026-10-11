"""Offline tests for the hosted demo: a public, read-only, synthetic-only server.

The server under test is bound to the loopback interface so the tests open no
public port. Nothing here uses a key, saved data, or the network.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reviewring import app  # noqa: E402
from reviewring.synthetic import MAX_EXPANSION_STEPS  # noqa: E402
from reviewring.views import synthetic_view  # noqa: E402

LIVE_ROUTES = (
    "/api/search/plan",
    "/api/search",
    "/api/investigations/plan",
    "/api/investigations",
    "/api/expansions/plan",
    "/api/expansions",
)


class HostedServerTestCase(unittest.TestCase):
    def setUp(self):
        self.server = app.create_hosted_server(0, host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def get(self, path, headers=None):
        return self.call("GET", path, headers=headers)


class HostedDemoTests(HostedServerTestCase):
    def test_page_assets_and_config_are_served(self):
        for path, content_type in (("/", "text/html"), ("/static/app.css", "text/css"), ("/static/app.js", "text/javascript")):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(content_type))
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertTrue(body)
        status, headers, body = self.get("/api/config")
        self.assertEqual((status, json.loads(body)), (200, {"hosted_demo": True}))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.get("/favicon.ico")[0], 204)

    def test_synthetic_journey_matches_the_local_demo_at_every_step(self):
        for steps in range(MAX_EXPANSION_STEPS + 1):
            status, _, body = self.get(f"/api/view?mode=synthetic&expansions={steps}")
            self.assertEqual((status, json.loads(body)), (200, synthetic_view(steps)))
        initial = json.loads(self.get("/api/view?mode=synthetic")[2])
        first = json.loads(self.get("/api/view?mode=synthetic&expansions=1")[2])
        second = json.loads(self.get("/api/view?mode=synthetic&expansions=2")[2])
        self.assertEqual([v["metrics"]["reviewers"] for v in (initial, first, second)], [13, 17, 18])
        self.assertEqual([v["expansion"]["available"] for v in (initial, first, second)], [True, True, False])
        # Reset is just the initial request again, and it is identical every time.
        self.assertEqual(json.loads(self.get("/api/view?mode=synthetic&expansions=0")[2]), initial)
        self.assertEqual(json.loads(self.get("/api/view?mode=synthetic")[2]), initial)

    def test_every_response_is_fictional_and_labelled(self):
        for steps in range(3):
            view = json.loads(self.get(f"/api/view?mode=synthetic&expansions={steps}")[2])
            self.assertEqual((view["mode"], view["synthetic"]), ("synthetic", True))
            for node in view["network"]["nodes"]:
                self.assertTrue(node["id"].startswith("synthetic-"), node["id"])

    def test_real_and_unknown_modes_cannot_be_reached(self):
        for query in (
            "", "mode=real", "mode=investigate", "mode=REAL", "mode=synthetic%00", "mode=", "expansions=1",
            "mode=real&mode=synthetic", "mode=synthetic&mode=real", "mode=synthetic&expansions=1&expansions=2",
            "mode=synthetic&raw_dir=/", "mode=synthetic&extra=1", "mode=synthetic&expansions=9",
            "mode=synthetic&expansions=-1", "mode=synthetic&expansions=", "mode=synthetic&expansions=1.0",
            "mode=synthetic;mode=real", "Mode=synthetic", "mode=synthetic&&", "x",
        ):
            with self.subTest(query=query):
                status, _, body = self.get(f"/api/view?{query}" if query else "/api/view")
                self.assertEqual(status, 400)
                self.assertNotIn('"mode": "real"', body)
                self.assertNotIn("network", body)

    def test_live_and_investigation_routes_do_not_exist(self):
        payload = json.dumps({"query": "bakery in testville", "plan": "x", "business": "x", "investigation": "x"})
        headers = {"Content-Type": "application/json", "X-ReviewRing-Token": "anything"}
        for path in LIVE_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.call("POST", path, body=payload, headers=headers)[0], 405)
                self.assertEqual(self.get(path)[0], 404)
        for path in ("/api/session", "/api/investigations/abc", "/api/investigations/", "/api", "/api/view/", "/api/config/x", "/api/config?x=1"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotIn("token", body)

    def test_no_method_writes_anything(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            for path in ("/", "/api/view?mode=synthetic", "/api/config", "/static/app.js"):
                with self.subTest(method=method, path=path):
                    status, headers, body = self.call(method, path, body="{}", headers={"Content-Type": "application/json"})
                    self.assertEqual(status, 405)
                    self.assertEqual(json.loads(body), {"error": app.HOSTED_ONLY})
                    self.assertNotIn("Allow", headers)
        status, headers, body = self.call("HEAD", "/")
        self.assertEqual((status, body), (200, ""))
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_only_the_three_page_files_can_be_served(self):
        for path in (
            "/static/", "/static/app.py", "/static/index.html", "/static/../app.py", "/static/..%2fapp.py",
            "/static/%2e%2e/live.py", "/static/app.js/../../live.py", "/..%2f..%2f.env", "/.env", "/.git/config",
            "/src/reviewring/live.py", "/README.md", "/data/raw/", "//etc/passwd", "/static/app.js?x=1",
            "/static/app.css%00.py", "/index.html", "/main.py", "/C:/Windows/win.ini", "/static\\..\\live.py",
        ):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 404, path)
                self.assertEqual(json.loads(body), {"error": "Not found."})

    def test_host_header_is_not_required_to_be_local_but_nothing_else_opens_up(self):
        # A public server is reached under its own domain name.
        status, _, body = self.get("/api/view?mode=synthetic", headers={"Host": "reviewring.example.invalid", "Origin": "https://elsewhere.invalid"})
        self.assertEqual((status, json.loads(body)["synthetic"]), (200, True))
        self.assertEqual(self.get("/api/view?mode=real", headers={"Host": "127.0.0.1"})[0], 400)

    def test_errors_carry_no_detail(self):
        secret = "C:\\private\\path SECRET-VALUE"
        with mock.patch.object(app, "synthetic_view", side_effect=RuntimeError(secret)):
            status, _, body = self.get("/api/view?mode=synthetic")
        self.assertEqual((status, json.loads(body)), (500, {"error": "The request could not be completed."}))
        self.assertNotIn("SECRET", body)
        self.assertNotIn("Traceback", body)

    def test_a_stalled_connection_is_dropped(self):
        self.assertEqual(app._HostedHandler.timeout, app.HOSTED_REQUEST_TIMEOUT_SECONDS)
        self.assertLessEqual(app.HOSTED_REQUEST_TIMEOUT_SECONDS, 30)


class HostedIsolationTests(unittest.TestCase):
    def test_hosted_demo_runs_without_the_client_a_key_saved_data_or_any_dependency(self):
        """In a fresh interpreter where the live code and third-party packages cannot be imported at all."""
        script = r"""
import http.client, json, sys, threading

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("httpx", "dotenv", "pydantic", "pandas") or name in ("reviewring.live", "reviewring.serpapi_client", "reviewring.investigation"):
            raise ImportError(f"blocked in the hosted demo: {name}")
        return None

sys.meta_path.insert(0, Blocked())
import socket
real_connect = socket.socket.connect
def guarded(self, address):
    if address[0] not in ("127.0.0.1", "localhost"):
        raise AssertionError("outbound connection attempted")
    return real_connect(self, address)
socket.socket.connect = guarded

from reviewring import app
server = app.create_hosted_server(0, host="127.0.0.1")
threading.Thread(target=server.serve_forever, daemon=True).start()
seen = []
for path in ("/", "/api/config", "/api/view?mode=synthetic", "/api/view?mode=synthetic&expansions=1", "/api/view?mode=synthetic&expansions=2", "/api/view?mode=real"):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    c.request("GET", path)
    r = c.getresponse(); r.read(); seen.append(r.status); c.close()
server.shutdown(); server.server_close()
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("httpx", "dotenv") or m in ("reviewring.live", "reviewring.serpapi_client", "reviewring.investigation"))
print(json.dumps({"statuses": seen, "loaded": loaded}))
"""
        environment = {k: v for k, v in os.environ.items() if k not in ("SERPAPI_API_KEY", "REVIEWRING_PSEUDONYM_KEY")}
        environment["PYTHONPATH"] = str(ROOT / "src")
        with mock.patch.dict(os.environ, {}, clear=False):
            result = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=environment, cwd=str(ROOT / "tests")
            )
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report, {"statuses": [200, 200, 200, 200, 200, 400], "loaded": []})


class _FakeServer:
    created: list[tuple] = []

    def __init__(self, address, handler):
        self.server_address = address
        _FakeServer.created.append((address, handler))

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


class StartupTests(unittest.TestCase):
    def run_main(self, environment, argv=()):
        _FakeServer.created = []
        cleaned = {k: v for k, v in os.environ.items() if k not in (app.HOSTED_ENV_VAR, "PORT")}
        with mock.patch.dict(os.environ, {**cleaned, **environment}, clear=True), \
                mock.patch.object(app, "ThreadingHTTPServer", _FakeServer), \
                mock.patch("builtins.print") as printed:
            code = app.main(list(argv))
        return code, _FakeServer.created, " ".join(str(call.args[0]) for call in printed.call_args_list)

    def test_default_is_the_local_dashboard_on_loopback(self):
        code, created, output = self.run_main({})
        self.assertEqual(code, 0)
        (address, handler), = created
        self.assertEqual(address, ("127.0.0.1", 8765))
        self.assertIsNot(handler, app._HostedHandler)
        self.assertIn("http://127.0.0.1:8765", output)
        # PORT alone changes nothing: it is read only by the hosted demo.
        _, created, _ = self.run_main({"PORT": "9999"})
        self.assertEqual(created[0][0], ("127.0.0.1", 8765))

    def test_hosted_demo_binds_publicly_only_when_explicitly_enabled(self):
        code, created, output = self.run_main({app.HOSTED_ENV_VAR: "1", "PORT": "12345"})
        self.assertEqual(code, 0)
        self.assertEqual(created, [(("0.0.0.0", 12345), app._HostedHandler)])
        self.assertIn("synthetic data only", output)
        _, created, _ = self.run_main({app.HOSTED_ENV_VAR: "1"})
        self.assertEqual(created, [(("0.0.0.0", app.HOSTED_DEFAULT_PORT), app._HostedHandler)])
        # The local --port option does not apply to the hosted demo.
        _, created, _ = self.run_main({app.HOSTED_ENV_VAR: "1", "PORT": "12345"}, ["--port", "7000"])
        self.assertEqual(created[0][0], ("0.0.0.0", 12345))

    def test_an_unclear_setting_or_port_starts_nothing(self):
        for value in ("true", "yes", "0", "on", " 1", "1 "):
            with self.subTest(setting=value):
                code, created, output = self.run_main({app.HOSTED_ENV_VAR: value})
                self.assertEqual((code, created), (1, []))
                self.assertIn(app.HOSTED_ENV_VAR, output)
        for port in ("", "abc", "0", "70000", "-1", "80; rm"):
            with self.subTest(port=port):
                code, created, _ = self.run_main({app.HOSTED_ENV_VAR: "1", "PORT": port})
                self.assertEqual((code, created), (1, []))

    def test_page_script_hides_real_data_controls_when_hosted(self):
        script = (app.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (hosted) return "synthetic";', script)
        self.assertIn('$("mode-real").hidden = true;', script)
        self.assertIn('$("mode-investigate").hidden = true;', script)
        self.assertIn("This public demo serves only this fictional walkthrough.", script)
        # Hosted always opens on the demo, whatever the server says about a key.
        self.assertIn('startMode = !hosted && config.key_configured === true ? "investigate" : "synthetic";', script)
        self.assertIn('request("/api/config")', script)


if __name__ == "__main__":
    unittest.main()
