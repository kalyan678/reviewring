"""Offline tests for reviewring.serpapi_client.

All HTTP goes through httpx.MockTransport; constructing a real httpx.Client
inside the module under test fails the test. No request can reach serpapi.com.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import traceback
import unittest
from pathlib import Path
from unittest import mock

import httpx

# pyproject.toml has no build backend yet, so the src/ package is not installed.
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from reviewring import serpapi_client  # noqa: E402
from reviewring.serpapi_client import (  # noqa: E402
    MissingApiKeyError,
    SerpApiError,
    cache_key,
    search,
)

FAKE_KEY = "test-secret-key-0123456789"
PARAMS = {"engine": "google_maps", "q": "bakery in Exampleville", "type": "search"}
LIBRARY_LOGGERS = ["httpx", "httpcore"]
SUCCESS_BODY = b'{"search_metadata": {"status": "Success"},  "local_results": [{"title": "X"}]}'


class FakeSerpApi:
    """MockTransport handler that records requests and returns a canned response."""

    def __init__(self, status: int = 200, body: bytes = SUCCESS_BODY, exc: Exception | None = None):
        self.status = status
        self.body = body
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, content=self.body)


class CacheKeyTests(unittest.TestCase):
    def test_cache_key_is_deterministic(self):
        self.assertEqual(cache_key(PARAMS), cache_key(dict(PARAMS)))
        self.assertRegex(cache_key(PARAMS), r"^[0-9a-f]{64}$")

    def test_dictionary_ordering_does_not_affect_cache_key(self):
        reordered = dict(reversed(list(PARAMS.items())))
        self.assertEqual(list(reordered), list(reversed(list(PARAMS))))
        self.assertEqual(cache_key(PARAMS), cache_key(reordered))

    def test_api_key_is_excluded_from_cache_key(self):
        self.assertEqual(cache_key(PARAMS), cache_key({**PARAMS, "api_key": FAKE_KEY}))
        self.assertEqual(
            cache_key({**PARAMS, "api_key": "one"}), cache_key({**PARAMS, "api_key": "two"})
        )

    def test_different_params_produce_different_keys(self):
        self.assertNotEqual(cache_key(PARAMS), cache_key({**PARAMS, "q": "dentist"}))


class SearchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache_dir = Path(tmp.name) / "cache"
        self.raw_dir = Path(tmp.name) / "raw"

        # Never read the real .env; control the key through the environment only.
        dotenv_patch = mock.patch.object(serpapi_client, "load_dotenv")
        dotenv_patch.start()
        self.addCleanup(dotenv_patch.stop)
        env_patch = mock.patch.dict(os.environ, {serpapi_client.API_KEY_ENV_VAR: FAKE_KEY})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _client(self, fake: FakeSerpApi) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(fake))
        self.addCleanup(client.close)
        return client

    def _search(self, fake: FakeSerpApi, params: dict = PARAMS) -> dict:
        client = self._client(fake)
        # Guard: any attempt by the module to build a real client fails the test.
        with mock.patch.object(
            serpapi_client.httpx, "Client", side_effect=AssertionError("real httpx.Client used")
        ):
            return search(params, client=client, cache_dir=self.cache_dir, raw_dir=self.raw_dir)

    def _all_files(self) -> list[Path]:
        return [p for d in (self.cache_dir, self.raw_dir) if d.exists() for p in d.iterdir()]

    def test_successful_response_is_written_to_cache(self):
        fake = FakeSerpApi()
        result = self._search(fake)

        self.assertEqual(result, json.loads(SUCCESS_BODY))
        cache_file = self.cache_dir / f"google_maps_{cache_key(PARAMS)}.json"
        self.assertTrue(cache_file.exists())
        self.assertEqual(cache_file.read_bytes(), SUCCESS_BODY)

    def test_uncached_response_is_preserved_as_sanitized_raw(self):
        self._search(FakeSerpApi())

        raw_files = list(self.raw_dir.iterdir())
        self.assertEqual(len(raw_files), 1)
        self.assertEqual(raw_files[0].name, f"google_maps_{cache_key(PARAMS)[:16]}.json")
        # With no secret in the payload, sanitizing changes nothing: the file is
        # byte-for-byte the response, including the original whitespace.
        self.assertEqual(raw_files[0].read_bytes(), SUCCESS_BODY)

    def test_cache_hit_avoids_http_call(self):
        first = FakeSerpApi()
        self._search(first)
        self.assertEqual(len(first.requests), 1)

        second = FakeSerpApi(body=b'{"different": true}')
        reordered = dict(reversed(list(PARAMS.items())))
        result = self._search(second, reordered)

        self.assertEqual(second.requests, [])
        self.assertEqual(result, json.loads(SUCCESS_BODY))

    def test_is_cached_reports_cache_state_without_any_request(self):
        self.assertFalse(serpapi_client.is_cached(PARAMS, cache_dir=self.cache_dir))
        self._search(FakeSerpApi())
        reordered = dict(reversed(list(PARAMS.items())))
        self.assertTrue(serpapi_client.is_cached(reordered, cache_dir=self.cache_dir))
        self.assertFalse(serpapi_client.is_cached({**PARAMS, "q": "other"}, cache_dir=self.cache_dir))

    def test_api_key_configured_reports_presence_only(self):
        self.assertIs(serpapi_client.api_key_configured(), True)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(serpapi_client.api_key_configured(), False)

    def test_cache_hit_does_not_require_api_key(self):
        self._search(FakeSerpApi())
        with mock.patch.dict(os.environ, {}, clear=True):
            result = self._search(FakeSerpApi())
        self.assertEqual(result, json.loads(SUCCESS_BODY))

    def test_missing_api_key_fails_safely(self):
        fake = FakeSerpApi()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingApiKeyError) as ctx:
                self._search(fake)

        self.assertIn("SERPAPI_API_KEY", str(ctx.exception))
        self.assertEqual(fake.requests, [])
        self.assertEqual(self._all_files(), [])

    def test_api_key_is_sent_but_never_stored(self):
        fake = FakeSerpApi()
        self._search(fake)

        self.assertEqual(fake.requests[0].url.params["api_key"], FAKE_KEY)
        for path in self._all_files():
            self.assertNotIn(FAKE_KEY, path.name)
            self.assertNotIn(FAKE_KEY.encode(), path.read_bytes())

    def test_echoed_api_key_is_redacted_before_storage(self):
        body = json.dumps({"search_parameters": {"api_key": FAKE_KEY}}).encode()
        result = self._search(FakeSerpApi(body=body))

        self.assertNotIn(FAKE_KEY, json.dumps(result))
        for path in self._all_files():
            self.assertNotIn(FAKE_KEY.encode(), path.read_bytes())

    def test_api_key_anywhere_in_payload_is_never_persisted(self):
        payload = {
            "search_metadata": {"json_endpoint": f"https://serpapi.com/x?api_key={FAKE_KEY}&q=1"},
            "items": [{"note": f"{FAKE_KEY} and again {FAKE_KEY}"}],
        }
        body = json.dumps(payload, indent=2).encode()
        result = self._search(FakeSerpApi(body=body))

        self.assertNotIn(FAKE_KEY, json.dumps(result))
        files = self._all_files()
        self.assertEqual(len(files), 2)  # one raw snapshot, one cache entry
        for path in files:
            content = path.read_bytes()
            self.assertNotIn(FAKE_KEY.encode(), content)
            # Sanitizing redacts the key only; everything else is preserved as received.
            self.assertEqual(content, body.replace(FAKE_KEY.encode(), b"[REDACTED]"))

    def test_client_exceptions_never_expose_api_key(self):
        failures = {
            "network error": FakeSerpApi(
                exc=httpx.ConnectError(f"GET https://serpapi.com/search.json?api_key={FAKE_KEY}")
            ),
            "timeout": FakeSerpApi(
                exc=httpx.ReadTimeout(f"timed out: https://serpapi.com/?api_key={FAKE_KEY}")
            ),
            "serpapi error": FakeSerpApi(
                status=401, body=json.dumps({"error": f"bad key {FAKE_KEY}"}).encode()
            ),
            "http status": FakeSerpApi(status=503, body=b"{}"),
            "invalid json": FakeSerpApi(body=f"<html>{FAKE_KEY}</html>".encode()),
            "non-object json": FakeSerpApi(body=json.dumps([FAKE_KEY]).encode()),
        }
        for name, fake in failures.items():
            with self.subTest(name):
                with self.assertRaises(SerpApiError) as ctx:
                    self._search(fake)
                exc = ctx.exception
                rendered = "".join(traceback.format_exception(exc))
                for text in (str(exc), repr(exc), rendered):
                    self.assertNotIn(FAKE_KEY, text)
                self.assertEqual(self._all_files(), [])

    def test_serpapi_error_response_is_not_treated_as_success(self):
        body = json.dumps({"error": "Invalid API key."}).encode()
        for status in (200, 401):
            with self.subTest(status=status):
                with self.assertRaises(SerpApiError) as ctx:
                    self._search(FakeSerpApi(status=status, body=body))
                self.assertIn("Invalid API key.", str(ctx.exception))
                self.assertEqual(self._all_files(), [])

    def test_serpapi_error_message_is_redacted(self):
        body = json.dumps({"error": f"bad key {FAKE_KEY}"}).encode()
        with self.assertRaises(SerpApiError) as ctx:
            self._search(FakeSerpApi(body=body))
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_http_error_status_is_not_cached(self):
        with self.assertRaises(SerpApiError) as ctx:
            self._search(FakeSerpApi(status=500, body=b'{"search_metadata": {}}'))
        self.assertIn("500", str(ctx.exception))
        self.assertEqual(self._all_files(), [])

    def test_invalid_json_is_not_cached(self):
        with self.assertRaises(SerpApiError):
            self._search(FakeSerpApi(body=b"<html>not json</html>"))
        self.assertEqual(self._all_files(), [])

    def test_network_error_does_not_leak_api_key(self):
        exc = httpx.ConnectError(f"failed for https://serpapi.com/search.json?api_key={FAKE_KEY}")
        with self.assertRaises(SerpApiError) as ctx:
            self._search(FakeSerpApi(exc=exc))

        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)
        self.assertEqual(self._all_files(), [])

    def test_existing_raw_file_is_never_overwritten(self):
        self._search(FakeSerpApi())
        for path in self.cache_dir.iterdir():
            path.unlink()  # force a second uncached request for the same params

        second_body = b'{"second": true}'
        self._search(FakeSerpApi(body=second_body))

        contents = sorted(p.read_bytes() for p in self.raw_dir.iterdir())
        self.assertEqual(contents, sorted([SUCCESS_BODY, second_body]))

    def test_missing_engine_is_rejected(self):
        with self.assertRaises(ValueError):
            self._search(FakeSerpApi(), {"q": "no engine"})

    def test_using_client_does_not_change_logger_levels(self):
        loggers = [logging.getLogger(name) for name in LIBRARY_LOGGERS]
        original = [logger.level for logger in loggers]
        self.addCleanup(lambda: [l.setLevel(v) for l, v in zip(loggers, original)])
        for logger in loggers:
            logger.setLevel(logging.DEBUG)

        self._search(FakeSerpApi())  # uncached request path
        self._search(FakeSerpApi())  # cache-hit path

        self.assertEqual([logger.level for logger in loggers], [logging.DEBUG] * len(loggers))

    def test_client_code_emits_no_log_records(self):
        with self.assertNoLogs("reviewring", level=logging.DEBUG):
            self._search(FakeSerpApi())
            self._search(FakeSerpApi())
            with self.assertRaises(SerpApiError):
                self._search(FakeSerpApi(status=500, body=b"{}"), {**PARAMS, "q": "other"})


class ImportSideEffectTests(unittest.TestCase):
    def test_import_does_not_change_httpx_or_httpcore_logger_levels(self):
        # A fresh interpreter, so the module is genuinely imported for the first
        # time. The script only imports the module; it makes no requests.
        script = textwrap.dedent(
            f"""
            import json, logging, sys
            sys.path.insert(0, {str(SRC_DIR)!r})
            names = {LIBRARY_LOGGERS!r}
            for name in names:
                logging.getLogger(name).setLevel(logging.DEBUG)
            before = [logging.getLogger(n).level for n in names]
            import reviewring.serpapi_client
            after = [logging.getLogger(n).level for n in names]
            print(json.dumps({{"before": before, "after": after}}))
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        levels = json.loads(completed.stdout)
        self.assertEqual(levels["before"], [logging.DEBUG] * len(LIBRARY_LOGGERS))
        self.assertEqual(levels["after"], levels["before"])


if __name__ == "__main__":
    unittest.main()
