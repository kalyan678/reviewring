"""Offline tests for experiments/exp001_maps_reviews_page2.py (EXP-001 / Call #4).

`search` is patched in every test that reaches it, and the patched mock asserts
call counts, so no request can reach serpapi.com. Payloads are synthetic and
shaped like the fields observed in the Call #2 response; no real reviewer data,
pagination token, or API credential is used.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "experiments" / "exp001_maps_reviews_page2.py"
_spec = importlib.util.spec_from_file_location("exp001_maps_reviews_page2", SCRIPT_PATH)
exp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp)

# Fictional local inputs. The real ones live in the untracked local_inputs.json.
exp.SEED_DATA_ID = "0x00000000000000a0:0x00000000000000b0"

FAKE_TOKEN = "FAKE-TOKEN-aaaaBBBBccccDDDD0000111122223333"
FAKE_NEXT_TOKEN = "FAKE-FOLLOWING-TOKEN-zzzzYYYYxxxx9999"
FAKE_CONTRIBUTOR_ID = "999000111222333444555"
FAKE_NAME = "Test Reviewer Name"
FAKE_PROFILE_URL = "https://www.google.com/maps/contrib/999000111222333444555?hl=en"
FAKE_THUMBNAIL = "https://example.invalid/avatar.png"
FAKE_SNIPPET = "Private review text that must never be printed."
FAKE_MALFORMED_COUNT = "private-string-in-a-count-field"

EXPECTED_PARAMS = {
    "engine": "google_maps_reviews",
    "data_id": exp.SEED_DATA_ID,
    "hl": "en",
    "next_page_token": FAKE_TOKEN,
}


def _next_url(
    query: dict[str, str] | list[tuple[str, str]] | None = None,
    base: str = "https://serpapi.com/search.json",
) -> str:
    return f"{base}?{urlencode(EXPECTED_PARAMS if query is None else query)}"


def _call2(**overrides: object) -> dict:
    response = {
        "search_parameters": {"engine": "google_maps_reviews", "data_id": exp.SEED_DATA_ID, "hl": "en"},
        "reviews": [_review()],
        "serpapi_pagination": {"next": _next_url(), "next_page_token": FAKE_TOKEN},
    }
    response.update(overrides)
    return response


def _review(**user_overrides: object) -> dict:
    user = {
        "name": FAKE_NAME,
        "link": FAKE_PROFILE_URL,
        "contributor_id": FAKE_CONTRIBUTOR_ID,
        "thumbnail": FAKE_THUMBNAIL,
        "local_guide": True,
        "reviews": 10,
        "photos": 2,
    }
    user.update(user_overrides)
    return {"review_id": "rev-1", "rating": 5, "snippet": FAKE_SNIPPET, "user": user}


def _make_stdout() -> io.TextIOWrapper:
    # main() calls sys.stdout.reconfigure(), which io.StringIO does not support.
    # newline="\n" keeps captured output identical across platforms.
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")


def _read(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


class BuildParamsTests(unittest.TestCase):
    def _assert_fails(self, response: dict, pattern: str) -> None:
        with self.assertRaisesRegex(exp.Call4SetupError, pattern) as ctx:
            exp.build_params(response)
        # No failure message may quote the token or the pagination URL.
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertNotIn("search.json?", str(ctx.exception))

    def test_saved_pagination_builds_exactly_the_observed_request(self):
        self.assertEqual(exp.build_params(_call2()), EXPECTED_PARAMS)

    def test_query_parameter_order_does_not_matter(self):
        url = _next_url(list(reversed(list(EXPECTED_PARAMS.items()))))
        response = _call2(serpapi_pagination={"next": url, "next_page_token": FAKE_TOKEN})
        self.assertEqual(exp.build_params(response), EXPECTED_PARAMS)

    def test_missing_or_malformed_pagination_object_fails(self):
        response = _call2()
        del response["serpapi_pagination"]
        self._assert_fails(response, "no 'serpapi_pagination' object")
        for value in (None, [], "next", 1):
            with self.subTest(value=value):
                self._assert_fails(_call2(serpapi_pagination=value), "no 'serpapi_pagination' object")

    def test_missing_or_malformed_token_fails(self):
        self._assert_fails(_call2(serpapi_pagination={"next": _next_url()}), "next_page_token")
        for token in ("", "   ", None, 12345, ["token"], {"token": FAKE_TOKEN}):
            with self.subTest(token=token):
                pagination = {"next": _next_url(), "next_page_token": token}
                self._assert_fails(
                    _call2(serpapi_pagination=pagination), "not a non-empty string"
                )

    def test_missing_or_malformed_next_url_fails(self):
        self._assert_fails(
            _call2(serpapi_pagination={"next_page_token": FAKE_TOKEN}), "serpapi_pagination.next"
        )
        for url in ("", None, 7, ["https://serpapi.com/search.json"]):
            with self.subTest(url=url):
                pagination = {"next": url, "next_page_token": FAKE_TOKEN}
                self._assert_fails(_call2(serpapi_pagination=pagination), "serpapi_pagination.next")

    def test_unexpected_hostname_fails(self):
        for base in (
            "https://example.invalid/search.json",
            "https://serpapi.com.example.invalid/search.json",
            "https://www.serpapi.com/search.json",
            "https://serpapi.com@example.invalid/search.json",
        ):
            with self.subTest(base=base):
                pagination = {"next": _next_url(base=base), "next_page_token": FAKE_TOKEN}
                self._assert_fails(_call2(serpapi_pagination=pagination), "hostname")

    def test_unexpected_scheme_or_path_fails(self):
        cases = [
            ("http://serpapi.com/search.json", "scheme"),
            ("https://serpapi.com/search", "path"),
            ("https://serpapi.com/other/search.json", "path"),
        ]
        for base, pattern in cases:
            with self.subTest(base=base):
                pagination = {"next": _next_url(base=base), "next_page_token": FAKE_TOKEN}
                self._assert_fails(_call2(serpapi_pagination=pagination), pattern)

    def test_business_id_mismatch_in_url_fails(self):
        url = _next_url({**EXPECTED_PARAMS, "data_id": "0x0000000000000000:0x1111111111111111"})
        pagination = {"next": url, "next_page_token": FAKE_TOKEN}
        self._assert_fails(_call2(serpapi_pagination=pagination), "data_id does not match")

    def test_saved_response_for_another_business_fails(self):
        search_parameters = {
            "engine": "google_maps_reviews",
            "data_id": "0x0000000000000000:0x1111111111111111",
            "hl": "en",
        }
        self._assert_fails(_call2(search_parameters=search_parameters), "not the seed business")
        self._assert_fails(_call2(search_parameters=None), "no 'search_parameters' object")

    def test_engine_hl_or_token_mismatch_fails(self):
        for name, value in [
            ("engine", "google_maps"),
            ("hl", "hi"),
            ("next_page_token", "a-different-token"),
        ]:
            with self.subTest(name=name):
                url = _next_url({**EXPECTED_PARAMS, name: value})
                pagination = {"next": url, "next_page_token": FAKE_TOKEN}
                self._assert_fails(_call2(serpapi_pagination=pagination), f"'{name}' does not match")

    def test_saved_search_parameters_engine_or_hl_mismatch_fails(self):
        for name, value in [("engine", "google_maps"), ("hl", "hi")]:
            with self.subTest(name=name):
                search_parameters = {**_call2()["search_parameters"], name: value}
                self._assert_fails(
                    _call2(search_parameters=search_parameters), f"search_parameters.{name}"
                )

    def test_unexpected_missing_or_duplicated_query_parameters_fail(self):
        items = list(EXPECTED_PARAMS.items())
        cases = {
            "unexpected": items + [("sort_by", "newestFirst")],
            "unexpected api_key": items + [("api_key", "not-a-real-key")],
            "missing": [item for item in items if item[0] != "hl"],
            "duplicated": items + [("data_id", exp.SEED_DATA_ID)],
            "empty": [],
        }
        for label, query in cases.items():
            with self.subTest(case=label):
                pagination = {"next": _next_url(query), "next_page_token": FAKE_TOKEN}
                with self.assertRaisesRegex(
                    exp.Call4SetupError, "do not match the observed contract"
                ) as ctx:
                    exp.build_params(_call2(serpapi_pagination=pagination))
                # Unexpected parameter names and values are counted, never quoted.
                for text in (FAKE_TOKEN, "sort_by", "newestFirst", "not-a-real-key"):
                    self.assertNotIn(text, str(ctx.exception))


class LoadCall2ResponseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call4SetupError, "not found"):
            exp.load_call2_response(self.tmp / "missing.json")

    def test_invalid_json_fails_without_quoting_content(self):
        path = self.tmp / "bad.json"
        path.write_text(f'{{"serpapi_pagination": {{"next_page_token": {FAKE_TOKEN}', encoding="utf-8")
        with self.assertRaises(exp.Call4SetupError) as ctx:
            exp.load_call2_response(path)
        self.assertIn("could not be read as JSON", str(ctx.exception))
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))

    def test_non_object_top_level_fails_clearly(self):
        path = self.tmp / "list.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(exp.Call4SetupError, "top level is list"):
            exp.load_call2_response(path)


class MainTests(unittest.TestCase):
    """main() with a synthetic Call #2 file; `search` is always patched."""

    SENSITIVE = [
        FAKE_TOKEN,
        FAKE_NEXT_TOKEN,
        FAKE_CONTRIBUTOR_ID,
        FAKE_NAME,
        FAKE_PROFILE_URL,
        FAKE_THUMBNAIL,
        FAKE_SNIPPET,
        FAKE_MALFORMED_COUNT,
        "search.json",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.call2_path = Path(self._tmp.name) / "call2.json"
        self._write_call2(_call2())

    def tearDown(self):
        self._tmp.cleanup()

    def _write_call2(self, response: dict) -> None:
        self.call2_path.write_text(json.dumps(response), encoding="utf-8")

    def _run_main(self, search_mock: mock.Mock):
        stdout, stderr = _make_stdout(), io.StringIO()
        with mock.patch.object(exp, "search", search_mock), \
                mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = exp.main(self.call2_path)
        return code, _read(stdout), stderr.getvalue()

    def _assert_no_sensitive_output(self, *outputs: str) -> None:
        for output in outputs:
            for value in self.SENSITIVE:
                self.assertNotIn(value, output)

    def test_success_calls_search_once_with_the_saved_request(self):
        response = {
            "search_parameters": dict(EXPECTED_PARAMS),
            "reviews": [_review(reviews=1), _review(reviews=2), _review(reviews=5), _review(reviews=5)],
            "serpapi_pagination": {
                "next": _next_url({**EXPECTED_PARAMS, "next_page_token": FAKE_NEXT_TOKEN}),
                "next_page_token": FAKE_NEXT_TOKEN,
            },
        }
        search_mock = mock.Mock(return_value=response)
        code, out, err = self._run_main(search_mock)

        self.assertEqual(code, 0)
        search_mock.assert_called_once_with(EXPECTED_PARAMS)
        self.assertEqual(err, "")
        self.assertIn(f"Cache key prefix: {exp.cache_key(EXPECTED_PARAMS)[:16]}", out)
        self.assertIn("Number of reviews in this response: 4", out)
        self.assertIn("Valid contributor IDs: 4 of 4", out)
        self.assertIn("  1: 1\n  2: 1\n  5: 2\n", out)
        self.assertIn("Reviewers with reported review count >= 2: 3", out)
        self.assertIn("Reviewers with reported review count >= 5: 2", out)
        self.assertIn("Further pagination token present: YES", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_and_malformed_metadata_is_reported_not_guessed(self):
        no_metadata = _review()
        for field in ("contributor_id", "reviews", "photos", "local_guide"):
            del no_metadata["user"][field]
        response = {
            "reviews": [
                _review(),
                no_metadata,
                _review(contributor_id="", reviews=None, photos=None, local_guide=None),
                _review(contributor_id=12345, reviews=FAKE_MALFORMED_COUNT, photos=True, local_guide=1),
                {"review_id": "no-user", "snippet": FAKE_SNIPPET},
                {"user": FAKE_NAME},
                FAKE_SNIPPET,
            ]
        }
        code, out, err = self._run_main(mock.Mock(return_value=response))

        self.assertEqual(code, 0)
        rows = [line.split(" | ") for line in out.splitlines() if " | " in line][1:]
        self.assertEqual(len(rows), 7)
        self.assertEqual([cell.strip() for cell in rows[0]], ["1", "10", "2", "True", "YES"])
        for row in (rows[1], rows[2], rows[4], rows[5], rows[6]):
            self.assertEqual([cell.strip() for cell in row[1:]], ["MISSING", "MISSING", "MISSING", "NO"])
        self.assertEqual(
            [cell.strip() for cell in rows[3][1:]],
            ["MALFORMED (str)", "MALFORMED (bool)", "MALFORMED (int)", "NO"],
        )
        self.assertIn("Valid contributor IDs: 1 of 7", out)
        self.assertIn("  10: 1\n  MALFORMED (str): 1\n  MISSING: 5\n", out)
        self.assertIn("Reviewers with reported review count >= 2: 1", out)
        self.assertIn("Reviewers with reported review count >= 5: 1", out)
        self.assertIn("Further pagination token present: NO", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_or_malformed_reviews_collection_is_reported(self):
        for response, state in [({}, "MISSING"), ({"reviews": {"0": _review()}}, "not a list (got dict)")]:
            with self.subTest(state=state):
                code, out, err = self._run_main(mock.Mock(return_value=response))
                self.assertEqual(code, 0)
                self.assertIn(f"'reviews' collection: {state}", out)
                self.assertIn("Number of reviews in this response: 0", out)
                self._assert_no_sensitive_output(out, err)

    def test_empty_further_token_is_reported_as_absent(self):
        for pagination in ({"next_page_token": ""}, {"next_page_token": None}, "token", None):
            with self.subTest(pagination=pagination):
                response = {"reviews": [], "serpapi_pagination": pagination}
                _, out, _ = self._run_main(mock.Mock(return_value=response))
                self.assertIn("Further pagination token present: NO", out)

    def test_invalid_saved_pagination_fails_before_search(self):
        invalid = [
            _call2(serpapi_pagination=None),
            _call2(serpapi_pagination={"next": _next_url(), "next_page_token": ""}),
            _call2(serpapi_pagination={
                "next": _next_url(base="https://example.invalid/search.json"),
                "next_page_token": FAKE_TOKEN,
            }),
            _call2(serpapi_pagination={
                "next": _next_url({**EXPECTED_PARAMS, "data_id": "0x0:0x1"}),
                "next_page_token": FAKE_TOKEN,
            }),
            _call2(serpapi_pagination={
                "next": _next_url({**EXPECTED_PARAMS, "num": "20"}),
                "next_page_token": FAKE_TOKEN,
            }),
        ]
        for response in invalid:
            with self.subTest(pagination=type(response["serpapi_pagination"]).__name__):
                self._write_call2(response)
                search_mock = mock.Mock()
                code, out, err = self._run_main(search_mock)
                self.assertEqual(code, 1)
                search_mock.assert_not_called()
                self.assertIn("EXP-001 / Call #4 not run:", err)
                self.assertEqual(out, "")
                self._assert_no_sensitive_output(out, err)

    def test_missing_call2_file_fails_before_search(self):
        self.call2_path.unlink()
        search_mock = mock.Mock()
        code, _, err = self._run_main(search_mock)
        self.assertEqual(code, 1)
        search_mock.assert_not_called()
        self.assertIn("not found", err)

    def test_search_error_message_redacts_token(self):
        error = exp.SerpApiError(f"SerpApi error (HTTP 400): bad token {FAKE_TOKEN}")
        search_mock = mock.Mock(side_effect=error)
        code, out, err = self._run_main(search_mock)
        self.assertEqual(code, 1)
        search_mock.assert_called_once_with(EXPECTED_PARAMS)
        self.assertIn(exp.REDACTED_TOKEN, err)
        self._assert_no_sensitive_output(out, err)


class FilenameTests(unittest.TestCase):
    def test_cache_key_does_not_contain_token(self):
        key = exp.cache_key(EXPECTED_PARAMS)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(FAKE_TOKEN, key)


if __name__ == "__main__":
    unittest.main()
