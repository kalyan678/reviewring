"""Offline tests for experiments/exp002_build_graph.py.

The entry point is run against synthetic raw files written to a temporary
directory. No real response, identifier, or secret is used, and the SerpApi
client is never reachable from the module under test.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "experiments" / "exp002_build_graph.py"
_spec = importlib.util.spec_from_file_location("exp002_build_graph", SCRIPT_PATH)
exp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exp)

# Fictional local inputs. The real ones live in the untracked local_inputs.json.
exp.RAW_FILES.update(
    {
        "google_maps_0000000000000001.json": exp.MAPS_SEARCH_ENGINE,
        "google_maps_reviews_0000000000000002.json": exp.MAPS_REVIEWS_ENGINE,
        "google_maps_contributor_reviews_0000000000000003.json": exp.CONTRIBUTOR_REVIEWS_ENGINE,
        "google_maps_reviews_0000000000000004.json": exp.MAPS_REVIEWS_ENGINE,
        "google_maps_contributor_reviews_0000000000000005.json": exp.CONTRIBUTOR_REVIEWS_ENGINE,
        "google_maps_reviews_0000000000000006.json": exp.MAPS_REVIEWS_ENGINE,
    }
)

SEARCH, PAGE_1, HISTORY_1, PAGE_2, HISTORY_2, PAGE_3 = exp.RAW_FILES

BUSINESS_A = "0x00000000000000aa:0x00000000000000a1"
BUSINESS_B = "0x00000000000000bb:0x00000000000000b1"
BUSINESS_C = "0x00000000000000cc:0x00000000000000c1"
FAKE_NAME = "Test Reviewer Name"
FAKE_SNIPPET = "Private review text that must never be printed."
FAKE_TITLE = "Private Place Title"
FAKE_ADDRESS = "1 Private Street, Nowhere"
FAKE_CATEGORY = "Private category"
FAKE_TOKEN = "FAKE-TOKEN-0001"


def _reviewer(n: int) -> str:
    return f"1000000000000000000{n:02d}"


def _review(n: int, review_id: str) -> dict:
    return {
        "review_id": review_id,
        "rating": 5.0,
        "date": "a month ago",
        "iso_date": "2026-09-01T00:00:00Z",
        "snippet": FAKE_SNIPPET,
        "user": {"name": FAKE_NAME, "link": "https://example.invalid/p", "contributor_id": _reviewer(n), "reviews": 2},
    }


def _page(data_id: str, reviews: list, total: int | None = None, request_token: str | None = None, next_token: str | None = None) -> dict:
    response = {
        "search_parameters": {"engine": "google_maps_reviews", "data_id": data_id, "hl": "en"},
        "reviews": reviews,
    }
    if total is not None:
        response["place_info"] = {"title": FAKE_TITLE, "address": FAKE_ADDRESS, "type": FAKE_CATEGORY, "reviews": total}
    if request_token:
        response["search_parameters"]["next_page_token"] = request_token
    if next_token:
        response["serpapi_pagination"] = {"next": "https://example.invalid/next", "next_page_token": next_token}
    return response


def _history(n: int, entries: list[tuple[str, str]]) -> dict:
    return {
        "search_parameters": {"engine": "google_maps_contributor_reviews", "contributor_id": _reviewer(n)},
        "contributor": {"name": FAKE_NAME},
        "reviews": [
            {
                "place_info": {"title": FAKE_TITLE, "address": FAKE_ADDRESS, "type": FAKE_CATEGORY, "data_id": data_id},
                "review_id": review_id,
                "rating": 5,
                "date": "a month ago",
                "snippet": FAKE_SNIPPET,
            }
            for data_id, review_id in entries
        ],
    }


def synthetic_files() -> dict[str, dict]:
    """Same shape as the real collection: two pages of A, one page of B, two histories."""
    return {
        SEARCH: {"search_parameters": {"engine": "google_maps"}, "local_results": [{"title": FAKE_TITLE}]},
        PAGE_1: _page(BUSINESS_A, [_review(1, "a-1"), _review(2, "a-2")], total=30, next_token=FAKE_TOKEN),
        PAGE_2: _page(BUSINESS_A, [_review(3, "a-3")], request_token=FAKE_TOKEN, next_token="FAKE-TOKEN-0002"),
        # Reviewer 1's history: only the business it was found on.
        HISTORY_1: _history(1, [(BUSINESS_A, "a-1")]),
        # Reviewer 3's history: A (already seen), B, and C.
        HISTORY_2: _history(3, [(BUSINESS_B, "b-3"), (BUSINESS_A, "a-3"), (BUSINESS_C, "c-3")]),
        PAGE_3: _page(BUSINESS_B, [_review(4, "b-4"), _review(3, "b-3")], total=2),
    }


class BuildGraphEntryPointTests(unittest.TestCase):
    SENSITIVE = [
        BUSINESS_A, BUSINESS_B, BUSINESS_C, "10000000000000000", FAKE_NAME, FAKE_SNIPPET,
        FAKE_TITLE, FAKE_ADDRESS, FAKE_CATEGORY, FAKE_TOKEN, "a-1", "b-3", "http",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmp.name) / "raw"
        self.raw_dir.mkdir()
        self.files = synthetic_files()
        for name, content in self.files.items():
            self._write(name, content)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, content: object) -> None:
        (self.raw_dir / name).write_text(json.dumps(content), encoding="utf-8")

    def _run_main(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = exp.main(self.raw_dir)
        return code, stdout.getvalue(), stderr.getvalue()

    def _assert_no_sensitive_output(self, *outputs: str) -> None:
        for output in outputs:
            for value in self.SENSITIVE:
                self.assertNotIn(value, output)

    def test_reports_aggregate_metrics_for_the_merged_graph(self):
        code, out, err = self._run_main()
        self.assertEqual((code, err), (0, ""))
        for line in (
            "google_maps: 1 (no reviewer data; adds no edge)",
            "google_maps_reviews: 3",
            "google_maps_contributor_reviews: 2",
            "Entries skipped while parsing: 0",
            "Reviewers: 4",
            "Businesses: 3",
            "Unique reviewer-business edges: 6",
            "Review observations merged into those edges: 9",
            "Edges observed more than once: 3",
            "Connected components: 1",
            "  1 business(es): 3 reviewer(s)",
            "  3 business(es): 1 reviewer(s)",
            "  1 reviewer(s): 1 business(es)",
            "  2 reviewer(s): 1 business(es)",
            "  3 reviewer(s): 1 business(es)",
            # A: pairs among reviewers 1-3 (3 pairs); B: reviewers 3 and 4 (1 pair).
            "Reviewer pairs sharing at least one business: 4",
            "Reviewer pairs sharing at least two businesses: 0",
            "Edges with conflicting observations: 0",
            "Businesses with at least one Google Maps Reviews page: 2",
            "Businesses seen only in contributor histories: 1",
            "pages captured: 2 | reviewers captured: 3 | reported review total: 30 | pagination tokens not followed: 1",
            "pages captured: 1 | reviewers captured: 2 | reported review total: 2 | pagination tokens not followed: 0",
            "Reviewers with a contributor history fetched: 2 of 4",
            "Contributor histories offering a further page: 0",
        ):
            self.assertIn(line, out)
        self._assert_no_sensitive_output(out, err)

    def test_output_is_reproducible(self):
        self.assertEqual(self._run_main(), self._run_main())

    def test_skipped_entries_and_conflicts_are_counted_not_described(self):
        page = self.files[PAGE_3]
        page["reviews"].append({"snippet": FAKE_SNIPPET, "user": {"name": FAKE_NAME}})
        page["reviews"][1]["rating"] = 1.0
        self._write(PAGE_3, page)
        code, out, err = self._run_main()
        self.assertEqual(code, 0)
        self.assertIn("Entries skipped while parsing: 1", out)
        self.assertIn("  missing_contributor_id: 1", out)
        self.assertIn("Edges with conflicting observations: 1", out)
        self.assertIn("  rating: 1", out)
        self._assert_no_sensitive_output(out, err)

    def test_missing_or_unusable_file_stops_the_build_without_quoting_content(self):
        bad_engine = dict(self.files[PAGE_1], search_parameters={"engine": "google_maps_contributor_reviews"})
        no_business = _page("", [_review(1, "a-1")])
        cases = [
            (SEARCH, None, "Raw response not found"),
            (HISTORY_2, None, "Raw response not found"),
            (PAGE_1, f'{{"reviews": [{FAKE_SNIPPET}', "could not be read as JSON"),
            (PAGE_1, "[]", "top level is list"),
            (PAGE_1, json.dumps(bad_engine), "is not a 'google_maps_reviews' response"),
            (PAGE_1, json.dumps({"reviews": []}), "is not a 'google_maps_reviews' response"),
            (PAGE_1, json.dumps(no_business), "data_id is not a non-empty string"),
        ]
        for name, text, message in cases:
            with self.subTest(message=message, name=name):
                path = self.raw_dir / name
                if text is None:
                    path.unlink()
                else:
                    path.write_text(text, encoding="utf-8")
                code, out, err = self._run_main()
                self.assertEqual((code, out), (1, ""))
                self.assertIn("Graph not built:", err)
                self.assertIn(message, err)
                self.assertIn(name, err)
                self._assert_no_sensitive_output(err.replace(str(self.raw_dir), ""))
                self._write(name, self.files[name])

    def test_only_the_expected_files_in_the_raw_directory_are_read(self):
        self._write("google_maps_reviews_ffffffffffffffff.json", _page(BUSINESS_C, [_review(9, "c-9")]))
        _, out, _ = self._run_main()
        self.assertIn("Reviewers: 4", out)
        self.assertEqual(len(exp.RAW_FILES), 6)

    def test_runs_without_any_secret_and_cannot_reach_the_serpapi_client(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, _, _ = self._run_main()
        self.assertEqual(code, 0)
        self.assertFalse(hasattr(exp, "search"))
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for forbidden in ("import serpapi_client", "from reviewring.serpapi_client", "httpx"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
