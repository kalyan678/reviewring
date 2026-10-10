"""Offline tests for live investigations: reviewring.investigation, reviewring.live,
and the HTTP endpoints that expose them.

SerpApi is replaced by ``FakeSerpApi`` everywhere. It records every call and
whether it would have been a new request, so tests can assert exactly how many
requests an action would send. No request leaves the machine and no API key is
read.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring import app, live  # noqa: E402
from reviewring.graph import build_graph, compute_metrics  # noqa: E402
from reviewring.indicators import expansion_candidates  # noqa: E402
from reviewring.investigation import (  # noqa: E402
    DEFAULT_HISTORY_LIMIT,
    expand,
    choose_reviewers,
    page_statement,
    usable_page_token,
    collect,
    describe_error,
    history_params,
    review_page_params,
    search_params,
)
from reviewring.models import MAPS_REVIEWS_ENGINE, ParsedResponse, ReviewerAttributes  # noqa: E402
from reviewring.parsers import parse_maps_reviews  # noqa: E402
from reviewring.serpapi_client import cache_key  # noqa: E402
from reviewring.views import INVESTIGATION, build_view  # noqa: E402
from reviewring.serpapi_client import MissingApiKeyError, SerpApiError  # noqa: E402

BUSINESS = "0x00000000000000aa:0x00000000000000a1"
OTHER_BUSINESS = "0x00000000000000bb:0x00000000000000b1"
SHARED_BUSINESS = "0x00000000000000cc:0x00000000000000c1"
# Businesses that appear only in fictional reviewer histories, for expansion tests.
EXTRA_X = "0x00000000000000e1:0x0000000000000001"
EXTRA_Y = "0x00000000000000e2:0x0000000000000002"
POPULAR = "0x00000000000000e3:0x0000000000000003"
OTHER_TITLE = "Private Other Title"
PRIVATE_NAME = "Private Reviewer Name"
PRIVATE_TEXT = "Private review text."
# Reported review counts for reviewers 1-6 on the page, in page order.
REPORTED = {1: 1, 2: 5, 3: 3, 4: None, 5: 2, 6: 4, 7: 3, 8: 2, 9: 60, 10: 1, 11: 4, 12: None}
FIRST_PAGE = [1, 2, 3, 4, 5, 6]
# A business with four review pages, for pagination tests.
FOUR_PAGES = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]


def reviewer(n: int) -> str:
    return f"1000000000000000000{n:02d}"


def key(params: dict) -> frozenset:
    return frozenset(params.items())


class FakeSerpApi:
    """Stands in for serpapi_client.search / is_cached. Never touches the network."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.new_requests = 0
        self.saved: set[frozenset] = set()
        self.fail_new_request_number: int | None = None
        self.error: Exception = SerpApiError("SerpApi request failed (ReadTimeout)")
        self.key_configured = True
        # Reviewers on each review page. After the last page a token is still
        # offered when ``more_after_last`` is set (the business has more pages
        # than this fake defines); asking for it is rejected.
        self.review_pages: list[list[int]] = [FIRST_PAGE]
        self.more_after_last = True
        self.token_overrides: dict[int, object] = {}  # page index -> next token to return instead
        # Expansion fixtures: further businesses in a reviewer's history, and the
        # single review page of a business other than the investigated one.
        self.history_extra: dict[int, list[str]] = {}
        self.shared_history = True  # reviewers 2 and 3 both list one further business
        self.history_lists_seed = True  # a history lists the investigated business itself
        self.business_pages: dict[str, list[int]] = {}

    def use_overlap_fixture(self) -> None:
        """Reviewers 2 and 3 share three businesses beyond the investigated one; 2 and 5 share one."""
        self.history_extra = {2: [EXTRA_X, EXTRA_Y, POPULAR], 3: [EXTRA_X, EXTRA_Y], 5: [POPULAR]}
        self.business_pages = {
            SHARED_BUSINESS: [2, 3, 30, 31, 32],
            EXTRA_X: [2, 3, 5, 33, 34, 35, 36, 37],
            EXTRA_Y: [3, 6, 38],
            POPULAR: [2, 5, 39],
        }

    def next_token(self, index: int) -> object:
        if index in self.token_overrides:
            return self.token_overrides[index]
        if index + 1 < len(self.review_pages):
            return f"PRIVATE-TOKEN-{index + 2}"
        return "PRIVATE-TOKEN" if self.more_after_last else None

    def is_cached(self, params: dict) -> bool:
        return key(params) in self.saved

    def search(self, params: dict) -> dict:
        self.calls.append(dict(params))
        if not self.is_cached(params):
            if not self.key_configured:
                raise MissingApiKeyError("SERPAPI_API_KEY is not set.")
            self.new_requests += 1
            if self.new_requests == self.fail_new_request_number:
                raise self.error
            response = self.respond(params)  # a rejected request is never saved
            self.saved.add(key(params))
            return response
        return self.respond(params)

    def respond(self, params: dict) -> dict:
        engine = params["engine"]
        if engine == "google_maps":
            return {
                "search_parameters": dict(params),
                "local_results": [
                    {"title": "Alpha Bakery", "data_id": BUSINESS, "type": "Bakery", "rating": 4.5, "reviews": 120, "address": "1 Test Street"},
                    {"title": "Beta Bakery", "data_id": OTHER_BUSINESS, "rating": "n/a", "reviews": "many"},
                    {"title": "No identifier"},
                    "not-an-object",
                ],
            }
        if engine == "google_maps_reviews" and params.get("data_id") in self.business_pages and "next_page_token" not in params:
            data_id = params["data_id"]
            return {
                "search_parameters": dict(params),
                "place_info": {"title": OTHER_TITLE, "type": "Cafe", "reviews": 4321, "rating": 3.7},
                "reviews": [
                    {
                        # The same review a history lists for this reviewer and business.
                        "review_id": f"other-{n}" if data_id == SHARED_BUSINESS else f"rev-{n}-{data_id[-4:]}",
                        "rating": 5.0,
                        "snippet": PRIVATE_TEXT,
                        "user": {"name": PRIVATE_NAME, "contributor_id": reviewer(n)},
                    }
                    for n in self.business_pages[data_id]
                ],
            }
        if engine == "google_maps_reviews":
            token = params.get("next_page_token")
            tokens: dict[object, int] = {}
            for i in range(len(self.review_pages) - 1):
                if isinstance(self.next_token(i), str):
                    tokens.setdefault(self.next_token(i), i + 1)  # a repeated token leads to the first page it named
            index = 0 if token is None else tokens.get(token)
            if index is None:
                raise SerpApiError(f"SerpApi error (HTTP 400): Invalid next_page_token {token}")
            response = {
                "search_parameters": dict(params),
                "reviews": [
                    {
                        "review_id": f"page-{n}",
                        "rating": 5.0,
                        "snippet": PRIVATE_TEXT,
                        "user": {"name": PRIVATE_NAME, "contributor_id": reviewer(n), **({"reviews": REPORTED[n]} if REPORTED.get(n) is not None else {})},
                    }
                    for n in self.review_pages[index]
                ],
            }
            if index == 0:  # observed: only the first page carries place_info
                response["place_info"] = {"title": "Alpha Bakery", "type": "Bakery", "reviews": 120, "rating": 4.5}
            following = self.next_token(index)
            if following is not None:
                response["serpapi_pagination"] = {"next_page_token": following}
            return response
        number = int(params["contributor_id"][-2:])
        entries = [{"review_id": f"page-{number}", "rating": 5, "place_info": {"data_id": BUSINESS, "type": "Bakery"}}] if self.history_lists_seed else []
        # Reviewers 2 and 3 both also reviewed one other business.
        other = SHARED_BUSINESS if number in (2, 3) and self.shared_history else f"0x00000000000000dd:0x000000000000{number:04x}"
        entries.append({"review_id": f"other-{number}", "rating": 4, "snippet": PRIVATE_TEXT, "place_info": {"data_id": other, "type": "Cafe"}})
        for extra in self.history_extra.get(number, []):
            entries.append({"review_id": f"rev-{number}-{extra[-4:]}", "rating": 5, "snippet": PRIVATE_TEXT, "place_info": {"data_id": extra, "type": "Gym"}})
        return {"search_parameters": dict(params), "contributor": {"name": PRIVATE_NAME}, "reviews": entries}


def new_state(fake: FakeSerpApi) -> live.LiveState:
    return live.LiveState(search=fake.search, is_cached=fake.is_cached, key_configured=lambda: fake.key_configured)


