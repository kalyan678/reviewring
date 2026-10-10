"""Offline tests for experiments/exp001_contributor_reviews.py (EXP-001 / Call #5).

`search` is patched in every test that reaches it, and the patched mock asserts
call counts, so no request can reach serpapi.com. Payloads are synthetic and
shaped like the fields observed in the Call #2, #3 and #4 responses; no real
reviewer data or API credential is used.

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

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "experiments" / "exp001_contributor_reviews.py"
_spec = importlib.util.spec_from_file_location("exp001_contributor_reviews", SCRIPT_PATH)
exp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp)

# Fictional local inputs. The real ones live in the untracked local_inputs.json.
exp.SEED_DATA_ID = "0x00000000000000a0:0x00000000000000b0"
exp.SELECTED_POSITION = 4
exp.EXPECTED_REPORTED_REVIEWS = 9

FAKE_SELECTED_ID = "999000111222333444555"
FAKE_CALL3_ID = "888000111222333444555"
FAKE_OTHER_DATA_ID = "0x00000000000000aa:0x00000000000000bb"
FAKE_THIRD_DATA_ID = "0x00000000000000cc:0x00000000000000dd"
FAKE_NAME = "Test Reviewer Name"
FAKE_PROFILE_URL = "https://www.google.com/maps/contrib/999000111222333444555?hl=en"
FAKE_THUMBNAIL = "https://example.invalid/avatar.png"
FAKE_SNIPPET = "Private review text that must never be printed."
FAKE_PLACE_TITLE = "Private Place Title"
FAKE_ADDRESS = "1 Private Street, Nowhere"
FAKE_TYPE = "Private category"
FAKE_DATE = "3 private months ago"
FAKE_LATITUDE = 12.3456789

EXPECTED_PARAMS = {"engine": "google_maps_contributor_reviews", "contributor_id": FAKE_SELECTED_ID}


def _review(contributor_id: object, reviews: object = 1) -> dict:
    return {
        "review_id": "rev",
        "snippet": FAKE_SNIPPET,
        "user": {
            "name": FAKE_NAME,
            "link": FAKE_PROFILE_URL,
            "contributor_id": contributor_id,
            "thumbnail": FAKE_THUMBNAIL,
            "reviews": reviews,
            "photos": 0,
        },
    }


def _page(reviews: list) -> dict:
    return {
        "search_parameters": {"engine": "google_maps_reviews", "data_id": exp.SEED_DATA_ID, "hl": "en"},
        "reviews": reviews,
    }


def _call4(selected: object = None) -> dict:
    """Ten reviews; the selected reviewer sits at the configured position."""
    reviews = [_review(f"page2-id-{n}") for n in range(1, 11)]
    reviews[3] = _review(FAKE_SELECTED_ID, 9) if selected is None else selected
    return _page(reviews)


def _call2() -> dict:
    return _page([_review(FAKE_CALL3_ID)] + [_review(f"page1-id-{n}") for n in range(2, 9)])


def _call3(queried: object = FAKE_CALL3_ID) -> dict:
    return {"search_parameters": {"engine": "google_maps_contributor_reviews", "contributor_id": queried}}


def _entry(data_id: object = FAKE_OTHER_DATA_ID, **overrides: object) -> dict:
    entry = {
        "place_info": {
            "title": FAKE_PLACE_TITLE,
            "address": FAKE_ADDRESS,
            "gps_coordinates": {"latitude": FAKE_LATITUDE, "longitude": FAKE_LATITUDE},
            "type": FAKE_TYPE,
            "thumbnail": FAKE_THUMBNAIL,
            "data_id": data_id,
        },
        "date": FAKE_DATE,
        "snippet": FAKE_SNIPPET,
        "review_id": "rev",
        "rating": 5,
        "link": FAKE_PROFILE_URL,
    }
    entry.update(overrides)
    return entry


def _make_stdout() -> io.TextIOWrapper:
    # main() calls sys.stdout.reconfigure(), which io.StringIO does not support.
    # newline="\n" keeps captured output identical across platforms.
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")


def _read(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


class SelectContributorIdTests(unittest.TestCase):
    def _assert_fails(self, call4: dict, pattern: str) -> None:
        with self.assertRaisesRegex(exp.Call5SetupError, pattern) as ctx:
            exp.select_contributor_id(call4)
        self.assertNotIn(FAKE_SELECTED_ID, str(ctx.exception))

    def test_returns_the_contributor_at_the_configured_position(self):
        self.assertEqual(exp.select_contributor_id(_call4()), FAKE_SELECTED_ID)

    def test_selection_is_by_position_not_by_highest_count(self):
        call4 = _call4()
        call4["reviews"][0] = _review("busier-reviewer", 500)
        self.assertEqual(exp.select_contributor_id(call4), FAKE_SELECTED_ID)

    def test_reported_review_count_must_be_the_integer_six(self):
        for reported in (8, 10, "9", 9.0, True, None, [9]):
            with self.subTest(reported=reported):
                self._assert_fails(_call4(_review(FAKE_SELECTED_ID, reported)), "not the integer 9")
        selected = _review(FAKE_SELECTED_ID, 9)
        del selected["user"]["reviews"]
        self._assert_fails(_call4(selected), "not the integer 9")

    def test_contributor_id_must_be_a_non_empty_string(self):
        for contributor_id in ("", "   ", None, 12345, ["id"]):
            with self.subTest(contributor_id=contributor_id):
                self._assert_fails(_call4(_review(contributor_id, 9)), "not a non-empty string")

    def test_malformed_selected_review_fails(self):
        for selected in ("text", 7, [], {"review_id": "no-user"}, {"user": "name"}):
            with self.subTest(selected=selected):
                self._assert_fails(_call4(selected), "has no 'user' object")

    def test_too_few_reviews_fails(self):
        self._assert_fails(_page([_review(FAKE_SELECTED_ID, 9)] * 3), "position 4 does not exist")

    def test_missing_or_malformed_reviews_collection_fails(self):
        for reviews in (None, {"4": _review(FAKE_SELECTED_ID, 9)}, "reviews"):
            with self.subTest(reviews=reviews):
                self._assert_fails(_page(reviews), "no 'reviews' list")

    def test_response_for_another_business_fails(self):
        call4 = _call4()
        call4["search_parameters"]["data_id"] = FAKE_OTHER_DATA_ID
        self._assert_fails(call4, "not the seed business")
        del call4["search_parameters"]
        self._assert_fails(call4, "not the seed business")


class DuplicateHandlingTests(unittest.TestCase):
    def test_contributor_queried_in_call3_fails(self):
        with self.assertRaisesRegex(exp.Call5SetupError, "already queried in Call #3") as ctx:
            exp.check_not_already_queried(FAKE_SELECTED_ID, _call3(FAKE_SELECTED_ID))
        self.assertNotIn(FAKE_SELECTED_ID, str(ctx.exception))

    def test_different_call3_contributor_passes(self):
        exp.check_not_already_queried(FAKE_SELECTED_ID, _call3())

    def test_unreadable_call3_contributor_fails_rather_than_assuming(self):
        for call3 in ({}, {"search_parameters": None}, _call3(""), _call3(None), _call3(123)):
            with self.subTest(call3=call3):
                with self.assertRaisesRegex(exp.Call5SetupError, "cannot confirm"):
                    exp.check_not_already_queried(FAKE_SELECTED_ID, call3)

    def test_other_positions_reports_positions_only(self):
        reviews = [_review(FAKE_SELECTED_ID), _review("x"), None, {"user": None}, _review(FAKE_SELECTED_ID)]
        self.assertEqual(exp.other_positions(FAKE_SELECTED_ID, reviews), [1, 5])
        self.assertEqual(exp.other_positions(FAKE_SELECTED_ID, reviews, skip=5), [1])
        self.assertEqual(exp.other_positions("absent", reviews), [])


class BuildParamsTests(unittest.TestCase):
    def test_builds_exactly_the_two_observed_parameters(self):
        self.assertEqual(exp.build_params(FAKE_SELECTED_ID), EXPECTED_PARAMS)

    def test_cache_key_does_not_contain_contributor_id(self):
        key = exp.cache_key(exp.build_params(FAKE_SELECTED_ID))
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(FAKE_SELECTED_ID, key)


class LoadRawResponseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call5SetupError, "Call #4 raw response not found"):
            exp.load_raw_response(self.tmp / "missing.json", "Call #4")

    def test_invalid_json_fails_without_quoting_content(self):
        path = self.tmp / "bad.json"
        path.write_text(f'{{"reviews": [{FAKE_SELECTED_ID}', encoding="utf-8")
        with self.assertRaises(exp.Call5SetupError) as ctx:
            exp.load_raw_response(path, "Call #4")
        self.assertIn("could not be read as JSON", str(ctx.exception))
        self.assertNotIn(FAKE_SELECTED_ID, str(ctx.exception))

    def test_non_object_top_level_fails_clearly(self):
        path = self.tmp / "list.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(exp.Call5SetupError, "top level is list"):
            exp.load_raw_response(path, "Call #4")


class MainTests(unittest.TestCase):
    """main() with synthetic Call #2/#3/#4 files; `search` is always patched."""

    SENSITIVE = [
        FAKE_SELECTED_ID,
        FAKE_CALL3_ID,
        FAKE_OTHER_DATA_ID,
        FAKE_THIRD_DATA_ID,
        exp.SEED_DATA_ID,
        FAKE_NAME,
        FAKE_PROFILE_URL,
        FAKE_THUMBNAIL,
        FAKE_SNIPPET,
        FAKE_PLACE_TITLE,
        FAKE_ADDRESS,
        FAKE_TYPE,
        FAKE_DATE,
        str(FAKE_LATITUDE),
        "http",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.paths = {name: tmp / f"{name}.json" for name in ("call2", "call3", "call4")}
        self._write("call2", _call2())
        self._write("call3", _call3())
        self._write("call4", _call4())

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, response: dict) -> None:
        self.paths[name].write_text(json.dumps(response), encoding="utf-8")

    def _run_main(self, search_mock: mock.Mock):
        stdout, stderr = _make_stdout(), io.StringIO()
        with mock.patch.object(exp, "search", search_mock), \
                mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = exp.main(self.paths["call2"], self.paths["call3"], self.paths["call4"])
        return code, _read(stdout), stderr.getvalue()

    def _assert_no_sensitive_output(self, *outputs: str) -> None:
        for output in outputs:
            # The raw-response directory line is a local path, not response data.
            lines = [line for line in output.splitlines() if not line.startswith("Sanitized raw")]
            for value in self.SENSITIVE:
                self.assertNotIn(value, "\n".join(lines))

    def _rows(self, out: str) -> list[list[str]]:
        lines = [line for line in out.splitlines() if " | " in line][1:]
        return [[cell.strip() for cell in line.split(" | ")] for line in lines]

    def test_success_calls_search_once_and_reports_counts_only(self):
        response = {
            "search_parameters": dict(EXPECTED_PARAMS),
            "contributor": {"name": FAKE_NAME, "thumbnail": FAKE_THUMBNAIL},
            "reviews": [
                _entry(exp.SEED_DATA_ID),
                _entry(FAKE_OTHER_DATA_ID),
                _entry(FAKE_OTHER_DATA_ID),
                _entry(FAKE_THIRD_DATA_ID),
            ],
        }
        search_mock = mock.Mock(return_value=response)
        code, out, err = self._run_main(search_mock)

        self.assertEqual(code, 0)
        search_mock.assert_called_once_with(EXPECTED_PARAMS)
        self.assertEqual(err, "")
        self.assertIn(f"Cache key prefix: {exp.cache_key(EXPECTED_PARAMS)[:16]}", out)
        self.assertIn("Same contributor at Call #2 positions: none", out)
        self.assertIn("Same contributor at other Call #4 positions: none", out)
        self.assertIn("Number of history entries: 4", out)
        self.assertEqual(
            self._rows(out),
            [
                ["1", "YES", "YES", "PRESENT", "PRESENT", "PRESENT"],
                ["2", "YES", "NO", "PRESENT", "PRESENT", "PRESENT"],
                ["3", "YES", "NO", "PRESENT", "PRESENT", "PRESENT"],
                ["4", "YES", "NO", "PRESENT", "PRESENT", "PRESENT"],
            ],
        )
        self.assertIn("Entries with a valid place_info.data_id: 4 of 4", out)
        self.assertIn("Distinct business data_ids: 3", out)
        self.assertIn("Seed business appears: YES", out)
        self.assertIn("Businesses other than the seed: 2", out)
        self.assertIn("Further pagination token present: NO", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_and_malformed_entry_fields_are_reported_not_guessed(self):
        no_optional = _entry(FAKE_OTHER_DATA_ID, rating=None)
        del no_optional["date"]
        del no_optional["place_info"]["type"]
        response = {
            "reviews": [
                no_optional,
                _entry(""),
                _entry(None),
                _entry(12345),
                {"rating": 4, "date": FAKE_DATE},
                {"place_info": FAKE_PLACE_TITLE},
                FAKE_SNIPPET,
            ],
            "serpapi_pagination": {"next_page_token": "FAKE-TOKEN-must-not-print"},
        }
        code, out, err = self._run_main(mock.Mock(return_value=response))

        self.assertEqual(code, 0)
        self.assertEqual(
            self._rows(out),
            [
                ["1", "YES", "NO", "MISSING", "MISSING", "MISSING"],
                ["2", "NO", "UNKNOWN", "PRESENT", "PRESENT", "PRESENT"],
                ["3", "NO", "UNKNOWN", "PRESENT", "PRESENT", "PRESENT"],
                ["4", "NO", "UNKNOWN", "PRESENT", "PRESENT", "PRESENT"],
                ["5", "NO", "UNKNOWN", "PRESENT", "PRESENT", "MISSING"],
                ["6", "NO", "UNKNOWN", "MISSING", "MISSING", "MISSING"],
                ["7", "NO", "UNKNOWN", "MISSING", "MISSING", "MISSING"],
            ],
        )
        self.assertIn("Entries with a valid place_info.data_id: 1 of 7", out)
        self.assertIn("Distinct business data_ids: 1", out)
        self.assertIn("Seed business appears: NO", out)
        self.assertIn("Businesses other than the seed: 1", out)
        self.assertIn("Further pagination token present: YES", out)
        self.assertNotIn("FAKE-TOKEN-must-not-print", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_or_malformed_history_collection_is_reported(self):
        for response, state in [({}, "MISSING"), ({"reviews": {"0": _entry()}}, "not a list (got dict)")]:
            with self.subTest(state=state):
                code, out, err = self._run_main(mock.Mock(return_value=response))
                self.assertEqual(code, 0)
                self.assertIn(f"'reviews' collection: {state}", out)
                self.assertIn("Number of history entries: 0", out)
                self.assertIn("Seed business appears: NO", out)
                self._assert_no_sensitive_output(out, err)

    def test_cross_page_and_in_page_duplicates_are_reported_by_position(self):
        call2, call4 = _call2(), _call4()
        call2["reviews"][2] = _review(FAKE_SELECTED_ID)
        call4["reviews"][8] = _review(FAKE_SELECTED_ID)
        self._write("call2", call2)
        self._write("call4", call4)
        search_mock = mock.Mock(return_value={"reviews": []})
        code, out, err = self._run_main(search_mock)
        self.assertEqual(code, 0)
        search_mock.assert_called_once_with(EXPECTED_PARAMS)
        self.assertIn("Same contributor at Call #2 positions: [3]", out)
        self.assertIn("Same contributor at other Call #4 positions: [9]", out)
        self._assert_no_sensitive_output(out, err)

    def test_contributor_already_queried_in_call3_fails_before_search(self):
        self._write("call3", _call3(FAKE_SELECTED_ID))
        search_mock = mock.Mock()
        code, out, err = self._run_main(search_mock)
        self.assertEqual(code, 1)
        search_mock.assert_not_called()
        self.assertIn("EXP-001 / Call #5 not run:", err)
        self.assertIn("already queried in Call #3", err)
        self.assertEqual(out, "")
        self._assert_no_sensitive_output(out, err)

    def test_invalid_selection_fails_before_search(self):
        for selected in (_review(FAKE_SELECTED_ID, 5), _review("", 9), "not-an-object"):
            with self.subTest(selected=type(selected).__name__):
                self._write("call4", _call4(selected))
                search_mock = mock.Mock()
                code, out, err = self._run_main(search_mock)
                self.assertEqual(code, 1)
                search_mock.assert_not_called()
                self.assertEqual(out, "")
                self._assert_no_sensitive_output(out, err)

    def test_missing_source_file_fails_before_search(self):
        sources = {"call2": _call2(), "call3": _call3(), "call4": _call4()}
        for name, source in sources.items():
            with self.subTest(name=name):
                self.paths[name].unlink()
                search_mock = mock.Mock()
                code, _, err = self._run_main(search_mock)
                self.assertEqual(code, 1)
                search_mock.assert_not_called()
                self.assertIn("not found", err)
                self._write(name, source)

    def test_search_error_message_redacts_contributor_id(self):
        error = exp.SerpApiError(f"SerpApi error (HTTP 400): bad id {FAKE_SELECTED_ID}")
        search_mock = mock.Mock(side_effect=error)
        code, out, err = self._run_main(search_mock)
        self.assertEqual(code, 1)
        search_mock.assert_called_once_with(EXPECTED_PARAMS)
        self.assertIn(exp.REDACTED_CONTRIBUTOR_ID, err)
        self._assert_no_sensitive_output(out, err)


if __name__ == "__main__":
    unittest.main()
