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
from reviewring.investigation import (  # noqa: E402
    DEFAULT_HISTORY_LIMIT,
    choose_reviewers,
    collect,
    describe_error,
    history_params,
    review_page_params,
    search_params,
)
from reviewring.models import MAPS_REVIEWS_ENGINE, ParsedResponse, ReviewerAttributes  # noqa: E402
from reviewring.parsers import parse_maps_reviews  # noqa: E402
from reviewring.serpapi_client import cache_key  # noqa: E402
from reviewring.serpapi_client import MissingApiKeyError, SerpApiError  # noqa: E402

BUSINESS = "0x00000000000000aa:0x00000000000000a1"
OTHER_BUSINESS = "0x00000000000000bb:0x00000000000000b1"
SHARED_BUSINESS = "0x00000000000000cc:0x00000000000000c1"
PRIVATE_NAME = "Private Reviewer Name"
PRIVATE_TEXT = "Private review text."
# Reported review counts for reviewers 1-6 on the page, in page order.
REPORTED = {1: 1, 2: 5, 3: 3, 4: None, 5: 2, 6: 4}


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
            self.saved.add(key(params))
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
        if engine == "google_maps_reviews":
            reviews = [
                {
                    "review_id": f"page-{n}",
                    "rating": 5.0,
                    "snippet": PRIVATE_TEXT,
                    "user": {"name": PRIVATE_NAME, "contributor_id": reviewer(n), **({"reviews": count} if count is not None else {})},
                }
                for n, count in REPORTED.items()
            ]
            return {
                "search_parameters": dict(params),
                "place_info": {"title": "Alpha Bakery", "type": "Bakery", "reviews": 120, "rating": 4.5},
                "reviews": reviews,
                "serpapi_pagination": {"next_page_token": "PRIVATE-TOKEN"},
            }
        number = int(params["contributor_id"][-2:])
        entries = [{"review_id": f"page-{number}", "rating": 5, "place_info": {"data_id": BUSINESS, "type": "Bakery"}}]
        # Reviewers 2 and 3 both also reviewed one other business.
        other = SHARED_BUSINESS if number in (2, 3) else f"0x00000000000000dd:0x000000000000{number:04x}"
        entries.append({"review_id": f"other-{number}", "rating": 4, "snippet": PRIVATE_TEXT, "place_info": {"data_id": other, "type": "Cafe"}})
        return {"search_parameters": dict(params), "contributor": {"name": PRIVATE_NAME}, "reviews": entries}


def new_state(fake: FakeSerpApi) -> live.LiveState:
    return live.LiveState(search=fake.search, is_cached=fake.is_cached, key_configured=lambda: fake.key_configured)


PRIVATE = [BUSINESS, OTHER_BUSINESS, SHARED_BUSINESS, "10000000000000000", PRIVATE_NAME, PRIVATE_TEXT, "PRIVATE-TOKEN", "page-2", "other-2", "0x0000"]


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
        self.assertEqual(view["indicators"]["groups"][0]["reviewers"], 2)
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
        with mock.patch.object(live, "PROCESS_REQUEST_CEILING", 2):
            status = self.investigate(histories=3)  # search + page reach the ceiling
        self.assertEqual((self.api.new_requests, status["state"], status["histories_collected"]), (2, "partial", 0))
        self.assertIn("safety limit", status["stopped"])

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
        status, plan = self.post("/api/investigations/plan", {"business": found["businesses"][0]["ref"], "histories": 2, "history_limit": 20})
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

    def test_every_spending_endpoint_refuses_requests_without_the_session_token(self):
        for path in ("/api/search/plan", "/api/search", "/api/investigations/plan", "/api/investigations"):
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
