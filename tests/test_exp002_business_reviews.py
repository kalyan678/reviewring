"""Offline tests for experiments/exp002_business_reviews.py (EXP-002 / Call #6).

`search` is patched in every test that reaches it, and the patched mock asserts
call counts, so no request can reach serpapi.com. Payloads are synthetic and
shaped like the fields observed in the Call #2, #4 and #5 responses; no real
reviewer data, business identifier (other than the seed's), or API credential
is used.

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

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "experiments" / "exp002_business_reviews.py"
_spec = importlib.util.spec_from_file_location("exp002_business_reviews", SCRIPT_PATH)
exp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp)

# Fictional local inputs. The real ones live in the untracked local_inputs.json.
exp.SEED_DATA_ID = "0x00000000000000a0:0x00000000000000b0"

SEED = exp.SEED_DATA_ID
DATA_ID_A = "0x00000000000000aa:0x00000000000000a1"
DATA_ID_B = "0x00000000000000bb:0x00000000000000b1"
DATA_ID_C = "0x00000000000000cc:0x00000000000000c1"
SEED_TYPE = "Seed category"
OTHER_TYPE = "Other category"
FAKE_EXPANDED_ID = "999000111222333444555"
FAKE_NAME = "Test Reviewer Name"
FAKE_PROFILE_URL = "https://www.google.com/maps/contrib/999000111222333444555?hl=en"
FAKE_THUMBNAIL = "https://example.invalid/avatar.png"
FAKE_SNIPPET = "Private review text that must never be printed."
FAKE_PLACE_TITLE = "Private Place Title"
FAKE_ADDRESS = "1 Private Street, Nowhere"
FAKE_TOKEN = "FAKE-TOKEN-must-not-print"


def _entry(data_id: object, category: object = OTHER_TYPE) -> dict:
    return {
        "place_info": {
            "title": FAKE_PLACE_TITLE,
            "address": FAKE_ADDRESS,
            "type": category,
            "thumbnail": FAKE_THUMBNAIL,
            "data_id": data_id,
        },
        "snippet": FAKE_SNIPPET,
        "rating": 5,
    }


def _call5(entries: list | None = None) -> dict:
    if entries is None:
        entries = [_entry(DATA_ID_A, SEED_TYPE), _entry(SEED, SEED_TYPE), _entry(DATA_ID_B), _entry(DATA_ID_C)]
    return {
        "search_parameters": {
            "engine": "google_maps_contributor_reviews",
            "contributor_id": FAKE_EXPANDED_ID,
        },
        "reviews": entries,
    }


def _review(contributor_id: object) -> dict:
    return {
        "review_id": "rev",
        "snippet": FAKE_SNIPPET,
        "user": {
            "name": FAKE_NAME,
            "link": FAKE_PROFILE_URL,
            "contributor_id": contributor_id,
            "thumbnail": FAKE_THUMBNAIL,
            "reviews": 3,
        },
    }


def _call2() -> dict:
    return {"reviews": [_review(f"page1-id-{n}") for n in range(1, 9)]}


def _call4() -> dict:
    """Ten reviews; the Call #5 contributor sits at position 3."""
    reviews = [_review(f"page2-id-{n}") for n in range(1, 11)]
    reviews[2] = _review(FAKE_EXPANDED_ID)
    return {"reviews": reviews}


def _make_stdout() -> io.TextIOWrapper:
    # main() calls sys.stdout.reconfigure(), which io.StringIO does not support.
    # newline="\n" keeps captured output identical across platforms.
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")