PRIVATE = [BUSINESS, OTHER_BUSINESS, SHARED_BUSINESS, "10000000000000000", PRIVATE_NAME, PRIVATE_TEXT, "PRIVATE-TOKEN", "page-2", "other-2", "0x0000", OTHER_TITLE, "4321", "3.7", "rev-2-"]


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSerpApi()

    def run_collect(self, histories: int, max_requests: int):
        return collect(BUSINESS, histories, max_requests, self.api.search, self.api.is_cached)

    def test_reviewers_whose_history_fits_come_first_in_page_order(self):
        page = parse_maps_reviews(self.api.respond(review_page_params(BUSINESS)), "page")
        # Reported counts 5, 3, 2, 4 all fit the default window: page order, not most active first.
        # Then the reviewer with no reported count. The single-review reviewer is never chosen.
        self.assertEqual(choose_reviewers(page, 10), [reviewer(n) for n in (2, 3, 5, 6, 4)])
        self.assertEqual(choose_reviewers(page, 2), [reviewer(2), reviewer(3)])
        self.assertEqual(choose_reviewers(page, 0), [])

    def test_prolific_reviewers_come_last_least_truncated_first(self):
        counts = {1: 900, 2: 520, 3: 7, 4: None, 5: 1, 6: 12, 7: 10, 8: 60}
        page = ParsedResponse(
            "page",
            MAPS_REVIEWS_ENGINE,
            reviewer_attributes=[ReviewerAttributes(reviewer(n), "page", reported_review_count=c) for n, c in counts.items()],
        )
        # Window of 10: reviewers 3 and 7 fit (page order); 4 is unknown; then 12, 60, 520, 900.
        self.assertEqual(choose_reviewers(page, 10, limit=10), [reviewer(n) for n in (3, 7, 4, 6, 8, 2, 1)])
        # A larger window changes who fits, never by ranking the busiest first.
        self.assertEqual(choose_reviewers(page, 4, limit=100), [reviewer(n) for n in (3, 6, 7, 8)])
        self.assertEqual(choose_reviewers(page, 10, limit=10), choose_reviewers(page, 10, limit=10))

    def test_a_reviewer_listed_twice_is_chosen_once(self):
        page = ParsedResponse(
            "page",
            MAPS_REVIEWS_ENGINE,
            reviewer_attributes=[ReviewerAttributes(reviewer(2), "page", reported_review_count=4)] * 2,
        )
        self.assertEqual(choose_reviewers(page, 5), [reviewer(2)])

    def test_entry_limit_is_part_of_the_request_and_its_cache_identity(self):
        default = history_params("c1")
        self.assertEqual(default, {"engine": "google_maps_contributor_reviews", "contributor_id": "c1"})
        self.assertEqual(history_params("c1", 10), default)  # the API default sends no `num`
        wider = history_params("c1", 50)
        self.assertEqual(wider, {"engine": "google_maps_contributor_reviews", "contributor_id": "c1", "num": 50})
        self.assertEqual(len({cache_key(default), cache_key(wider), cache_key(history_params("c1", 200))}), 3)
        for bad in (0, 9, 201, 1000, -5):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    history_params("c1", bad)

    def test_unexpected_error_keeps_what_was_parsed_and_never_raises(self):
        original, calls = self.api.search, {"n": 0}

        def flaky(params):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError(f"boom {reviewer(3)} {BUSINESS}")
            return original(params)

        result = collect(BUSINESS, 3, 4, flaky, self.api.is_cached)
        self.assertEqual((len(result.parsed), result.histories_collected, self.api.new_requests), (2, 1, 2))
        self.assertEqual(result.stopped, "Collection stopped because of an unexpected error.")

        def broken_progress(collection):
            raise RuntimeError("progress failed")

        result = collect(BUSINESS, 1, 2, self.api.search, self.api.is_cached, broken_progress)
        self.assertTrue(result.parsed)
        self.assertIn("unexpected", result.stopped)

    def test_sends_exactly_one_page_request_and_the_chosen_histories(self):
        result = self.run_collect(histories=2, max_requests=3)
        self.assertEqual(
            self.api.calls,
            [
                review_page_params(BUSINESS),
                history_params(reviewer(2), DEFAULT_HISTORY_LIMIT),
                history_params(reviewer(3), DEFAULT_HISTORY_LIMIT),
            ],
        )
        self.assertEqual((result.requests_sent, result.cache_hits, result.histories_collected, result.stopped), (3, 0, 2, None))
        self.assertEqual(len(result.parsed), 3)

    def test_never_paginates_or_follows_businesses_found_in_histories(self):
        self.run_collect(histories=8, max_requests=9)
        engines = [call["engine"] for call in self.api.calls]
        self.assertEqual(engines.count("google_maps_reviews"), 1)
        self.assertEqual(len(self.api.calls), 6)  # one page + the five eligible reviewers
        for call in self.api.calls:
            self.assertNotIn("next_page_token", call)
            self.assertNotEqual(call.get("data_id"), SHARED_BUSINESS)

    def test_budget_stops_collection_before_an_extra_request(self):
        result = self.run_collect(histories=3, max_requests=2)
        self.assertEqual(self.api.new_requests, 2)
        self.assertEqual((result.requests_sent, result.histories_collected), (2, 1))
        self.assertIn("budget", result.stopped)
        self.assertEqual(len(result.parsed), 2)

    def test_zero_budget_sends_nothing(self):
        result = self.run_collect(histories=3, max_requests=0)
        self.assertEqual((self.api.calls, result.parsed), ([], []))
        self.assertIn("budget", result.stopped)

    def test_saved_responses_are_reused_and_do_not_count(self):
        self.api.saved |= {key(review_page_params(BUSINESS)), key(history_params(reviewer(2), DEFAULT_HISTORY_LIMIT))}
        result = self.run_collect(histories=2, max_requests=1)
        self.assertEqual((self.api.new_requests, result.requests_sent, result.cache_hits), (1, 1, 2))
        self.assertEqual((result.histories_collected, result.stopped), (2, None))

    def test_failure_keeps_what_was_collected_and_stops(self):
        self.api.fail_new_request_number = 3
        result = self.run_collect(histories=3, max_requests=4)
        self.assertEqual(self.api.new_requests, 3)  # no attempt after the failure
        self.assertEqual((len(result.parsed), result.histories_collected), (2, 1))
        self.assertIn("failed or timed out", result.stopped)

    def test_failure_on_the_page_collects_nothing(self):
        self.api.fail_new_request_number = 1
        result = self.run_collect(histories=3, max_requests=4)
        self.assertEqual((result.parsed, self.api.new_requests), ([], 1))

    def test_response_for_another_business_or_contributor_is_rejected(self):
        original = self.api.respond
        self.api.respond = lambda params: original({**params, "data_id": OTHER_BUSINESS} if "data_id" in params else params)
        result = self.run_collect(histories=2, max_requests=3)
        self.assertEqual(result.parsed, [])
        self.assertIn("could not be used", result.stopped)

    def test_error_descriptions_never_repeat_serpapi_text(self):
        leaky = SerpApiError(f"SerpApi error (HTTP 400): bad contributor_id {reviewer(2)} for {BUSINESS}")
        self.assertEqual(describe_error(leaky), "SerpApi rejected the request (HTTP 400).")
        self.assertEqual(describe_error(SerpApiError("SerpApi returned HTTP 429")), "SerpApi rejected the request (HTTP 429).")
        for error in (leaky, SerpApiError(f"Cached response is not valid JSON: C:/x/{BUSINESS}.json"), MissingApiKeyError("x"), RuntimeError(reviewer(2))):
            text = describe_error(error)
            for private in PRIVATE:
                self.assertNotIn(private, text)

    def test_request_parameters_match_the_verified_contracts(self):
        self.assertEqual(search_params("bakery"), {"engine": "google_maps", "q": "bakery", "type": "search"})
        self.assertEqual(review_page_params(BUSINESS), {"engine": "google_maps_reviews", "data_id": BUSINESS, "hl": "en"})
        self.assertEqual(history_params("c1"), {"engine": "google_maps_contributor_reviews", "contributor_id": "c1"})


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSerpApi()
        self.state = new_state(self.api)

    def search(self, query: str = "bakery in testville") -> list[dict]:
        return self.state.run_search(self.state.plan_search(query)["plan"])["businesses"]

    def investigate(self, histories: int = 2) -> dict:
        ref = self.search()[0]["ref"]
        plan = self.state.plan_investigation(ref, histories)
        started = self.state.start_investigation(plan["plan"])
        self.state._investigations[started["investigation"]].thread.join(timeout=10)
        return self.state.investigation_status(started["investigation"])

    def assert_error(self, status: int, call, *args):
        with self.assertRaises(live.LiveError) as ctx:
            call(*args)
        self.assertEqual(ctx.exception.status, status)
        for private in PRIVATE:
            self.assertNotIn(private, ctx.exception.message)

    def test_planning_sends_no_request_and_states_the_maximum(self):
        plan = self.state.plan_search("bakery in testville")
        self.assertEqual((plan["max_requests"], self.api.calls), (1, []))
        self.assertIn("At most 1 SerpApi request", plan["statement"])
        ref = self.search()[0]["ref"]
        calls_before = len(self.api.calls)
        plan = self.state.plan_investigation(ref, 3)
        self.assertEqual((plan["max_requests"], len(self.api.calls)), (4, calls_before))
        self.assertIn("At most 4 SerpApi requests", plan["statement"])
        self.assertIn("unknown", plan["statement"])

    def test_search_input_is_validated_before_any_request(self):
        for query in (None, "", "  ", "ab", "x" * 121, 42, ["bakery"], "bakery\nq=1", "bakery\x00"):
            with self.subTest(query=query):
                self.assert_error(400, self.state.plan_search, query)
        self.assertEqual(self.api.calls, [])

    def test_a_saved_search_is_reported_as_costing_nothing(self):
        self.search()
        plan = self.state.plan_search("bakery in testville")
        self.assertEqual(plan["max_requests"], 0)
        self.assertIn("No SerpApi request", plan["statement"])
        self.state.run_search(plan["plan"])
        self.assertEqual(self.api.new_requests, 1)

    def test_execution_requires_a_valid_single_use_plan_of_the_right_kind(self):
        for bad in (None, "", "not-a-plan", 5, {"plan": 1}):
            with self.subTest(plan=bad):
                self.assert_error(409, self.state.run_search, bad)
                self.assert_error(409, self.state.start_investigation, bad)
        plan = self.state.plan_search("bakery in testville")["plan"]
        self.assert_error(409, self.state.start_investigation, plan)  # wrong kind also consumes nothing usable
        plan = self.state.plan_search("bakery in testville")["plan"]
        self.state.run_search(plan)
        self.assert_error(409, self.state.run_search, plan)
        self.assertEqual(self.api.new_requests, 1)

    def test_expired_plan_is_refused(self):
        plan = self.state.plan_search("bakery in testville")["plan"]
        with mock.patch.object(live.time, "monotonic", return_value=live.time.monotonic() + live.PLAN_LIFETIME_SECONDS + 1):
            self.assert_error(409, self.state.run_search, plan)
        self.assertEqual(self.api.calls, [])

    def test_search_results_are_minimal_and_hide_business_identifiers(self):
        results = self.search()
        self.assertEqual([r["title"] for r in results], ["Alpha Bakery", "Beta Bakery"])
        first, second = results
        self.assertEqual(set(first), {"ref", "title", "category", "rating", "reviews", "address"})
        self.assertEqual((first["category"], first["rating"], first["reviews"]), ("Bakery", 4.5, 120))
        self.assertEqual((second["category"], second["rating"], second["reviews"], second["address"]), (None, None, None, None))
        self.assertNotEqual(first["ref"], second["ref"])
        self.assertNotIn(BUSINESS, json.dumps(results))

    def test_investigation_plan_input_is_validated(self):
        ref = self.search()[0]["ref"]
        for bad_ref in (None, "", "unknown", BUSINESS, 3):
            self.assert_error(404, self.state.plan_investigation, bad_ref, 2)
        for histories in (-1, 9, 2.0, "2", None, True):
            with self.subTest(histories=histories):
                self.assert_error(400, self.state.plan_investigation, ref, histories)

    def test_saved_page_makes_the_plan_exact(self):
        self.investigate(histories=2)  # saves the page and histories for reviewers 2 and 3
        sent = self.api.new_requests
        ref = self.search()[0]["ref"]
        plan = self.state.plan_investigation(ref, 3)  # reviewer 5's history is new
        self.assertEqual((plan["max_requests"], self.api.new_requests), (1, sent))
        self.assertIn("2 of 3 reviewer histories are already saved", plan["statement"])
        plan = self.state.plan_investigation(ref, 2)
        self.assertEqual(plan["max_requests"], 0)
        self.assertIn("No SerpApi request", plan["statement"])

    def test_completed_investigation_returns_an_aggregate_view_through_the_graph_foundation(self):
        status = self.investigate(histories=2)
        self.assertEqual((status["state"], status["business"]), ("complete", "Alpha Bakery"))
        self.assertEqual((status["max_requests"], status["requests_sent"], status["saved_responses_used"]), (3, 3, 0))
        self.assertEqual((status["histories_planned"], status["histories_collected"], status["stopped"]), (2, 2, None))
        view = status["view"]
        self.assertEqual((view["mode"], view["network"]["aggregated"], view["synthetic"]), ("investigation", True, False))
        # The business the user chose is named; the business found in histories is not.
        self.assertEqual([row["label"] for row in view["coverage"]["business_rows"]], ["Alpha Bakery"])
        self.assertEqual(
            [n["label"] for n in view["network"]["nodes"] if n["kind"] == "business"],
            ["Alpha Bakery", "1 business (history only)"],
        )
        # 6 reviewers; the seed, the shared business. Reviewers 2 and 6 share both.
        self.assertEqual((view["metrics"]["reviewers"], view["metrics"]["businesses"], view["metrics"]["pairs_sharing_two"]), (6, 2, 1))
        # Two real reviewers overlap: reported as evidence, never as a described group.
        indicators = view["indicators"]
        self.assertEqual((indicators["group_count"], indicators["groups"], indicators["groups_withheld"]), (1, [], 1))
        self.assertEqual((indicators["evidence"]["pairs"], indicators["evidence"]["reviewers_involved"]), (1, None))
        self.assertTrue(indicators["evidence"]["detail_withheld"])
        self.assertTrue(view["explanation"]["assessment"]["assessable"])
        self.assertIn("not a finding of coordination", view["explanation"]["assessment"]["statement"])
        text = json.dumps(status)
        for private in PRIVATE:
            self.assertNotIn(private, text)

    def test_entry_limit_is_validated_stated_and_counted_in_the_plan(self):
        ref = self.search()[0]["ref"]
        for bad in (9, 201, 0, -1, "50", 50.0, True, [50]):
            with self.subTest(limit=bad):
                self.assert_error(400, self.state.plan_investigation, ref, 2, bad)
        plan = self.state.plan_investigation(ref, 2)
        self.assertEqual(plan["history_limit"], DEFAULT_HISTORY_LIMIT)
        self.assertIn(f"up to {DEFAULT_HISTORY_LIMIT} entries; the API may return fewer", plan["statement"])
        self.assertNotIn("entries", self.state.plan_investigation(ref, 0)["statement"])
        info = self.state.session_info()
        self.assertEqual(
            (info["min_history_limit"], info["default_history_limit"], info["max_history_limit"]), (10, 50, 200)
        )

    def test_a_history_saved_at_one_entry_limit_is_not_reused_at_another(self):
        self.investigate(histories=2)  # default limit
        sent = self.api.new_requests
        ref = self.search()[0]["ref"]
        self.assertEqual(self.state.plan_investigation(ref, 2)["max_requests"], 0)
        other = self.state.plan_investigation(ref, 2, 10)
        self.assertEqual(other["max_requests"], 2)  # same reviewers, different request: two new requests
        self.assertIn("0 of 2 reviewer histories are already saved", other["statement"])
        started = self.state.start_investigation(other["plan"])
        self.state._investigations[started["investigation"]].thread.join(timeout=10)
        status = self.state.investigation_status(started["investigation"])
        self.assertEqual((self.api.new_requests - sent, status["history_limit"], status["state"]), (2, 10, "complete"))
        # The earlier saved responses are still there and still reused at their own limit.
        self.assertEqual(self.state.plan_investigation(ref, 2)["max_requests"], 0)
        requested = [c for c in self.api.calls if c["engine"] == "google_maps_contributor_reviews"]
        self.assertEqual([c.get("num") for c in requested], [50, 50, None, None])

    def test_unexpected_error_during_collection_gives_a_truthful_partial_result(self):
        ref = self.search()[0]["ref"]
        plan = self.state.plan_investigation(ref, 3)
        original, calls = self.state._search, {"n": 0}

        def flaky(params):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError(f"boom {reviewer(3)} {BUSINESS}")
            return original(params)

        self.state._search = flaky
        started = self.state.start_investigation(plan["plan"])
        self.state._investigations[started["investigation"]].thread.join(timeout=10)
        status = self.state.investigation_status(started["investigation"])
        self.assertEqual((status["state"], status["histories_collected"], status["responses_collected"]), ("partial", 1, 2))
        self.assertEqual(status["stopped"], "Collection stopped because of an unexpected error.")
        self.assertEqual(status["view"]["coverage"]["reviewers_with_history"], 1)
        self.assertFalse(self.state._running)
        for private in PRIVATE + ["boom", "Traceback", "RuntimeError"]:
            self.assertNotIn(private, json.dumps(status))

    def test_view_construction_failure_is_reported_without_claiming_a_view(self):
        ref = self.search()[0]["ref"]
        plan = self.state.plan_investigation(ref, 2)
        with mock.patch.object(live, "build_view", side_effect=KeyError(f"{BUSINESS} {reviewer(2)}")):
            started = self.state.start_investigation(plan["plan"])
            self.state._investigations[started["investigation"]].thread.join(timeout=10)
        status = self.state.investigation_status(started["investigation"])
        self.assertEqual((status["state"], status["responses_collected"], status["histories_collected"]), ("unviewable", 3, 2))
        self.assertNotIn("view", status)
        self.assertIsNone(status["stopped"])  # collection itself did not fail
        self.assertFalse(self.state._running)
        for private in PRIVATE + ["KeyError", "Traceback"]:
            self.assertNotIn(private, json.dumps(status))

    def test_too_little_data_says_coordination_cannot_be_assessed(self):
        for histories in (0, 1):
            with self.subTest(histories=histories):
                self.setUp()
                explanation = self.investigate(histories)["view"]["explanation"]
                assessment = explanation["assessment"]
                self.assertEqual(explanation["headline"], "Coordination cannot be assessed from this data.")
                self.assertFalse(assessment["assessable"])
                self.assertIn("cannot be assessed", assessment["statement"])

    def test_failure_midway_is_partial_and_keeps_collected_data(self):
        self.api.fail_new_request_number = 4  # search, page, first history succeed
        self.api.error = SerpApiError(f"SerpApi error (HTTP 429): limit for {reviewer(6)}")
        status = self.investigate(histories=3)
        self.assertEqual((status["state"], status["histories_collected"]), ("partial", 1))
        self.assertEqual(status["stopped"], "SerpApi rejected the request (HTTP 429).")
        self.assertEqual(status["view"]["coverage"]["reviewers_with_history"], 1)
        self.assertEqual(self.api.new_requests, 4)

    def test_failure_on_the_page_is_failed_with_no_view(self):
        self.api.fail_new_request_number = 2
        status = self.investigate(histories=3)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("view", status)
        self.assertIn("failed or timed out", status["stopped"])

    def test_missing_key_is_reported_without_any_request(self):
        self.api.key_configured = False
        self.assertIs(self.state.session_info()["key_configured"], False)
        plan = self.state.plan_search("bakery in testville")["plan"]
        with self.assertRaises(live.LiveError) as ctx:
            self.state.run_search(plan)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("No SerpApi key is configured", ctx.exception.message)
        self.assertEqual(self.api.new_requests, 0)

    def test_process_ceiling_refuses_further_new_requests(self):
        ref = self.search()[0]["ref"]
        plan = self.state.plan_investigation(ref, 3)
        with mock.patch.object(live, "PROCESS_REQUEST_CEILING", 2):  # the search used one; one remains
            started = self.state.start_investigation(plan["plan"])
            self.state._investigations[started["investigation"]].thread.join(timeout=10)
        status = self.state.investigation_status(started["investigation"])
        self.assertEqual((self.api.new_requests, status["state"], status["histories_collected"]), (2, "partial", 0))
        self.assertIn("safety limit", status["stopped"])

    def test_a_plan_that_could_exceed_the_remaining_ceiling_is_refused(self):
        ref = self.search()[0]["ref"]
        with mock.patch.object(live, "PROCESS_REQUEST_CEILING", 4):  # three requests remain
            self.assert_error(409, self.state.plan_investigation, ref, 3)        # 1 page + 3 histories
            self.assert_error(409, self.state.plan_investigation, ref, 0, None, 4)  # 4 pages
            self.assertEqual(self.state.plan_investigation(ref, 2)["max_requests"], 3)
        self.assertEqual(self.api.new_requests, 1)

    def test_only_one_investigation_runs_at_a_time(self):
        ref = self.search()[0]["ref"]
        first, second = (self.state.plan_investigation(ref, 1)["plan"] for _ in range(2))
        release = threading.Event()
        original = self.state._search
        self.state._search = lambda params: (release.wait(10), original(params))[1]
        started = self.state.start_investigation(first)
        try:
            self.assert_error(409, self.state.start_investigation, second)
            self.assertEqual(self.state.investigation_status(started["investigation"])["state"], "running")
            self.assertNotIn("view", self.state.investigation_status(started["investigation"]))
        finally:
            release.set()
            self.state._investigations[started["investigation"]].thread.join(timeout=10)
        self.assertEqual(self.state.investigation_status(started["investigation"])["state"], "complete")

    def test_investigations_and_states_are_isolated(self):
        first = self.investigate(histories=0)
        second = self.investigate(histories=2)
        self.assertEqual(first["view"]["coverage"]["reviewers_with_history"], 0)
        self.assertEqual(second["view"]["coverage"]["reviewers_with_history"], 2)
        self.assert_error(404, self.state.investigation_status, "unknown")
        other = new_state(self.api)
        ref = self.search()[0]["ref"]
        self.assert_error(404, other.plan_investigation, ref, 1)
        self.assertNotEqual(other.token, self.state.token)

    def test_stored_state_is_bounded(self):
        for n in range(live.MAX_STORED + 5):
            self.state.plan_search(f"bakery number {n}")
        self.assertEqual(len(self.state._plans), live.MAX_STORED)


