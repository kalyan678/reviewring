"""Offline tests for experiments/exp000_contributor_reviews.py (EXP-000 / Call #3).

`search` is patched in every test that reaches it, and the patched mock asserts
call counts, so no request can reach serpapi.com. Payloads are synthetic and
shaped like the fields observed in the Call #2 response.

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

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "experiments" / "exp000_contributor_reviews.py"
_spec = importlib.util.spec_from_file_location("exp000_contributor_reviews", SCRIPT_PATH)
exp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp)

FAKE_CONTRIBUTOR_ID = "999000111222333444555"
FAKE_NAME = "Test Reviewer Name"
FAKE_PROFILE_URL = "https://www.google.com/maps/contrib/999000111222333444555?hl=en"
FAKE_THUMBNAIL = "https://example.invalid/avatar.png"
FAKE_SNIPPET = "Private review text that must never be printed."
FAKE_ENGINE = "test_engine"
FAKE_PARAM = "test_contributor_param"


def _review(contributor_id: object = FAKE_CONTRIBUTOR_ID) -> dict:
    return {
        "review_id": "rev-1",
        "rating": 5,
        "snippet": FAKE_SNIPPET,
        "user": {
            "name": FAKE_NAME,
            "link": FAKE_PROFILE_URL,
            "contributor_id": contributor_id,
            "thumbnail": FAKE_THUMBNAIL,
            "local_guide": True,
            "reviews": 10,
            "photos": 2,
        },
    }


def _make_stdout() -> io.TextIOWrapper:
    # main() calls sys.stdout.reconfigure(), which io.StringIO does not support.
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


def _read(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


class SelectContributorIdTests(unittest.TestCase):
    def test_returns_first_usable_contributor_id(self):
        response = {"reviews": [_review("first-id"), _review("second-id")]}
        self.assertEqual(exp.select_contributor_id(response), "first-id")

    def test_missing_reviews_collection_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call3SetupError, "no 'reviews' collection"):
            exp.select_contributor_id({"place_info": {}})

    def test_reviews_not_a_list_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call3SetupError, "'reviews' is dict, expected a list"):
            exp.select_contributor_id({"reviews": {"0": _review()}})

    def test_malformed_review_entries_are_skipped(self):
        response = {"reviews": [None, "text", 42, ["x"], _review("usable-id")]}
        self.assertEqual(exp.select_contributor_id(response), "usable-id")

    def test_malformed_user_objects_are_skipped(self):
        response = {
            "reviews": [
                {"review_id": "no-user"},
                {"user": None},
                {"user": "not-an-object"},
                {"user": ["contributor_id"]},
                _review("usable-id"),
            ]
        }
        self.assertEqual(exp.select_contributor_id(response), "usable-id")

    def test_empty_or_non_string_contributor_ids_are_skipped(self):
        response = {
            "reviews": [
                {"user": {"name": FAKE_NAME}},
                _review(""),
                _review("   "),
                _review(None),
                _review(12345),
                _review(["id"]),
                _review("usable-id"),
            ]
        }
        self.assertEqual(exp.select_contributor_id(response), "usable-id")

    def test_no_usable_contributor_id_fails_clearly(self):
        response = {"reviews": [_review(""), {"user": {}}, None]}
        with self.assertRaisesRegex(exp.Call3SetupError, "non-empty string user.contributor_id"):
            exp.select_contributor_id(response)


class LoadCall2ResponseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call3SetupError, "not found"):
            exp.load_call2_response(self.tmp / "missing.json")

    def test_invalid_json_fails_without_quoting_content(self):
        path = self.tmp / "bad.json"
        path.write_text(f'{{"reviews": [{FAKE_SNIPPET}', encoding="utf-8")
        with self.assertRaises(exp.Call3SetupError) as ctx:
            exp.load_call2_response(path)
        self.assertIn("could not be read as JSON", str(ctx.exception))
        self.assertNotIn(FAKE_SNIPPET, str(ctx.exception))

    def test_non_object_top_level_fails_clearly(self):
        path = self.tmp / "list.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(exp.Call3SetupError, "top level is list"):
            exp.load_call2_response(path)


class BuildParamsTests(unittest.TestCase):
    def test_unestablished_contract_fails(self):
        for engine, param in [(None, None), (FAKE_ENGINE, None), (None, FAKE_PARAM), ("", "")]:
            with self.subTest(engine=engine, param=param):
                with self.assertRaisesRegex(exp.Call3SetupError, "not established"):
                    exp.build_params(FAKE_CONTRIBUTOR_ID, engine, param)

    def test_builds_exactly_one_engine_and_contributor_parameter(self):
        params = exp.build_params(FAKE_CONTRIBUTOR_ID, FAKE_ENGINE, FAKE_PARAM)
        self.assertEqual(params, {"engine": FAKE_ENGINE, FAKE_PARAM: FAKE_CONTRIBUTOR_ID})

    def test_module_contract_matches_documented_values(self):
        # Source: https://serpapi.com/google-maps-contributor-reviews-api
        self.assertEqual(exp.CONTRIBUTOR_REVIEWS_ENGINE, "google_maps_contributor_reviews")
        self.assertEqual(exp.CONTRIBUTOR_ID_PARAM, "contributor_id")

    def test_documented_contract_builds_exactly_the_two_required_parameters(self):
        params = exp.build_params(
            FAKE_CONTRIBUTOR_ID, exp.CONTRIBUTOR_REVIEWS_ENGINE, exp.CONTRIBUTOR_ID_PARAM
        )
        self.assertEqual(
            params,
            {"engine": "google_maps_contributor_reviews", "contributor_id": FAKE_CONTRIBUTOR_ID},
        )


class MainTests(unittest.TestCase):
    """main() with a synthetic Call #2 file; `search` is always patched."""

    SENSITIVE = [FAKE_CONTRIBUTOR_ID, FAKE_NAME, FAKE_PROFILE_URL, FAKE_THUMBNAIL, FAKE_SNIPPET]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.call2_path = Path(self._tmp.name) / "call2.json"
        self.call2_path.write_text(json.dumps({"reviews": [_review()]}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _run_main(self, search_mock: mock.Mock, engine: str | None, param: str | None):
        stdout, stderr = _make_stdout(), io.StringIO()
        with mock.patch.object(exp, "search", search_mock), \
                mock.patch.object(exp, "CONTRIBUTOR_REVIEWS_ENGINE", engine), \
                mock.patch.object(exp, "CONTRIBUTOR_ID_PARAM", param), \
                mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = exp.main(self.call2_path)
        return code, _read(stdout), stderr.getvalue()

    def _assert_no_sensitive_output(self, *outputs: str) -> None:
        for output in outputs:
            for value in self.SENSITIVE:
                self.assertNotIn(value, output)

    def test_unestablished_contract_fails_before_search(self):
        search_mock = mock.Mock()
        code, out, err = self._run_main(search_mock, None, None)
        self.assertEqual(code, 1)
        search_mock.assert_not_called()
        self.assertIn("not established", err)
        self._assert_no_sensitive_output(out, err)

    def test_no_usable_contributor_fails_before_search(self):
        self.call2_path.write_text(json.dumps({"reviews": [_review("")]}), encoding="utf-8")
        search_mock = mock.Mock()
        code, out, err = self._run_main(search_mock, FAKE_ENGINE, FAKE_PARAM)
        self.assertEqual(code, 1)
        search_mock.assert_not_called()
        self.assertIn("non-empty string user.contributor_id", err)

    def test_success_calls_search_once_and_never_prints_contributor_data(self):
        # The response echoes the contributor ID in places SerpApi might.
        response = {
            "search_parameters": {"engine": FAKE_ENGINE, FAKE_PARAM: FAKE_CONTRIBUTOR_ID},
            "contributor": {"name": FAKE_NAME, "link": FAKE_PROFILE_URL},
            "history": [
                {
                    "snippet": FAKE_SNIPPET,
                    "place": {"title": "Some Place", "id": FAKE_CONTRIBUTOR_ID},
                    "user": {"thumbnail": FAKE_THUMBNAIL},
                },
                "not-an-object",
            ],
        }
        search_mock = mock.Mock(return_value=response)
        code, out, err = self._run_main(search_mock, FAKE_ENGINE, FAKE_PARAM)

        self.assertEqual(code, 0)
        search_mock.assert_called_once_with({"engine": FAKE_ENGINE, FAKE_PARAM: FAKE_CONTRIBUTOR_ID})
        self.assertIn("Contributor ID loaded: YES", out)
        self.assertIn("=== Top-level list 'history': 2 entries ===", out)
        self.assertIn("Business/place identifier: NOT ESTABLISHED", out)
        self.assertIn("Available keys in nested object 'place':", out)
        self.assertIn("['title', 'id']", out)
        self._assert_no_sensitive_output(out, err)

    def test_history_inspection_is_capped_at_five_entries(self):
        response = {"history": [{"n": i} for i in range(8)]}
        code, out, _ = self._run_main(mock.Mock(return_value=response), FAKE_ENGINE, FAKE_PARAM)
        self.assertEqual(code, 0)
        self.assertIn("--- History entry 5 ---", out)
        self.assertNotIn("--- History entry 6 ---", out)

    def test_search_error_message_redacts_contributor_id(self):
        error = exp.SerpApiError(f"SerpApi error (HTTP 400): bad id {FAKE_CONTRIBUTOR_ID}")
        search_mock = mock.Mock(side_effect=error)
        code, out, err = self._run_main(search_mock, FAKE_ENGINE, FAKE_PARAM)
        self.assertEqual(code, 1)
        search_mock.assert_called_once()
        self.assertIn(exp.REDACTED_CONTRIBUTOR_ID, err)
        self._assert_no_sensitive_output(out, err)


class FilenameTests(unittest.TestCase):
    def test_cache_and_raw_filenames_do_not_contain_contributor_id(self):
        params = exp.build_params(FAKE_CONTRIBUTOR_ID, FAKE_ENGINE, FAKE_PARAM)
        key = exp.cache_key(params)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(FAKE_CONTRIBUTOR_ID, key)


if __name__ == "__main__":
    unittest.main()