def _read(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


class SelectBusinessTests(unittest.TestCase):
    def test_prefers_first_entry_whose_category_differs_from_the_seed(self):
        # Position 1 shares the seed's category, so position 3 is selected.
        self.assertEqual(exp.select_business(_call5()), (3, DATA_ID_B, exp.RULE_CATEGORY))

    def test_selection_is_deterministic(self):
        self.assertEqual(exp.select_business(_call5()), exp.select_business(_call5()))

    def test_first_entry_is_selected_when_its_category_differs(self):
        entries = [_entry(DATA_ID_A), _entry(SEED, SEED_TYPE), _entry(DATA_ID_B)]
        self.assertEqual(exp.select_business(_call5(entries)), (1, DATA_ID_A, exp.RULE_CATEGORY))

    def test_falls_back_to_first_valid_entry_when_all_share_the_seed_category(self):
        entries = [_entry(SEED, SEED_TYPE), _entry(DATA_ID_A, SEED_TYPE), _entry(DATA_ID_B, SEED_TYPE)]
        self.assertEqual(exp.select_business(_call5(entries)), (2, DATA_ID_A, exp.RULE_FALLBACK))

    def test_falls_back_when_the_seed_category_is_unavailable(self):
        for entries in (
            [_entry(DATA_ID_A), _entry(DATA_ID_B)],
            [_entry(SEED, None), _entry(DATA_ID_A), _entry(DATA_ID_B)],
            [_entry(SEED, 7), _entry(DATA_ID_A), _entry(DATA_ID_B)],
        ):
            with self.subTest(first=type(entries[0]["place_info"]["type"]).__name__):
                position, data_id, rule = exp.select_business(_call5(entries))
                self.assertEqual((data_id, rule), (DATA_ID_A, exp.RULE_FALLBACK))

    def test_falls_back_when_candidate_categories_are_missing_or_malformed(self):
        entries = [_entry(SEED, SEED_TYPE), _entry(DATA_ID_A, None), _entry(DATA_ID_B, ""), _entry(DATA_ID_C, 5)]
        self.assertEqual(exp.select_business(_call5(entries)), (2, DATA_ID_A, exp.RULE_FALLBACK))

    def test_the_seed_business_is_never_selected(self):
        entries = [_entry(SEED, SEED_TYPE), _entry(SEED, OTHER_TYPE), _entry(DATA_ID_A, SEED_TYPE)]
        self.assertEqual(exp.select_business(_call5(entries))[1], DATA_ID_A)

    def test_missing_and_malformed_entries_are_skipped(self):
        no_place_info = {"rating": 5}
        entries = [
            None,
            "text",
            no_place_info,
            {"place_info": "title"},
            _entry(""),
            _entry("   "),
            _entry(None),
            _entry(12345),
            _entry(SEED, SEED_TYPE),
            _entry(DATA_ID_A),
        ]
        self.assertEqual(exp.select_business(_call5(entries)), (10, DATA_ID_A, exp.RULE_CATEGORY))

    def test_no_valid_non_seed_business_fails_without_quoting_identifiers(self):
        for entries in ([], [_entry(SEED, SEED_TYPE)], [_entry(""), None, _entry(SEED)]):
            with self.subTest(count=len(entries)):
                with self.assertRaisesRegex(exp.Call6SetupError, "other than the seed business") as ctx:
                    exp.select_business(_call5(entries))
                self.assertNotIn(SEED, str(ctx.exception))

    def test_missing_or_malformed_history_collection_fails(self):
        for reviews in (None, {"1": _entry(DATA_ID_A)}, "reviews"):
            with self.subTest(reviews=reviews):
                call5 = _call5()
                call5["reviews"] = reviews
                with self.assertRaisesRegex(exp.Call6SetupError, "no 'reviews' list"):
                    exp.select_business(call5)


class SeedReviewerRefsTests(unittest.TestCase):
    def test_maps_ids_to_page_and_position_and_skips_malformed_reviews(self):
        pages = [
            (1, [_review("a"), None, {"user": None}, _review(""), _review("b")]),
            (2, [_review("c"), _review("a"), _review(123)]),
        ]
        self.assertEqual(
            exp.seed_reviewer_refs(pages),
            {
                "a": ["page 1/position 1", "page 2/position 2"],
                "b": ["page 1/position 5"],
                "c": ["page 2/position 1"],
            },
        )

    def test_call5_contributor_id_must_be_a_non_empty_string(self):
        self.assertEqual(exp.call5_contributor_id(_call5()), FAKE_EXPANDED_ID)
        for search_parameters in (None, {}, {"contributor_id": ""}, {"contributor_id": 5}):
            with self.subTest(search_parameters=search_parameters):
                with self.assertRaisesRegex(exp.Call6SetupError, "not a non-empty string"):
                    exp.call5_contributor_id({"search_parameters": search_parameters})


class BuildParamsTests(unittest.TestCase):
    def test_builds_exactly_the_observed_request(self):
        self.assertEqual(
            exp.build_params(DATA_ID_B),
            {"engine": "google_maps_reviews", "data_id": DATA_ID_B, "hl": "en"},
        )


class LoadRawResponseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_fails_clearly(self):
        with self.assertRaisesRegex(exp.Call6SetupError, "Call #5 raw response not found"):
            exp.load_raw_response(self.tmp / "missing.json", "Call #5")

    def test_invalid_json_fails_without_quoting_content(self):
        path = self.tmp / "bad.json"
        path.write_text(f'{{"reviews": [{DATA_ID_A}', encoding="utf-8")
        with self.assertRaises(exp.Call6SetupError) as ctx:
            exp.load_raw_response(path, "Call #5")
        self.assertIn("could not be read as JSON", str(ctx.exception))
        self.assertNotIn(DATA_ID_A, str(ctx.exception))

    def test_non_object_top_level_fails_clearly(self):
        path = self.tmp / "list.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(exp.Call6SetupError, "top level is list"):
            exp.load_raw_response(path, "Call #5")


class MainTests(unittest.TestCase):
    """main() with synthetic Call #2/#4/#5 files; `search` is always patched."""

    EXPECTED_PARAMS = {"engine": "google_maps_reviews", "data_id": DATA_ID_B, "hl": "en"}
    SENSITIVE = [
        SEED,
        DATA_ID_A,
        DATA_ID_B,
        DATA_ID_C,
        FAKE_EXPANDED_ID,
        "page1-id",
        "page2-id",
        "new-id",
        SEED_TYPE,
        OTHER_TYPE,
        FAKE_NAME,
        FAKE_PROFILE_URL,
        FAKE_THUMBNAIL,
        FAKE_SNIPPET,
        FAKE_PLACE_TITLE,
        FAKE_ADDRESS,
        FAKE_TOKEN,
        "http",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.paths = {name: tmp / f"{name}.json" for name in ("call2", "call4", "call5")}
        self.sources = {"call2": _call2(), "call4": _call4(), "call5": _call5()}
        for name, source in self.sources.items():
            self._write(name, source)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, response: dict) -> None:
        self.paths[name].write_text(json.dumps(response), encoding="utf-8")

    def _run_main(self, search_mock: mock.Mock):
        stdout, stderr = _make_stdout(), io.StringIO()
        with mock.patch.object(exp, "search", search_mock), \
                mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = exp.main(self.paths["call2"], self.paths["call4"], self.paths["call5"])
        return code, _read(stdout), stderr.getvalue()

    def _assert_no_sensitive_output(self, *outputs: str) -> None:
        for output in outputs:
            # The raw-response directory line is a local path, not response data.
            lines = [line for line in output.splitlines() if not line.startswith("Sanitized raw")]
            for value in self.SENSITIVE:
                self.assertNotIn(value, "\n".join(lines))

    def test_success_calls_search_once_with_the_selected_business(self):
        response = {
            "search_parameters": dict(self.EXPECTED_PARAMS),
            "place_info": {"title": FAKE_PLACE_TITLE, "address": FAKE_ADDRESS, "reviews": 42},
            "reviews": [_review("new-id-1"), _review("new-id-2"), _review("new-id-3")],
        }
        search_mock = mock.Mock(return_value=response)
        code, out, err = self._run_main(search_mock)

        self.assertEqual(code, 0)
        search_mock.assert_called_once_with(self.EXPECTED_PARAMS)
        self.assertEqual(err, "")
        self.assertIn("Selected business: Call #5 history position 3", out)
        self.assertIn(f"Selection rule applied: {exp.RULE_CATEGORY}", out)
        self.assertIn("Seed reviewers with a valid contributor ID (Calls #2 and #4): 18", out)
        self.assertIn(f"Cache key prefix: {exp.cache_key(self.EXPECTED_PARAMS)[:16]}", out)
        self.assertIn("Business reported review count: 42", out)
        self.assertIn("Number of reviews in this response: 3", out)
        self.assertIn("Valid contributor IDs: 3 of 3", out)
        self.assertIn("Distinct contributor IDs: 3", out)
        self.assertIn("Contributors also among the seed reviewers: 0", out)
        self.assertNotIn("this response position(s)", out)
        self.assertIn("Call #5 contributor appears in this response: NO", out)
        self.assertIn("Further pagination token present: NO", out)
        self._assert_no_sensitive_output(out, err)

    def test_overlap_is_counted_and_reported_by_page_and_position_only(self):
        response = {
            "reviews": [
                _review("new-id-1"),
                _review("page1-id-3"),
                _review(FAKE_EXPANDED_ID),
                _review("page2-id-10"),
                _review("page1-id-3"),
                _review(""),
                {"user": None},
                FAKE_SNIPPET,
            ],
            "serpapi_pagination": {"next": FAKE_PROFILE_URL, "next_page_token": FAKE_TOKEN},
        }
        code, out, err = self._run_main(mock.Mock(return_value=response))

        self.assertEqual(code, 0)
        self.assertIn("Business reported review count: MISSING", out)
        self.assertIn("Number of reviews in this response: 8", out)
        self.assertIn("Valid contributor IDs: 5 of 8", out)
        self.assertIn("Distinct contributor IDs: 4", out)
        self.assertIn("Contributors also among the seed reviewers: 3", out)
        self.assertIn("  this response position(s) [2, 5] = seed page 1/position 3", out)
        self.assertIn("  this response position(s) [3] = seed page 2/position 3", out)
        self.assertIn("  this response position(s) [4] = seed page 2/position 10", out)
        self.assertIn("Call #5 contributor appears in this response: YES", out)
        self.assertIn("Further pagination token present: YES", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_or_malformed_response_fields_are_reported_not_guessed(self):
        cases = [
            ({}, "'reviews' collection: MISSING", "MISSING"),
            ({"reviews": {"0": _review("x")}}, "'reviews' collection: not a list (got dict)", "MISSING"),
            ({"reviews": [], "place_info": {"reviews": FAKE_PLACE_TITLE}}, None, "MALFORMED (str)"),
            ({"reviews": [], "place_info": {"reviews": True}}, None, "MALFORMED (bool)"),
            ({"reviews": [], "place_info": FAKE_PLACE_TITLE}, None, "MISSING"),
        ]
        for response, collection_line, reported in cases:
            with self.subTest(reported=reported, collection=collection_line):
                code, out, err = self._run_main(mock.Mock(return_value=response))
                self.assertEqual(code, 0)
                if collection_line:
                    self.assertIn(collection_line, out)
                self.assertIn(f"Business reported review count: {reported}", out)
                self.assertIn("Number of reviews in this response: 0", out)
                self.assertIn("Contributors also among the seed reviewers: 0", out)
                self._assert_no_sensitive_output(out, err)

    def test_invalid_saved_data_fails_before_search(self):
        no_business = _call5([_entry(SEED, SEED_TYPE)])
        no_contributor = _call5()
        del no_contributor["search_parameters"]
        cases = [
            ("call5", no_business, "other than the seed business"),
            ("call5", no_contributor, "contributor_id"),
            ("call5", {"reviews": None}, "no 'reviews' list"),
            ("call2", {"reviews": "text"}, "Call #2 raw response has no 'reviews' list"),
            ("call4", {}, "Call #4 raw response has no 'reviews' list"),
        ]
        for name, content, message in cases:
            with self.subTest(message=message):
                self._write(name, content)
                search_mock = mock.Mock()
                code, out, err = self._run_main(search_mock)
                self.assertEqual(code, 1)
                search_mock.assert_not_called()
                self.assertIn("EXP-002 / Call #6 not run:", err)
                self.assertIn(message, err)
                self.assertEqual(out, "")
                self._assert_no_sensitive_output(out, err)
                self._write(name, self.sources[name])

    def test_missing_source_file_fails_before_search(self):
        for name, source in self.sources.items():
            with self.subTest(name=name):
                self.paths[name].unlink()
                search_mock = mock.Mock()
                code, _, err = self._run_main(search_mock)
                self.assertEqual(code, 1)
                search_mock.assert_not_called()
                self.assertIn("not found", err)
                self._write(name, source)

    def test_search_error_message_redacts_business_data_id(self):
        error = exp.SerpApiError(f"SerpApi error (HTTP 400): bad data_id {DATA_ID_B}")
        search_mock = mock.Mock(side_effect=error)
        code, out, err = self._run_main(search_mock)
        self.assertEqual(code, 1)
        search_mock.assert_called_once_with(self.EXPECTED_PARAMS)
        self.assertIn(exp.REDACTED_DATA_ID, err)
        self._assert_no_sensitive_output(out, err)

    def test_cache_key_does_not_contain_business_data_id(self):
        key = exp.cache_key(self.EXPECTED_PARAMS)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(DATA_ID_B, key)


if __name__ == "__main__":
    unittest.main()