class MultiPageCollectionTests(unittest.TestCase):
    """Bounded review pagination: only tokens the service returned, never more than asked or budgeted."""

    def setUp(self):
        self.api = FakeSerpApi()
        self.api.review_pages = FOUR_PAGES
        self.api.more_after_last = False

    def run_collect(self, pages, histories=0, max_requests=20):
        return collect(BUSINESS, histories, max_requests, self.api.search, self.api.is_cached, None, DEFAULT_HISTORY_LIMIT, pages)

    def page_calls(self):
        return [c for c in self.api.calls if c["engine"] == "google_maps_reviews"]

    def test_one_page_is_unchanged(self):
        result = collect(BUSINESS, 0, 1, self.api.search, self.api.is_cached)  # default page count
        self.assertEqual(self.api.calls, [{"engine": "google_maps_reviews", "data_id": BUSINESS, "hl": "en"}])
        self.assertEqual((result.pages_requested, result.pages_collected, result.more_pages, result.stopped), (1, 1, True, None))
        self.assertEqual(page_statement(result), "1 of 1 review page requested was collected. The service provided a token for a further page, which was not used.")

    def test_two_three_and_four_pages_follow_only_returned_tokens(self):
        for pages in (2, 3, 4):
            with self.subTest(pages=pages):
                self.setUp()
                result = self.run_collect(pages)
                self.assertEqual((result.pages_collected, result.requests_sent, result.stopped), (pages, pages, None))
                self.assertEqual(
                    [c.get("next_page_token") for c in self.page_calls()],
                    [None] + [f"PRIVATE-TOKEN-{n}" for n in range(2, pages + 1)],
                )
                for call in self.page_calls():
                    self.assertEqual(set(call) - {"next_page_token"}, {"engine", "data_id", "hl"})
                    self.assertEqual(call["data_id"], BUSINESS)
                self.assertEqual(len({o.reviewer_id for p in result.parsed for o in p.observations}), 3 * pages)
                self.assertEqual(result.more_pages, pages < 4)
        self.assertEqual(
            page_statement(result),
            "4 of 4 review pages requested were collected. The service did not provide another page token. "
            "This does not establish that all reviews were retrieved.",
        )

    def test_missing_next_page_token_ends_pagination_without_a_request(self):
        self.api.review_pages = FOUR_PAGES[:2]
        result = self.run_collect(4, histories=1)
        self.assertEqual((result.pages_requested, result.pages_collected, result.more_pages), (4, 2, False))
        self.assertEqual((len(self.page_calls()), result.histories_collected, result.stopped), (2, 1, None))
        self.assertEqual(
            page_statement(result),
            "2 of 4 review pages requested were collected. The service did not provide another page token, so "
            "collection stopped before the requested page limit. This does not establish that all reviews were retrieved.",
        )

    def test_an_offered_token_is_worded_as_a_token_not_as_a_retrieved_page(self):
        result = self.run_collect(2)
        statement = page_statement(result)
        self.assertEqual(statement, "2 of 2 review pages requested were collected. The service provided a token for a further page, which was not used.")
        for confusing in ("offered a further page", "further page was collected", "next page was retrieved"):
            self.assertNotIn(confusing, statement)
        self.api.token_overrides = {0: "bad token"}
        self.api.saved.clear()
        self.assertIn("The next-page token returned by the service", page_statement(self.run_collect(2)))

    def test_a_missing_token_is_never_reported_as_the_end_of_the_reviews(self):
        # Regression: an absent next-page token is an observation about the response,
        # not evidence that no further pages or reviews exist.
        for available, asked in ((2, 4), (2, 2), (1, 3), (4, 4)):
            with self.subTest(available=available, asked=asked):
                self.setUp()
                self.api.review_pages = FOUR_PAGES[:available]
                statement = page_statement(self.run_collect(asked))
                self.assertIn("did not provide another page token", statement)
                self.assertTrue(statement.endswith("This does not establish that all reviews were retrieved."))
                for overclaim in ("do not exist", "no further page", "no more reviews", "all reviews were collected", "every review"):
                    self.assertNotIn(overclaim, statement)
                self.assertEqual("stopped before the requested page limit" in statement, available < asked)

    def test_repeated_or_malformed_tokens_are_never_sent_back(self):
        cases = {
            "repeats the token just used": {1: "PRIVATE-TOKEN-2"},
            "contains spaces": {0: "bad token"},
            "contains a line break": {0: "bad\ntoken"},
            "is not text": {0: 12345},
            "is far too long": {0: "A" * 5000},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                self.setUp()
                self.api.token_overrides = overrides
                result = self.run_collect(4, histories=1)
                expected_pages = 2 if 1 in overrides else 1
                self.assertEqual((result.pages_collected, len(self.page_calls())), (expected_pages, expected_pages))
                self.assertEqual(result.stopped, None)  # not a failure: histories still run
                self.assertEqual(result.histories_collected, 1)
                sent = [c.get("next_page_token") for c in self.page_calls()]
                self.assertEqual(len(sent), len(set(sent)))
                if label == "is not text":
                    # A non-text marker is treated as no marker at all.
                    self.assertEqual((result.more_pages, result.pagination_note), (False, None))
                else:
                    self.assertEqual(result.more_pages, None)
                    self.assertIn("not usable", page_statement(result))
        # An empty marker is the same as none.
        self.setUp()
        self.api.token_overrides = {0: ""}
        self.assertEqual(self.run_collect(3).more_pages, False)

    def test_expired_token_or_failure_on_a_later_page_keeps_earlier_pages(self):
        for error, message in (
            (SerpApiError(f"SerpApi error (HTTP 400): token expired for {BUSINESS}"), "SerpApi rejected the request (HTTP 400)."),
            (SerpApiError("SerpApi request failed (ReadTimeout)"), "The request to SerpApi failed or timed out. It may still have used a credit."),
        ):
            with self.subTest(message=message):
                self.setUp()
                self.api.fail_new_request_number, self.api.error = 3, error
                result = self.run_collect(4, histories=2)
                self.assertEqual((result.pages_collected, self.api.new_requests, result.histories_collected), (2, 3, 0))
                self.assertEqual((result.stopped, len(result.parsed)), (message, 2))
                self.assertEqual(page_statement(result), "2 of 4 review pages requested were collected. Collection stopped before the remaining pages were requested.")

    def test_saved_pages_are_reused_individually(self):
        self.run_collect(2)
        first_run = self.api.new_requests
        result = self.run_collect(4, histories=0)
        self.assertEqual((first_run, result.requests_sent, result.cache_hits, result.pages_collected), (2, 2, 2, 4))
        again = self.run_collect(4)
        self.assertEqual((again.requests_sent, again.cache_hits), (0, 4))

    def test_budget_is_enforced_across_pages_and_histories(self):
        result = self.run_collect(4, histories=2, max_requests=3)
        self.assertEqual((self.api.new_requests, result.pages_collected, result.histories_collected), (3, 3, 0))
        self.assertIn("budget", result.stopped)
        self.setUp()
        result = self.run_collect(3, histories=2, max_requests=5)
        self.assertEqual((self.api.new_requests, result.pages_collected, result.histories_collected, result.stopped), (5, 3, 2, None))

    def test_page_count_is_clamped_to_the_supported_range(self):
        self.assertEqual(self.run_collect(9).pages_collected, 4)
        self.setUp()
        self.assertEqual(self.run_collect(0).pages_collected, 1)

    def test_reviewers_are_selected_from_all_collected_pages(self):
        result = self.run_collect(3, histories=5)
        requested = [c["contributor_id"] for c in self.api.calls if "contributor_id" in c]
        # Pages 1-3 hold reviewers 1-9. Fit the window, in page order: 2, 3, 5, 6, 7 (8 would be next).
        self.assertEqual(requested, [reviewer(n) for n in (2, 3, 5, 6, 7)])
        self.assertEqual(result.histories_planned, 5)
        one_page = FakeSerpApi()
        one_page.review_pages, one_page.more_after_last = FOUR_PAGES, False
        collect(BUSINESS, 5, 20, one_page.search, one_page.is_cached, None, DEFAULT_HISTORY_LIMIT, 1)
        self.assertEqual([c["contributor_id"] for c in one_page.calls if "contributor_id" in c], [reviewer(2), reviewer(3)])

    def test_duplicate_reviewers_and_reviews_across_pages_are_merged(self):
        self.api.review_pages = [[1, 2, 3], [3, 4, 2], [5]]
        result = self.run_collect(3, histories=4)
        graph = build_graph(result.parsed)
        metrics = compute_metrics(graph)
        page_edges = [e for (r, b), e in graph.edges.items() if b == BUSINESS]
        self.assertEqual(len(page_edges), 5)  # five distinct reviewers of the business, not seven
        self.assertEqual(sorted(len([o for o in e.observations if o.source.engine == "google_maps_reviews"]) for e in page_edges), [1, 1, 1, 2, 2])
        self.assertEqual(metrics.conflicts, {})
        self.assertEqual(metrics.business_degrees[5], 1)
        # A reviewer seen on two pages is asked for one history, once.
        requested = [c["contributor_id"] for c in self.api.calls if "contributor_id" in c]
        self.assertEqual(requested, [reviewer(n) for n in (2, 3, 5, 4)])
        self.assertEqual({o.source.file for e in page_edges for o in e.observations if o.source.engine == "google_maps_reviews"}, {"review-page-1", "review-page-2", "review-page-3"})

    def test_graph_and_coverage_reflect_every_collected_page(self):
        result = self.run_collect(3, histories=2)
        view = build_view(build_graph(result.parsed), INVESTIGATION, business_titles={BUSINESS: "Alpha Bakery"})
        row = view["coverage"]["business_rows"][0]
        self.assertEqual((row["label"], row["pages"], row["captured"], row["reported_total"], row["not_collected"]), ("Alpha Bakery", 3, 9, 120, 111))
        self.assertTrue(row["further_pages"])  # page 3 offered a fourth page that was not requested
        self.assertEqual((view["metrics"]["reviewers"], view["coverage"]["reviewers_with_history"]), (9, 2))
        self.assertEqual(view["metrics"]["pairs_sharing_two"], 1)
        self.assertEqual(view["coverage"]["history_windows"]["retrieved"], 2)
        all_pages = self.run_collect(4)
        row = build_view(build_graph(all_pages.parsed), INVESTIGATION)["coverage"]["business_rows"][0]
        self.assertEqual((row["pages"], row["captured"], row["further_pages"]), (4, 12, False))

    def test_unexpected_error_on_a_later_page_keeps_earlier_pages(self):
        original, calls = self.api.search, {"n": 0}

        def flaky(params):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError(f"boom {BUSINESS}")
            return original(params)

        result = collect(BUSINESS, 2, 10, flaky, self.api.is_cached, None, DEFAULT_HISTORY_LIMIT, 3)
        self.assertEqual((result.pages_collected, len(result.parsed), result.histories_collected), (1, 1, 0))
        self.assertEqual(result.stopped, "Collection stopped because of an unexpected error.")

    def test_response_for_the_wrong_page_is_rejected(self):
        original = self.api.respond

        def wrong_page(params):
            response = original(params)
            if "next_page_token" in params:
                response["search_parameters"]["next_page_token"] = "PRIVATE-TOKEN-9"
            return response

        self.api.respond = wrong_page
        result = self.run_collect(3)
        self.assertEqual(result.pages_collected, 1)
        self.assertIn("could not be used", result.stopped)

    def test_token_validation_and_request_shape(self):
        self.assertEqual(review_page_params(BUSINESS), {"engine": "google_maps_reviews", "data_id": BUSINESS, "hl": "en"})
        self.assertEqual(
            review_page_params(BUSINESS, "abc"),
            {"engine": "google_maps_reviews", "data_id": BUSINESS, "hl": "en", "next_page_token": "abc"},
        )
        self.assertNotEqual(cache_key(review_page_params(BUSINESS)), cache_key(review_page_params(BUSINESS, "abc")))
        for good in ("CAESBkVnSUlDZw==", "abc-DEF_123", "a.b~c:d/e+f%3D"):
            self.assertTrue(usable_page_token(good))
        for bad in ("", " ", "a b", "a\nb", "a&next=1", "a?b", "a#b", None, 5, ["a"], "x" * 2049):
            self.assertFalse(usable_page_token(bad), bad)


class MultiPagePlanningTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSerpApi()
        self.api.review_pages, self.api.more_after_last = FOUR_PAGES, False
        self.state = new_state(self.api)
        self.ref = self.state.run_search(self.state.plan_search("bakery in testville")["plan"])["businesses"][0]["ref"]

    def run_plan(self, plan):
        started = self.state.start_investigation(plan["plan"])
        self.state._investigations[started["investigation"]].thread.join(timeout=10)
        return self.state.investigation_status(started["investigation"])

    def test_page_count_is_validated_and_defaults_to_one(self):
        for bad in (0, 5, -1, "2", 2.0, True, [2]):
            with self.subTest(pages=bad):
                with self.assertRaises(live.LiveError) as ctx:
                    self.state.plan_investigation(self.ref, 1, None, bad)
                self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.state.plan_investigation(self.ref, 1)["pages"], 1)
        info = self.state.session_info()
        self.assertEqual((info["default_review_pages"], info["max_review_pages"]), (1, 4))
        self.assertEqual(self.api.new_requests, 1)  # only the search

    def test_budget_examples_for_one_to_four_pages(self):
        for pages, histories, expected in ((1, 3, 4), (2, 3, 5), (3, 3, 6), (4, 3, 7), (4, 8, 12), (4, 0, 4)):
            with self.subTest(pages=pages, histories=histories):
                plan = self.state.plan_investigation(self.ref, histories, None, pages)
                self.assertEqual((plan["max_requests"], plan["pages"]), (expected, pages))
                self.assertIn(f"At most {expected} SerpApi requests", plan["statement"])
                if pages > 1:
                    self.assertIn(f"up to {pages} for review pages", plan["statement"])
                    self.assertIn("only if the previous response provides a next-page token, so fewer pages may be retrieved than asked for", plan["statement"])
        self.assertEqual(self.api.new_requests, 1)

    def test_plan_grammar_for_one_and_several_saved_pages(self):
        self.run_plan(self.state.plan_investigation(self.ref, 0, None, 1))  # saves the first page only
        one = self.state.plan_investigation(self.ref, 0, None, 2)["statement"]
        self.assertIn("up to 1 for review pages (the first of 2 is already saved)", one)
        self.assertNotIn("the first 1 of 2 are", one)
        self.run_plan(self.state.plan_investigation(self.ref, 0, None, 2))  # saves the second
        several = self.state.plan_investigation(self.ref, 0, None, 4)["statement"]
        self.assertIn("(the first 2 of 4 are already saved)", several)

    def test_one_page_plan_wording_is_unchanged(self):
        statement = self.state.plan_investigation(self.ref, 2)["statement"]
        self.assertTrue(statement.startswith("At most 3 SerpApi requests (3 credits): 1 for the first page of reviews and up to 2 for reviewer histories."))

    def test_execution_never_exceeds_the_planned_maximum(self):
        status = self.run_plan(self.state.plan_investigation(self.ref, 2, None, 3))
        self.assertEqual((status["state"], status["max_requests"], status["requests_sent"]), ("complete", 5, 5))
        self.assertEqual((status["pages_requested"], status["pages_collected"], status["more_pages"]), (3, 3, True))
        self.assertEqual(status["page_statement"], "3 of 3 review pages requested were collected. The service provided a token for a further page, which was not used.")
        self.assertEqual(status["view"]["coverage"]["page_statement"], status["page_statement"])
        self.assertEqual(status["view"]["metrics"]["reviewers"], 9)

    def test_fewer_pages_exist_than_requested(self):
        self.api.review_pages = FOUR_PAGES[:2]
        plan = self.state.plan_investigation(self.ref, 1, None, 4)
        self.assertEqual(plan["max_requests"], 5)
        status = self.run_plan(plan)
        self.assertEqual((status["state"], status["requests_sent"], status["pages_collected"], status["more_pages"]), ("complete", 3, 2, False))
        self.assertIn("collection stopped before the requested page limit", status["page_statement"])
        # Now everything is saved, the plan knows only two pages can be collected.
        again = self.state.plan_investigation(self.ref, 1, None, 4)
        self.assertEqual(again["max_requests"], 0)
        self.assertIn("Only 2 of the 4 requested review pages can be collected", again["statement"])

    def test_saved_pages_make_the_plan_exact_or_narrow_it(self):
        self.run_plan(self.state.plan_investigation(self.ref, 0, None, 2))  # saves pages 1 and 2
        sent = self.api.new_requests
        open_plan = self.state.plan_investigation(self.ref, 2, None, 4)
        self.assertEqual(open_plan["max_requests"], 4)  # 2 further pages + 2 histories
        self.assertIn("up to 2 for review pages (the first 2 of 4 are already saved)", open_plan["statement"])
        exact = self.state.plan_investigation(self.ref, 2, None, 2)
        self.assertEqual(exact["max_requests"], 2)
        self.assertIn("The 2 review pages and 0 of 2 reviewer histories are already saved", exact["statement"])
        self.assertEqual(self.api.new_requests, sent)  # planning sent nothing
        status = self.run_plan(exact)
        self.assertEqual((status["requests_sent"], status["saved_responses_used"]), (2, 2))
        self.assertEqual(self.state.plan_investigation(self.ref, 2, None, 2)["max_requests"], 0)

    def test_failure_on_a_later_page_gives_a_truthful_partial_result(self):
        plan = self.state.plan_investigation(self.ref, 2, None, 3)
        self.api.fail_new_request_number = self.api.new_requests + 2
        self.api.error = SerpApiError(f"SerpApi error (HTTP 400): expired token PRIVATE-TOKEN-2 for {BUSINESS}")
        status = self.run_plan(plan)
        self.assertEqual((status["state"], status["pages_collected"], status["histories_collected"]), ("partial", 1, 0))
        self.assertEqual(status["stopped"], "SerpApi rejected the request (HTTP 400).")
        self.assertIn("Collection stopped before the remaining pages were requested", status["page_statement"])
        self.assertEqual(status["view"]["coverage"]["business_rows"][0]["pages"], 1)
        for private in PRIVATE + ["expired token"]:
            self.assertNotIn(private, json.dumps(status))

    def test_status_and_view_never_contain_tokens_or_identifiers(self):
        status = self.run_plan(self.state.plan_investigation(self.ref, 3, None, 4))
        self.assertEqual((status["pages_collected"], status["more_pages"]), (4, False))
        text = json.dumps(status)
        for private in PRIVATE + ["next_page_token", "review-page-"]:
            self.assertNotIn(private, text)


