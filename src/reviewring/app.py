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
"""

from __future__ import annotations

import argparse
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from reviewring.live import LiveError, LiveState
from reviewring.loader import RAW_DIR
from reviewring.views import REAL, SYNTHETIC, real_view, synthetic_view

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
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


def make_handler(raw_dir: Path, live: LiveState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ReviewRing"
        sys_version = ""

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

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
                    mode = parse_qs(url.query).get("mode", [REAL])[0]
                    if mode not in (REAL, SYNTHETIC):
                        raise _Rejected(400, "Unknown mode. Use 'real' or 'synthetic'.")
                    return real_view(raw_dir) if mode == REAL else synthetic_view()

                self._respond(view)
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
                "/api/investigations/plan": lambda body: live.plan_investigation(body.get("business"), body.get("histories")),
                "/api/investigations": lambda body: live.start_investigation(body.get("plan")),
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

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Quiet by default; nothing about requests or data is logged.
            return

    return Handler


def create_server(
    port: int = DEFAULT_PORT, raw_dir: Path = RAW_DIR, live: LiveState | None = None
) -> ThreadingHTTPServer:
    """A server bound to the loopback interface only. ``port=0`` picks a free port."""
    return ThreadingHTTPServer((HOST, port), make_handler(raw_dir, live or LiveState()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ReviewRing dashboard locally.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)
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
