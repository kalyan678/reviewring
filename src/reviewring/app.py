"""Local dashboard server for ReviewRing.

Standard library only. Serves one static page and a small JSON API on the
loopback interface. Viewing saved or synthetic data makes no outbound request.
The only outbound requests are SerpApi calls made by a live investigation,
each covered by a plan the user confirmed (see ``reviewring.live``).

Every request must carry this server's own Host header, which defeats DNS
rebinding. Requests that can spend credits or read investigation results must
also be same-origin JSON requests carrying the per-process session token, which
a page from another origin cannot obtain. No cookies are used.

Run from the project root:
    .venv/Scripts/python main.py

Hosted demo. With ``REVIEWRING_HOSTED_DEMO=1`` the process serves a public,
read-only, fictional demonstration instead (see ``_HostedHandler``). That mode
has its own handler with a fixed list of routes; it never loads the live
investigation code, the SerpApi client, a key, or anything saved on disk.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from reviewring.loader import RAW_DIR
from reviewring.synthetic import MAX_EXPANSION_STEPS
from reviewring.views import REAL, SYNTHETIC, real_view, synthetic_view

if TYPE_CHECKING:
    # Imported only when a local server is created: the hosted demo must not load it.
    from reviewring.live import LiveState

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Hosted demo: enabled only by this exact setting, never by default.
HOSTED_ENV_VAR = "REVIEWRING_HOSTED_DEMO"
HOSTED_HOST = "0.0.0.0"  # noqa: S104 (public by design, synthetic data only)
HOSTED_DEFAULT_PORT = 10000
HOSTED_REQUEST_TIMEOUT_SECONDS = 15
HOSTED_ONLY = "This hosted demo serves the synthetic demonstration only."
EXPANSION_STEPS = {str(n) for n in range(MAX_EXPANSION_STEPS + 1)}
WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY_BYTES = 4096
MAX_DRAIN_BYTES = 65536  # larger bodies are not read; the connection is closed
TOKEN_HEADER = "X-ReviewRing-Token"
INVESTIGATION_PREFIX = "/api/investigations/"

# The only files that can be served: request path -> (file, content type).
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

SECURITY_HEADERS = {
    # Same-origin only: no CDN, fonts, analytics, or inline script can load.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class _Rejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _Responder(BaseHTTPRequestHandler):
    """Response helpers shared by the local and the hosted handler."""

    server_version = "ReviewRing"
    sys_version = ""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Quiet by default; nothing about requests or data is logged.
        return


class _HostedHandler(_Responder):
    """The public demo: read-only, fictional data, and nothing else.

    Routes are an allowlist. Anything not listed is refused, so a route added
    to the local dashboard later is not exposed here by accident. There is no
    live state, no saved data, no key, and no method that writes.
    """

    timeout = HOSTED_REQUEST_TIMEOUT_SECONDS  # a stalled connection is dropped

    def _view(self, query: str) -> dict:
        try:
            params = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        except ValueError:
            raise _Rejected(400, HOSTED_ONLY) from None
        if set(params) - {"mode", "expansions"} or any(len(values) != 1 for values in params.values()):
            raise _Rejected(400, HOSTED_ONLY)
        if params.get("mode") != [SYNTHETIC]:
            raise _Rejected(400, HOSTED_ONLY)
        steps = params.get("expansions", ["0"])[0]
        if steps not in EXPANSION_STEPS:
            raise _Rejected(400, f"Use 0 to {MAX_EXPANSION_STEPS} expansion steps.")
        return synthetic_view(int(steps))

    def _serve(self) -> None:
        url = urlsplit(self.path)
        try:
            if url.path in STATIC_FILES and not url.query:
                name, content_type = STATIC_FILES[url.path]
                self._send(200, (WEB_DIR / name).read_bytes(), content_type)
            elif url.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif url.path == "/api/config" and not url.query:
                self._send_json(200, {"hosted_demo": True})
            elif url.path == "/api/view":
                self._send_json(200, self._view(url.query))
            else:
                self._send_json(404, {"error": "Not found."})
        except _Rejected as error:
            self._send_json(error.status, {"error": error.message})
        except Exception:  # noqa: BLE001
            self._send_json(500, {"error": "The request could not be completed."})

    def _refuse(self) -> None:
        # No request body is ever read here; the connection is closed instead.
        self.close_connection = True
        self._send_json(405, {"error": HOSTED_ONLY})

    do_GET = do_HEAD = _serve  # noqa: N815 (http.server naming)
    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse  # noqa: N815


def make_handler(raw_dir: Path, live: LiveState) -> type[BaseHTTPRequestHandler]:
    from reviewring.live import LiveError

    class Handler(_Responder):
        def _origin(self) -> str:
            return f"http://{HOST}:{self.server.server_address[1]}"

        def _check_host(self) -> None:
            port = self.server.server_address[1]
            if self.headers.get("Host") not in (f"{HOST}:{port}", f"localhost:{port}"):
                raise _Rejected(403, "Request not accepted.")

        def _check_same_origin_token(self) -> None:
            origin = self.headers.get("Origin")
            port = self.server.server_address[1]
            if origin is not None and origin not in (self._origin(), f"http://localhost:{port}"):
                raise _Rejected(403, "Request not accepted.")
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                raise _Rejected(403, "Request not accepted.")
            supplied = self.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied.encode("utf-8"), live.token.encode("utf-8")):
                raise _Rejected(403, "Request not accepted. Reload the page.")

        def _read_body(self) -> bytes | None:
            """Read the request body up front, so a rejected request is still
            fully consumed and the client gets the response, not a reset."""
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_DRAIN_BYTES:
                self.close_connection = True
                return None
            return self.rfile.read(length)

        def _json_body(self, raw: bytes | None) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise _Rejected(415, "Send JSON.")
            if raw is None or len(raw) > MAX_BODY_BYTES:
                raise _Rejected(413, "Request too large or of unknown length.")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _Rejected(400, "The request is not valid JSON.") from None
            if not isinstance(body, dict):
                raise _Rejected(400, "The request must be a JSON object.")
            return body

        def _respond(self, action) -> None:
            """Run ``action`` and send its payload; never forward exception text."""
            try:
                self._check_host()
                self._send_json(200, action())
            except (_Rejected, LiveError) as error:
                self._send_json(error.status, {"error": error.message})
            except Exception:  # noqa: BLE001
                # Exception text could quote saved or collected data.
                self._send_json(500, {"error": "The request could not be completed."})

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            url = urlsplit(self.path)
            if url.path in STATIC_FILES:
                def static() -> None:
                    self._check_host()
                    name, content_type = STATIC_FILES[url.path]
                    self._send(200, (WEB_DIR / name).read_bytes(), content_type)

                try:
                    static()
                except _Rejected as error:
                    self._send_json(error.status, {"error": error.message})
                return
            if url.path == "/favicon.ico":
                # Browsers ask for this on their own; answer with nothing instead of a 404.
                self._send(204, b"", "image/x-icon")
                return
            if url.path == "/api/view":
                def view() -> dict:
                    query = parse_qs(url.query)
                    mode = query.get("mode", [REAL])[0]
                    if mode not in (REAL, SYNTHETIC):
                        raise _Rejected(400, "Unknown mode. Use 'real' or 'synthetic'.")
                    if mode == REAL:
                        return real_view(raw_dir)
                    # Expansion steps of the fictional demo: computed locally, no request sent.
                    steps = query.get("expansions", ["0"])[0]
                    if steps not in EXPANSION_STEPS:
                        raise _Rejected(400, f"Use 0 to {MAX_EXPANSION_STEPS} expansion steps.")
                    return synthetic_view(int(steps))

                self._respond(view)
            elif url.path == "/api/config":
                self._respond(lambda: {"hosted_demo": False})
            elif url.path == "/api/session":
                def session() -> dict:
                    # Readable only same-origin: no CORS header is ever sent.
                    if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                        raise _Rejected(403, "Request not accepted.")
                    return live.session_info()

                self._respond(session)
            elif url.path.startswith(INVESTIGATION_PREFIX):
                def status() -> dict:
                    self._check_same_origin_token()
                    return live.investigation_status(url.path[len(INVESTIGATION_PREFIX):])

                self._respond(status)
            else:
                self._send_json(404, {"error": "Not found."})

        def do_POST(self) -> None:  # noqa: N802 (http.server naming)
            actions = {
                "/api/search/plan": lambda body: live.plan_search(body.get("query")),
                "/api/search": lambda body: live.run_search(body.get("plan")),
                "/api/investigations/plan": lambda body: live.plan_investigation(
                    body.get("business"), body.get("histories"), body.get("history_limit"), body.get("pages")
                ),
                "/api/investigations": lambda body: live.start_investigation(body.get("plan")),
                "/api/expansions/plan": lambda body: live.plan_expansion(body.get("investigation"), body.get("businesses")),
                "/api/expansions": lambda body: live.start_expansion(body.get("plan"), body.get("investigation")),
            }
            action = actions.get(urlsplit(self.path).path)
            if action is None:
                self._read_body()
                self._send_json(404, {"error": "Not found."})
                return

            raw = self._read_body()

            def run() -> dict:
                self._check_same_origin_token()
                return action(self._json_body(raw))

            self._respond(run)

    return Handler


def create_server(
    port: int = DEFAULT_PORT, raw_dir: Path = RAW_DIR, live: LiveState | None = None
) -> ThreadingHTTPServer:
    """A server bound to the loopback interface only. ``port=0`` picks a free port."""
    if live is None:
        from reviewring.live import LiveState

        live = LiveState()
    return ThreadingHTTPServer((HOST, port), make_handler(raw_dir, live))


def create_hosted_server(port: int, host: str = HOSTED_HOST) -> ThreadingHTTPServer:
    """The public synthetic-only demo. Takes no data directory, key, or live state."""
    return ThreadingHTTPServer((host, port), _HostedHandler)


def _hosted_main() -> int:
    raw = os.environ.get("PORT", str(HOSTED_DEFAULT_PORT))
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        print("Could not start: PORT must be a number from 1 to 65535.")
        return 1
    try:
        server = create_hosted_server(int(raw))
    except OSError:
        print(f"Could not start: port {raw} is unavailable.")
        return 1
    print(f"ReviewRing hosted demo (synthetic data only) listening on {HOSTED_HOST}:{raw}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ReviewRing dashboard locally.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    setting = os.environ.get(HOSTED_ENV_VAR, "")
    if setting == "1":
        return _hosted_main()
    if setting:
        # A half-set switch must not silently start the other mode.
        print(f"Could not start: set {HOSTED_ENV_VAR}=1 for the hosted demo, or unset it to run locally.")
        return 1
    try:
        server = create_server(args.port)
    except OSError:
        print(f"Could not start: port {args.port} is unavailable. Try --port with another number.")
        return 1
    print(f"ReviewRing dashboard: http://{HOST}:{server.server_address[1]}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