class ExpansionCandidateTests(unittest.TestCase):
    """Which businesses qualify for expansion, and the bounded collection itself."""

    def setUp(self):
        self.api = FakeSerpApi()
        self.api.use_overlap_fixture()

    def graph(self, histories):
        return build_graph(collect(BUSINESS, histories, 20, self.api.search, self.api.is_cached).parsed)

    def test_candidates_are_history_businesses_within_a_repeated_overlap(self):
        # Histories for reviewers 2 and 3: they share the investigated business and three others.
        self.assertEqual(expansion_candidates(self.graph(2)), [SHARED_BUSINESS, EXTRA_X, EXTRA_Y])
        self.assertEqual(expansion_candidates(self.graph(2)), expansion_candidates(self.graph(2)))

    def test_a_pair_sharing_exactly_two_businesses_yields_one_candidate(self):
        plain = FakeSerpApi()  # reviewers 2 and 3 share the investigated business and one other
        graph = build_graph(collect(BUSINESS, 2, 20, plain.search, plain.is_cached).parsed)
        self.assertEqual(expansion_candidates(graph), [SHARED_BUSINESS])
        # With reviewer 5's history retrieved too, the pair 2 and 5 shares exactly two as well.
        self.assertEqual(expansion_candidates(self.graph(3)), [SHARED_BUSINESS, EXTRA_X, EXTRA_Y, POPULAR])

    def test_a_shared_business_without_an_overlapping_history_pair_is_not_eligible(self):
        api = FakeSerpApi()
        api.shared_history = False
        api.history_extra = {2: [POPULAR], 5: [POPULAR]}
        # Reviewer 5 lists it too, but only reviewers 2 and 3 have a retrieved history.
        two = collect(BUSINESS, 2, 20, api.search, api.is_cached).parsed
        self.assertEqual(expansion_candidates(build_graph(two)), [])
        # Listed in one retrieved history only.
        api.history_extra = {2: [POPULAR]}
        self.assertEqual(expansion_candidates(build_graph(collect(BUSINESS, 3, 20, api.search, api.is_cached).parsed)), [])

    def test_the_investigated_business_counts_only_where_it_is_observed(self):
        api = FakeSerpApi()
        api.shared_history, api.history_lists_seed = False, False
        api.history_extra = {2: [POPULAR], 5: [POPULAR]}
        parsed = collect(BUSINESS, 3, 20, api.search, api.is_cached).parsed
        histories = [p for p in parsed if p.history is not None]
        self.assertEqual(len(histories), 3)
        # Histories alone: the two reviewers share one observed business. The investigated
        # business is not assumed for them, so they are not an overlapping pair.
        self.assertEqual(expansion_candidates(build_graph(histories)), [])
        # With the review page that shows both of them, the pair shares two observed businesses.
        self.assertEqual(expansion_candidates(build_graph(parsed)), [POPULAR])

    def test_no_candidates_without_two_histories_or_any_shared_history_business(self):
        self.assertEqual(expansion_candidates(self.graph(0)), [])
        self.assertEqual(expansion_candidates(self.graph(1)), [])
        apart = FakeSerpApi()
        apart.shared_history = False  # every history lists a different second business
        self.assertEqual(expansion_candidates(build_graph(collect(BUSINESS, 3, 20, apart.search, apart.is_cached).parsed)), [])

    def test_a_business_with_a_collected_page_is_never_a_candidate(self):
        graph = self.graph(2)
        self.assertNotIn(BUSINESS, expansion_candidates(graph))
        pages = expand([SHARED_BUSINESS], 1, self.api.search, self.api.is_cached).parsed
        graph = build_graph(collect(BUSINESS, 2, 20, self.api.search, self.api.is_cached).parsed + pages)
        self.assertEqual(expansion_candidates(graph), [EXTRA_X, EXTRA_Y])

    def test_expand_collects_first_pages_only_within_budget(self):
        before = len(self.api.calls)
        result = expand([EXTRA_X, EXTRA_Y, SHARED_BUSINESS, EXTRA_X], 5, self.api.search, self.api.is_cached)
        calls = self.api.calls[before:]
        # At most two businesses, one first page each, no histories, no tokens.
        self.assertEqual(calls, [review_page_params(EXTRA_X), review_page_params(EXTRA_Y)])
        self.assertEqual((result.pages_requested, result.pages_collected, result.requests_sent, result.stopped), (2, 2, 2, None))
        self.assertEqual([p.source_file for p in result.parsed], ["expansion-page-1", "expansion-page-2"])
        limited = expand([SHARED_BUSINESS, POPULAR], 1, self.api.search, self.api.is_cached)
        self.assertEqual((limited.pages_collected, limited.requests_sent), (1, 1))
        self.assertIn("budget", limited.stopped)

    def test_expand_keeps_the_first_page_when_the_second_fails_and_never_raises(self):
        self.api.fail_new_request_number = self.api.new_requests + 2
        result = expand([EXTRA_X, EXTRA_Y], 2, self.api.search, self.api.is_cached)
        self.assertEqual((result.pages_collected, len(result.parsed)), (1, 1))
        self.assertIn("failed or timed out", result.stopped)
        broken = expand([EXTRA_X], 1, lambda params: (_ for _ in ()).throw(RuntimeError(EXTRA_X)), lambda params: False)
        self.assertEqual((broken.parsed, broken.stopped), ([], "Collection stopped because of an unexpected error."))

    def test_expand_rejects_a_response_for_another_business(self):
        original = self.api.respond
        self.api.respond = lambda params: original({**params, "data_id": EXTRA_Y})
        result = expand([EXTRA_X], 1, self.api.search, self.api.is_cached)
        self.assertEqual(result.parsed, [])
        self.assertIn("could not be used", result.stopped)


class ExpansionWorkflowTests(unittest.TestCase):
    """Expansion as a separate, confirmed, bounded second stage."""

    def setUp(self):
        self.api = FakeSerpApi()
        self.api.use_overlap_fixture()
        self.state = new_state(self.api)

    def investigate(self, histories=2, business=0):
        ref = self.state.run_search(self.state.plan_search("bakery in testville")["plan"])["businesses"][business]["ref"]
        started = self.state.start_investigation(self.state.plan_investigation(ref, histories)["plan"])
        self.wait(started["investigation"])
        return started["investigation"]

    def wait(self, investigation_id):
        self.state._investigations[investigation_id].thread.join(timeout=10)

    def run_expansion(self, investigation_id, businesses):
        plan = self.state.plan_expansion(investigation_id, businesses)
        self.state.start_expansion(plan["plan"], investigation_id)
        self.wait(investigation_id)
        return plan, self.state.investigation_status(investigation_id)

    def assert_error(self, status, call, *args):
        with self.assertRaises(live.LiveError) as ctx:
            call(*args)
        self.assertEqual(ctx.exception.status, status)
        for private in PRIVATE:
            self.assertNotIn(private, ctx.exception.message)

    def expansion_calls(self, since):
        return self.api.calls[since:]

    def test_expansion_never_runs_as_part_of_the_investigation(self):
        investigation = self.investigate()
        self.assertEqual({c.get("data_id") for c in self.api.calls if c["engine"] == "google_maps_reviews"}, {BUSINESS})
        status = self.state.investigation_status(investigation)
        expansion = status["expansion"]
        self.assertEqual((expansion["available"], expansion["performed"], expansion["last"], expansion["findings"]), (True, 0, None, []))
        self.assertEqual(expansion["max_businesses"], 2)
        self.assertNotIn("eligible", expansion)
        self.assertIn("or say how many qualify", expansion["statement"])
        self.assertIn("A shared business is not evidence that reviews were arranged.", expansion["statement"])
        self.assertIn("The server chooses which by a fixed rule and does not list them", expansion["statement"])
        self.assertEqual(status["activity"], "collecting")

    def test_no_eligible_candidates_is_explained_as_a_valid_outcome(self):
        self.api.history_extra, self.api.shared_history = {}, False
        investigation = self.investigate()
        sent = self.api.new_requests
        expansion = self.state.investigation_status(investigation)["expansion"]
        self.assertEqual((expansion["available"], expansion["max_businesses"]), (False, 0))
        self.assertIn("No business qualifies", expansion["statement"])
        self.assertIn("both reviewers in a pair that shares at least 2 businesses", expansion["statement"])
        self.assertIn("is not a finding that no overlap exists", expansion["statement"])
        self.assert_error(409, self.state.plan_expansion, investigation, 1)
        none = self.state.investigation_status(self.investigate(histories=0, business=1))["expansion"]
        self.assertIn("needs at least two reviewers with a retrieved history, and none was retrieved", none["statement"])
        self.assertEqual(self.api.new_requests, sent + 1)  # only the second investigation's review page

    def test_plan_is_validated_and_states_the_exact_cost(self):
        investigation = self.investigate()
        sent = self.api.new_requests
        for bad in (0, 3, -1, "1", 1.0, True, None, [1]):
            with self.subTest(businesses=bad):
                self.assert_error(400, self.state.plan_expansion, investigation, bad)
        for missing in ("unknown", None, 5):
            self.assert_error(404, self.state.plan_expansion, missing, 1)
        one = self.state.plan_expansion(investigation, 1)
        self.assertEqual((one["businesses"], one["max_requests"]), (1, 1))
        self.assertTrue(one["statement"].startswith("At most 1 SerpApi request (1 credit)."))
        two = self.state.plan_expansion(investigation, 2)
        self.assertEqual((two["businesses"], two["max_requests"]), (2, 2))
        self.assertIn("At most 2 SerpApi requests (2 credits).", two["statement"])
        self.assertIn("requests no reviewer histories and no further pages", two["statement"])
        self.assertEqual(set(two), {"plan", "businesses", "max_requests", "statement"})
        self.assertEqual(self.api.new_requests, sent)  # planning sends nothing

    def test_expanding_two_businesses_merges_them_and_reassesses_the_evidence(self):
        investigation = self.investigate()
        before_view = self.state.investigation_status(investigation)["view"]
        calls_before, sent = len(self.api.calls), self.api.new_requests
        plan, status = self.run_expansion(investigation, 2)

        # Exactly the two planned first pages; no histories, tokens, or other businesses.
        self.assertEqual(self.expansion_calls(calls_before), [review_page_params(SHARED_BUSINESS), review_page_params(EXTRA_X)])
        self.assertEqual(self.api.new_requests - sent, plan["max_requests"])
        self.assertEqual((status["state"], status["activity"]), ("complete", "expanding"))
        expansion = status["expansion"]
        self.assertEqual(expansion["last"], {
            "state": "complete", "businesses_requested": 2, "businesses_collected": 2,
            "requests_sent": 2, "saved_responses_used": 0, "stopped": None,
        })
        self.assertEqual(expansion["performed"], 2)
        self.assertEqual(expansion["findings"], [
            "2 shared businesses were expanded, each by its first review page: 13 reviews collected.",
            "Reviewers in the investigation: 6 before expansion, 14 after.",
            "Fewer than 5 of the reviewers already in this investigation appear on the newly collected pages.",
            "Reviewer pairs sharing 2 or more businesses: 1 before expansion, 3 after.",
        ])
        limits = " ".join(expansion["limits"])
        for needed in ("does not show that reviews were arranged", "Only the first review page", "was not assessed", "does not make those reviewers anonymous", "because more was collected"):
            self.assertIn(needed, limits)

        view = status["view"]
        self.assertEqual((before_view["metrics"]["reviewers"], before_view["metrics"]["pairs_sharing_two"]), (6, 1))
        self.assertEqual((view["metrics"]["reviewers"], view["metrics"]["pairs_sharing_two"]), (14, 3))
        # Reviewer-business links are not duplicated: 13 before, plus 9 links not seen before on the two pages.
        self.assertEqual((before_view["metrics"]["edges"], view["metrics"]["edges"]), (13, 22))
        rows = view["coverage"]["business_rows"]
        self.assertEqual([r["label"] for r in rows], ["Alpha Bakery", "Expanded business 1", "Expanded business 2"])
        self.assertEqual([(r["expanded"], r["reported_total"], r["not_collected"]) for r in rows], [(False, 120, 114), (True, None, None), (True, None, None)])
        # Nothing claims an expanded business is fully covered, or implies cause or wrongdoing.
        text = json.dumps([status["expansion"], rows, view["ratings"]]).lower()
        for claim in ("all reviews", "fully collected", "100%", "fraud", "fake", "suspicious", "caused", "ring member"):
            self.assertNotIn(claim, text)
        self.assertEqual(sorted(r["captured"] for r in rows[1:]), [5, 8])
        self.assertEqual([(b["expanded"], b["reported_rating"], b["reported_total"]) for b in view["ratings"]["businesses"]], [(False, 4.5, 120), (True, None, None), (True, None, None)])
        # History-only businesses left: the third shared one and reviewer 2's single-shared one.
        self.assertEqual(view["coverage"]["history_only_businesses"], 2)
        # Still a small set of reviewers: nothing about the group is described.
        self.assertEqual((view["indicators"]["groups"], view["indicators"]["evidence"]["reviewers_involved"]), ([], None))

    def test_expansion_deduplicates_links_and_keeps_provenance(self):
        investigation = self.investigate()
        self.run_expansion(investigation, 2)
        record = self.state._investigations[investigation]
        graph = build_graph(record.collection.parsed + record.expansion_pages)
        edge = graph.edges[(reviewer(2), SHARED_BUSINESS)]
        self.assertEqual(sorted(o.source.file for o in edge.observations), ["expansion-page-1", "history-1"])
        self.assertEqual((edge.review_ids, edge.conflicts), ({"other-2"}, ("rating",)))  # 4 in the history, 5 on the page
        self.assertEqual(len([pair for pair in graph.edges if pair[1] == EXTRA_X]), 8)
        self.assertEqual(sorted(graph.pages), sorted([BUSINESS, SHARED_BUSINESS, EXTRA_X]))
        self.assertEqual(record.expanded_ids, [SHARED_BUSINESS, EXTRA_X])

    def test_browser_never_learns_which_businesses_or_reviewers_are_involved(self):
        investigation = self.investigate()
        plan = self.state.plan_expansion(investigation, 2)
        self.state.start_expansion(plan["plan"], investigation)
        self.wait(investigation)
        text = json.dumps(self.state.investigation_status(investigation)) + json.dumps({k: v for k, v in plan.items() if k != "plan"})
        for private in PRIVATE + [EXTRA_X, EXTRA_Y, POPULAR, "candidates", "expanded_ids", "expansion-page", "Cafe", "Gym"]:
            self.assertNotIn(private, text)

    def test_a_pair_sharing_exactly_two_businesses_can_be_expanded_once(self):
        api = FakeSerpApi()  # reviewers 2 and 3 share the investigated business and one other
        api.business_pages = {SHARED_BUSINESS: [2, 3, 30]}
        self.api, self.state = api, new_state(api)
        investigation = self.investigate()
        expansion = self.state.investigation_status(investigation)["expansion"]
        # Availability and the fixed maximum only: not that a single business qualifies.
        self.assertEqual((expansion["available"], expansion["max_businesses"]), (True, 2))
        sent = api.new_requests
        plan = self.state.plan_expansion(investigation, 2)
        self.assertEqual((plan["businesses"], plan["max_requests"]), (2, 1))
        self.assertIn("up to 2 shared businesses", plan["statement"])
        self.assertIn("Fewer are collected if fewer qualify", plan["statement"])
        self.state.start_expansion(plan["plan"], investigation)
        self.wait(investigation)
        status = self.state.investigation_status(investigation)
        self.assertEqual((api.new_requests - sent, status["expansion"]["last"]["businesses_collected"]), (1, 1))
        self.assertEqual(api.calls[-1], review_page_params(SHARED_BUSINESS))
        self.assertEqual((status["expansion"]["available"], status["expansion"]["statement"]), (False, "No further business qualifies for expansion."))
        # The shared business is now a collected page, no longer a history-only observation.
        self.assertEqual(status["view"]["coverage"]["history_only_businesses"], 0)
        self.assertEqual([r["label"] for r in status["view"]["coverage"]["business_rows"]], ["Alpha Bakery", "Expanded business 1"])

    def test_one_business_then_the_rest_and_saved_pages_cost_nothing(self):
        investigation = self.investigate()
        sent = self.api.new_requests
        _, status = self.run_expansion(investigation, 1)
        self.assertEqual((self.api.new_requests - sent, status["expansion"]["performed"]), (1, 1))
        self.assertEqual((status["expansion"]["available"], status["expansion"]["max_businesses"]), (True, 2))
        self.assertIn("1 shared business was expanded", status["expansion"]["findings"][0])
        self.api.saved.add(key(review_page_params(EXTRA_X)))  # one of the two remaining pages is already saved
        plan = self.state.plan_expansion(investigation, 2)
        self.assertEqual(plan["max_requests"], 1)
        self.assertTrue(plan["statement"].startswith("At most 1 SerpApi request (1 credit). Expansion collects the first review page of up to 2 shared businesses"))
        self.assertIn("a page already saved on this computer costs nothing", plan["statement"])
        self.state.start_expansion(plan["plan"], investigation)
        self.wait(investigation)
        status = self.state.investigation_status(investigation)
        self.assertEqual((self.api.new_requests - sent, status["expansion"]["last"]["saved_responses_used"]), (2, 1))
        # Nothing further qualifies, and nothing recursive happened.
        self.assertEqual((status["expansion"]["available"], status["expansion"]["statement"]), (False, "No further business qualifies for expansion."))
        self.assert_error(409, self.state.plan_expansion, investigation, 1)
        self.assertEqual(status["expansion"]["performed"], 3)

    def test_expansion_is_one_hop_and_requests_nothing_else(self):
        investigation = self.investigate()
        calls_before = len(self.api.calls)
        self.run_expansion(investigation, 2)
        self.run_expansion(investigation, 1)
        calls = self.expansion_calls(calls_before)
        self.assertEqual([c["engine"] for c in calls], ["google_maps_reviews"] * 3)
        self.assertTrue(all("next_page_token" not in c and "contributor_id" not in c for c in calls))
        self.assertEqual({c["data_id"] for c in calls}, {SHARED_BUSINESS, EXTRA_X, EXTRA_Y})
        # Reviewers first seen during expansion have no history, so they create no new candidate.
        self.assertEqual(self.state._candidates(self.state._investigations[investigation]), [])
        self.assertNotIn(POPULAR, {c.get("data_id") for c in self.api.calls})

    def test_plans_are_single_use_and_cannot_be_forged_or_moved(self):
        first = self.investigate()
        second = self.investigate(histories=2, business=1)
        sent = self.api.new_requests
        for bad in (None, "", "forged", 7):
            self.assert_error(409, self.state.start_expansion, bad, first)
        search_plan = self.state.plan_search("another bakery")["plan"]
        self.assert_error(409, self.state.start_expansion, search_plan, first)  # wrong kind
        plan = self.state.plan_expansion(first, 1)["plan"]
        self.assert_error(409, self.state.start_expansion, plan, second)  # another investigation
        self.assert_error(409, self.state.start_expansion, plan, first)  # consumed by the refused attempt
        plan = self.state.plan_expansion(first, 1)["plan"]
        with mock.patch.object(live.time, "monotonic", return_value=live.time.monotonic() + live.PLAN_LIFETIME_SECONDS + 1):
            self.assert_error(409, self.state.start_expansion, plan, first)
        expansion_plan = self.state.plan_expansion(first, 1)["plan"]
        self.assert_error(409, self.state.start_investigation, expansion_plan)  # not an investigation plan
        self.assertEqual(self.api.new_requests, sent)
        plan = self.state.plan_expansion(first, 1)["plan"]
        self.state.start_expansion(plan, first)
        self.wait(first)
        self.assert_error(409, self.state.start_expansion, plan, first)  # reuse
        self.assertEqual(self.api.new_requests, sent + 1)

    def test_a_plan_made_before_another_expansion_is_stale(self):
        investigation = self.investigate()
        stale = self.state.plan_expansion(investigation, 2)["plan"]
        self.run_expansion(investigation, 2)
        sent = self.api.new_requests
        self.assert_error(409, self.state.start_expansion, stale, investigation)
        self.assertEqual(self.api.new_requests, sent)

    def test_failed_second_request_keeps_the_first_page_and_the_investigation(self):
        investigation = self.investigate()
        plan = self.state.plan_expansion(investigation, 2)
        self.api.fail_new_request_number = self.api.new_requests + 2
        self.api.error = SerpApiError(f"SerpApi error (HTTP 429): limit reached for {EXTRA_X}")
        self.state.start_expansion(plan["plan"], investigation)
        self.wait(investigation)
        status = self.state.investigation_status(investigation)
        self.assertEqual(status["state"], "complete")  # the investigation itself is unaffected
        self.assertEqual(status["expansion"]["last"], {
            "state": "partial", "businesses_requested": 2, "businesses_collected": 1,
            "requests_sent": 2, "saved_responses_used": 0, "stopped": "SerpApi rejected the request (HTTP 429).",
        })
        self.assertEqual([r["label"] for r in status["view"]["coverage"]["business_rows"]], ["Alpha Bakery", "Expanded business 1"])
        self.assertIn("1 shared business was expanded", status["expansion"]["findings"][0])
        self.assertTrue(status["expansion"]["available"])  # the rest can be planned again, with a new confirmation
        for private in PRIVATE:
            self.assertNotIn(private, json.dumps(status))

    def test_failed_first_request_leaves_the_investigation_exactly_as_it_was(self):
        investigation = self.investigate()
        before = self.state.investigation_status(investigation)
        plan = self.state.plan_expansion(investigation, 2)
        self.api.fail_new_request_number = self.api.new_requests + 1
        self.state.start_expansion(plan["plan"], investigation)
        self.wait(investigation)
        after = self.state.investigation_status(investigation)
        self.assertEqual(after["view"], before["view"])
        self.assertEqual((after["state"], after["expansion"]["performed"], after["expansion"]["findings"]), ("complete", 0, []))
        self.assertEqual((after["expansion"]["last"]["state"], after["expansion"]["last"]["businesses_collected"]), ("failed", 0))
        self.assertEqual(self.api.new_requests, plan["max_requests"] - 1 + before["requests_sent"] + 1)  # one attempt, no retry
        self.assertTrue(after["expansion"]["available"])
        self.assertFalse(self.state._running)

    def test_an_expansion_that_cannot_be_displayed_is_not_added(self):
        investigation = self.investigate()
        before = self.state.investigation_status(investigation)["view"]
        plan = self.state.plan_expansion(investigation, 1)
        with mock.patch.object(live, "build_view", side_effect=KeyError(EXTRA_X)):
            self.state.start_expansion(plan["plan"], investigation)
            self.wait(investigation)
        status = self.state.investigation_status(investigation)
        self.assertEqual((status["state"], status["view"]), ("complete", before))
        self.assertEqual((status["expansion"]["performed"], status["expansion"]["last"]["state"]), (0, "failed"))
        self.assertEqual(status["expansion"]["last"]["stopped"], "The expansion result could not be displayed, so it was not added.")
        self.assertEqual(self.state._investigations[investigation].expansion_pages, [])

    def test_expansion_respects_the_running_lock_and_the_server_ceiling(self):
        investigation = self.investigate()
        plan = self.state.plan_expansion(investigation, 1)
        self.state._running = True
        self.assert_error(409, self.state.plan_expansion, investigation, 1)
        self.assert_error(409, self.state.start_expansion, plan["plan"], investigation)
        self.state._running = False
        with mock.patch.object(live, "PROCESS_REQUEST_CEILING", self.state._requests_attempted + 1):
            self.assert_error(409, self.state.plan_expansion, investigation, 2)
            self.assertEqual(self.state.plan_expansion(investigation, 1)["max_requests"], 1)

    def test_a_failed_investigation_cannot_be_expanded(self):
        ref = self.state.run_search(self.state.plan_search("bakery in testville")["plan"])["businesses"][0]["ref"]
        plan = self.state.plan_investigation(ref, 2)
        self.api.fail_new_request_number = self.api.new_requests + 1
        started = self.state.start_investigation(plan["plan"])
        self.wait(started["investigation"])
        status = self.state.investigation_status(started["investigation"])
        self.assertEqual((status["state"], status["expansion"]["available"]), ("failed", False))
        self.assert_error(409, self.state.plan_expansion, started["investigation"], 1)


class LiveHttpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = FakeSerpApi()
        self.state = new_state(self.api)
        self.server = app.create_server(port=0, raw_dir=Path(self._tmp.name) / "raw", live=self.state)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def call(self, method: str, path: str, body: object = None, headers: dict | None = None, token: bool = True):
        send = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            send[app.TOKEN_HEADER] = self.state.token
        send.update(headers or {})
        data = json.dumps(body).encode("utf-8") if body is not None and not isinstance(body, bytes) else body
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=send, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read().decode("utf-8")

    def post(self, path: str, body: object, **kwargs):
        status, _, text = self.call("POST", path, body, **kwargs)
        return status, json.loads(text)

    def test_full_workflow_over_http_with_confirmed_budgets(self):
        status, session = self.call("GET", "/api/session", token=False)[0], json.loads(self.call("GET", "/api/session", token=False)[2])
        self.assertEqual((status, session["token"], session["default_histories"], session["max_histories"]), (200, self.state.token, 3, 8))

        status, plan = self.post("/api/search/plan", {"query": "bakery in testville"})
        self.assertEqual((status, plan["max_requests"], self.api.new_requests), (200, 1, 0))
        status, found = self.post("/api/search", {"plan": plan["plan"]})
        self.assertEqual((status, len(found["businesses"]), self.api.new_requests), (200, 2, 1))

        self.assertEqual(self.post("/api/investigations/plan", {"business": found["businesses"][0]["ref"], "histories": 2, "history_limit": 500})[0], 400)
        for bad_pages in (0, 5, "2"):
            self.assertEqual(self.post("/api/investigations/plan", {"business": found["businesses"][0]["ref"], "histories": 2, "pages": bad_pages})[0], 400)
        status, plan = self.post("/api/investigations/plan", {"business": found["businesses"][0]["ref"], "histories": 2, "history_limit": 20})
        self.assertEqual(plan["pages"], 1)
        self.assertEqual((status, plan["max_requests"], plan["business"], self.api.new_requests), (200, 3, "Alpha Bakery", 1))
        self.assertEqual(plan["history_limit"], 20)
        status, started = self.post("/api/investigations", {"plan": plan["plan"]})
        self.assertEqual(status, 200)
        self.state._investigations[started["investigation"]].thread.join(timeout=10)

        status, _, text = self.call("GET", f"/api/investigations/{started['investigation']}")
        result = json.loads(text)
        self.assertEqual((status, result["state"], result["requests_sent"], self.api.new_requests), (200, "complete", 3, 4))
        self.assertEqual(result["view"]["metrics"]["pairs_sharing_two"], 1)
        self.assertEqual([c.get("num") for c in self.api.calls if "contributor_id" in c], [20, 20])
        for private in PRIVATE + ["api_key"]:
            self.assertNotIn(private, text)

    def test_expansion_over_http_is_a_separately_confirmed_stage(self):
        self.api.use_overlap_fixture()
        _, plan = self.post("/api/search/plan", {"query": "bakery in testville"})
        _, found = self.post("/api/search", {"plan": plan["plan"]})
        _, plan = self.post("/api/investigations/plan", {"business": found["businesses"][0]["ref"], "histories": 2})
        _, started = self.post("/api/investigations", {"plan": plan["plan"]})
        investigation = started["investigation"]
        self.state._investigations[investigation].thread.join(timeout=10)
        sent = self.api.new_requests

        self.assertEqual(self.post("/api/expansions/plan", {"investigation": investigation, "businesses": 3})[0], 400)
        self.assertEqual(self.post("/api/expansions/plan", {"investigation": "unknown", "businesses": 1})[0], 404)
        self.assertEqual(self.post("/api/expansions", {"plan": "forged", "investigation": investigation})[0], 409)
        status, plan = self.post("/api/expansions/plan", {"investigation": investigation, "businesses": 2})
        self.assertEqual((status, plan["max_requests"], self.api.new_requests), (200, 2, sent))
        status, _ = self.post("/api/expansions", {"plan": plan["plan"], "investigation": investigation})
        self.assertEqual(status, 200)
        self.state._investigations[investigation].thread.join(timeout=10)
        status, _, text = self.call("GET", f"/api/investigations/{investigation}")
        result = json.loads(text)
        self.assertEqual((status, result["expansion"]["last"]["businesses_collected"], self.api.new_requests - sent), (200, 2, 2))
        self.assertEqual(result["view"]["metrics"]["reviewers"], 14)
        for private in PRIVATE + [EXTRA_X, "api_key"]:
            self.assertNotIn(private, text)

    def test_every_spending_endpoint_refuses_requests_without_the_session_token(self):
        for path in ("/api/search/plan", "/api/search", "/api/investigations/plan", "/api/investigations", "/api/expansions/plan", "/api/expansions"):
            for headers, token in (({}, False), ({app.TOKEN_HEADER: "wrong"}, False), ({app.TOKEN_HEADER: ""}, False)):
                with self.subTest(path=path, headers=headers):
                    status, payload = self.post(path, {"query": "bakery in testville"}, headers=headers, token=token)
                    self.assertEqual(status, 403)
        self.assertEqual(self.call("GET", "/api/investigations/anything", token=False)[0], 403)
        self.assertEqual(self.api.calls, [])

    def test_cross_origin_and_rebinding_requests_are_refused(self):
        body = {"query": "bakery in testville"}
        cases = [
            {"Origin": "https://attacker.example"},
            {"Origin": f"http://127.0.0.1:{self.port + 1}"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"Host": "attacker.example"},
            {"Host": f"attacker.example:{self.port}"},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.post("/api/search/plan", body, headers=headers)[0], 403)
        # The same checks guard reads: a rebinding page cannot read the token or a view.
        self.assertEqual(self.call("GET", "/api/session", headers={"Host": "attacker.example"}, token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/session", headers={"Sec-Fetch-Site": "cross-site"}, token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/view?mode=synthetic", headers={"Host": "attacker.example"}, token=False)[0], 403)
        self.assertEqual(self.call("GET", "/", headers={"Host": "attacker.example"}, token=False)[0], 403)
        # Same-origin requests with the browser's own headers are accepted.
        ok = {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.post("/api/search/plan", body, headers=ok)[0], 200)
        self.assertEqual(self.api.calls, [])

    def test_no_response_grants_cross_origin_access_or_sets_a_cookie(self):
        responses = [
            self.call("GET", "/api/session", token=False),
            self.call("POST", "/api/search/plan", {"query": "bakery in testville"}),
            self.call("POST", "/api/search/plan", {"query": "bakery"}, headers={"Origin": "https://attacker.example"}),
            self.call("OPTIONS", "/api/search/plan", token=False),
        ]
        for status, headers, _ in responses:
            names = {name.lower() for name in headers}
            self.assertFalse({n for n in names if n.startswith("access-control-")})
            self.assertNotIn("set-cookie", names)
        self.assertNotEqual(responses[-1][0], 200)

    def test_malformed_requests_are_rejected_before_any_action(self):
        self.assertEqual(self.call("POST", "/api/search/plan", b"query=bakery", headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.assertEqual(self.call("POST", "/api/search/plan", b'{"query": "bakery"}', headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/search/plan", b"{not json", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.post("/api/search/plan", ["bakery"])[0], 400)
        self.assertEqual(self.post("/api/search/plan", {"query": "x" * 5000})[0], 413)
        self.assertEqual(self.post("/api/search/plan", {})[0], 400)
        self.assertEqual(self.post("/api/unknown", {})[0], 404)
        self.assertEqual(self.post("/api/view", {})[0], 404)
        self.assertEqual(self.api.calls, [])

    def test_errors_from_serpapi_reach_the_browser_only_as_fixed_text(self):
        self.api.fail_new_request_number = 1
        self.api.error = SerpApiError(f"SerpApi error (HTTP 401): Invalid API key abc123 for {BUSINESS}")
        _, plan = self.post("/api/search/plan", {"query": "bakery in testville"})
        status, payload = self.post("/api/search", {"plan": plan["plan"]})
        self.assertEqual((status, payload), (502, {"error": "SerpApi rejected the request (HTTP 401)."}))

    def test_unexpected_failure_is_reported_without_details(self):
        self.state._is_cached = lambda params: (_ for _ in ()).throw(RuntimeError(f"boom {BUSINESS}"))
        status, payload = self.post("/api/search/plan", {"query": "bakery in testville"})
        self.assertEqual((status, payload), (500, {"error": "The request could not be completed."}))

    def test_viewing_modes_never_call_serpapi(self):
        for path in ("/", "/static/app.js", "/api/view?mode=synthetic", "/api/view?mode=real", "/api/session"):
            self.call("GET", path, token=False)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
