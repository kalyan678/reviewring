"""Offline tests for the dashboard's data layer: indicators, synthetic fixtures,
the saved-response loader, and the view models that form the privacy boundary.

All "real mode" inputs here are synthetic stand-ins with marker identifiers, so
a leak of any identifier into a view is detectable. Nothing touches the network.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring import synthetic  # noqa: E402
from reviewring.graph import build_graph, compute_metrics  # noqa: E402
from reviewring.indicators import overlap_groups, shared_business_pairs  # noqa: E402
from reviewring.loader import SavedDataError, load_saved_graph  # noqa: E402
from reviewring.models import (  # noqa: E402
    MAPS_REVIEWS_ENGINE,
    ContributorHistory,
    ParsedResponse,
    ReviewObservation,
    ReviewsPage,
    Source,
)
from reviewring.views import INVESTIGATION, REAL, SYNTHETIC, build_view, real_view, synthetic_view  # noqa: E402

MARKER_BUSINESS_A = "0x00000000000000aa:0x00000000000000a1"
MARKER_BUSINESS_B = "0x00000000000000bb:0x00000000000000b1"
MARKER_BUSINESS_C = "0x00000000000000cc:0x00000000000000c1"
MARKER_CATEGORY = "Private marker category"
MARKER_NAME = "Private Reviewer Name"
MARKER_TEXT = "Private review text."
MARKER_TOKEN = "PRIVATE-TOKEN-0001"


def marker_reviewer(n: int) -> str:
    return f"1000000000000000000{n:02d}"


def graph_of(*pairs: tuple[str, str]):
    observations = [
        ReviewObservation(reviewer, business, Source("a.json", MAPS_REVIEWS_ENGINE, 1))
        for reviewer, business in pairs
    ]
    return build_graph([ParsedResponse("a.json", MAPS_REVIEWS_ENGINE, observations=observations)])


def _review(n: int, review_id: str, rating: float = 5.0) -> dict:
    return {
        "review_id": review_id,
        "rating": rating,
        "snippet": MARKER_TEXT,
        "user": {"name": MARKER_NAME, "link": "https://example.invalid/p", "contributor_id": marker_reviewer(n)},
    }


def saved_responses() -> dict[str, dict]:
    """Stand-in for data/raw: one search, two review pages, one contributor history."""
    return {
        "google_maps_aaaaaaaaaaaaaaaa.json": {"search_parameters": {"engine": "google_maps", "q": "x"}},
        "google_maps_reviews_bbbbbbbbbbbbbbbb.json": {
            "search_parameters": {"engine": "google_maps_reviews", "data_id": MARKER_BUSINESS_A},
            "place_info": {"title": "Private Title", "type": MARKER_CATEGORY, "reviews": 40, "rating": 4.5},
            "reviews": [_review(1, "a-1"), _review(2, "a-2", 3.0), _review(3, "a-3")],
            "serpapi_pagination": {"next_page_token": MARKER_TOKEN},
        },
        "google_maps_reviews_cccccccccccccccc.json": {
            "search_parameters": {"engine": "google_maps_reviews", "data_id": MARKER_BUSINESS_B},
            "place_info": {"type": MARKER_CATEGORY, "reviews": 2, "rating": 5},
            "reviews": [_review(3, "b-3"), _review(4, "b-4")],
        },
        "google_maps_contributor_reviews_dddddddddddddddd.json": {
            "search_parameters": {"engine": "google_maps_contributor_reviews", "contributor_id": marker_reviewer(3)},
            "contributor": {"name": MARKER_NAME},
            "reviews": [
                {"review_id": "a-3", "rating": 5, "place_info": {"data_id": MARKER_BUSINESS_A, "type": MARKER_CATEGORY}},
                {"review_id": "b-3", "rating": 5, "place_info": {"data_id": MARKER_BUSINESS_B, "type": MARKER_CATEGORY}},
                {"review_id": "c-3", "rating": 5, "place_info": {"data_id": MARKER_BUSINESS_C, "type": MARKER_CATEGORY}},
            ],
        },
    }


class SavedDataTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmp.name) / "raw"
        self.raw_dir.mkdir()
        self.files = saved_responses()
        for name, content in self.files.items():
            self.write(name, json.dumps(content))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        (self.raw_dir / name).write_text(text, encoding="utf-8")


class IndicatorTests(unittest.TestCase):
    def test_pairs_need_the_minimum_number_of_shared_businesses(self):
        graph = graph_of(("r1", "b1"), ("r2", "b1"), ("r3", "b1"), ("r1", "b2"), ("r2", "b2"))
        pairs = shared_business_pairs(graph)
        self.assertEqual([(p.reviewers, p.businesses) for p in pairs], [(("r1", "r2"), ("b1", "b2"))])
        self.assertEqual(len(shared_business_pairs(graph, minimum=1)), 3)
        self.assertEqual(shared_business_pairs(graph, minimum=3), [])

    def test_groups_are_connected_sets_of_linked_reviewers(self):
        # r1-r2 share b1,b2; r2-r3 share b3,b4: one group of three through r2.
        # r4-r5 share b5,b6: a second group. r6 shares one business: no group.
        graph = graph_of(
            ("r1", "b1"), ("r2", "b1"), ("r1", "b2"), ("r2", "b2"),
            ("r2", "b3"), ("r3", "b3"), ("r2", "b4"), ("r3", "b4"),
            ("r4", "b5"), ("r5", "b5"), ("r4", "b6"), ("r5", "b6"),
            ("r6", "b1"),
        )
        groups = overlap_groups(graph)
        self.assertEqual([g.reviewers for g in groups], [("r1", "r2", "r3"), ("r4", "r5")])
        self.assertEqual(groups[0].businesses, ("b1", "b2", "b3", "b4"))
        self.assertEqual([g.pairs for g in groups], [2, 1])

    def test_no_overlap_means_no_groups_and_results_are_deterministic(self):
        graph = graph_of(("r1", "b1"), ("r2", "b1"), ("r3", "b2"))
        self.assertEqual(overlap_groups(graph), [])
        busy = graph_of(("r1", "b1"), ("r2", "b1"), ("r1", "b2"), ("r2", "b2"))
        self.assertEqual(overlap_groups(busy), overlap_groups(busy))

    def test_indicator_agrees_with_graph_metrics(self):
        graph = synthetic.build_synthetic_graph()
        self.assertEqual(
            len(shared_business_pairs(graph)), compute_metrics(graph).reviewer_pairs_sharing_two_or_more
        )


class SyntheticFixtureTests(unittest.TestCase):
    def test_graph_is_deterministic_and_wholly_fictional(self):
        first, second = synthetic.build_synthetic_graph(), synthetic.build_synthetic_graph()
        self.assertEqual(first.edges, second.edges)
        self.assertEqual(first.skipped, {})
        for node_id in first.reviewer_ids | first.business_ids:
            self.assertTrue(node_id.startswith("synthetic-"), node_id)
        self.assertEqual(set(synthetic.display_names()), first.reviewer_ids | first.business_ids)

    def test_fixture_contains_the_intended_overlap_pattern(self):
        groups = overlap_groups(synthetic.build_synthetic_graph())
        self.assertEqual([len(g.reviewers) for g in groups], [5, 2])
        self.assertEqual(len(groups[0].businesses), 4)

    def test_fixture_shows_partial_coverage(self):
        view = synthetic_view()
        coverage = view["coverage"]
        self.assertGreater(coverage["history_only_businesses"], 0)
        self.assertLess(coverage["reviewers_with_history"], coverage["reviewers"])
        self.assertTrue(any(row["not_collected"] for row in coverage["business_rows"]))


class LoaderTests(SavedDataTestCase):
    def test_loads_by_engine_and_ignores_responses_without_reviewer_data(self):
        graph, report = load_saved_graph(self.raw_dir)
        self.assertEqual((len(graph.reviewer_ids), len(graph.business_ids), len(graph.edges)), (4, 3, 6))
        self.assertEqual((report.files_found, report.responses_used), (4, 3))
        self.assertEqual((report.files_without_reviewer_data, report.duplicate_responses, report.unusable_files), (1, 0, 0))

    def test_a_request_saved_twice_is_used_once(self):
        self.write("google_maps_reviews_bbbbbbbbbbbbbbbb_1.json", json.dumps(self.files["google_maps_reviews_bbbbbbbbbbbbbbbb.json"]))
        graph, report = load_saved_graph(self.raw_dir)
        self.assertEqual(report.duplicate_responses, 1)
        self.assertEqual(compute_metrics(graph).observations, 8)

    def test_unusable_files_are_counted_and_skipped(self):
        self.write("broken.json", f'{{"reviews": [{MARKER_TEXT}')
        self.write("list.json", "[]")
        self.write("no_engine.json", json.dumps({"reviews": []}))
        self.write("no_subject.json", json.dumps({"search_parameters": {"engine": "google_maps_reviews"}}))
        graph, report = load_saved_graph(self.raw_dir)
        self.assertEqual(report.unusable_files, 4)
        self.assertEqual(len(graph.edges), 6)

    def test_missing_or_empty_data_fails_without_naming_files_or_paths(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        only_search = Path(self._tmp.name) / "search"
        only_search.mkdir()
        (only_search / "google_maps_aaaaaaaaaaaaaaaa.json").write_text(
            json.dumps(self.files["google_maps_aaaaaaaaaaaaaaaa.json"]), encoding="utf-8"
        )
        for raw_dir in (Path(self._tmp.name) / "missing", empty, only_search):
            with self.subTest(raw_dir=raw_dir.name):
                with self.assertRaises(SavedDataError) as ctx:
                    load_saved_graph(raw_dir)
                message = str(ctx.exception)
                for private in (str(raw_dir), self._tmp.name, "aaaaaaaa", ".json"):
                    self.assertNotIn(private, message)


class RealViewPrivacyTests(SavedDataTestCase):
    """The real-mode view must carry aggregates only."""

    def private_strings(self) -> list[str]:
        strings = [
            MARKER_BUSINESS_A, MARKER_BUSINESS_B, MARKER_BUSINESS_C, "10000000000000000",
            MARKER_CATEGORY, MARKER_NAME, MARKER_TEXT, MARKER_TOKEN, "Private Title",
            "a-1", "b-3", "c-3", "example.invalid", ".json", str(self.raw_dir), self._tmp.name,
        ]
        # Raw filenames embed an unkeyed hash of the request parameters.
        return strings + [name.rsplit("_", 1)[-1].removesuffix(".json") for name in self.files]

    def test_view_contains_no_identifier_name_category_text_token_or_filename(self):
        text = json.dumps(real_view(self.raw_dir))
        for private in self.private_strings():
            self.assertNotIn(private, text)

    def test_network_is_aggregated_and_carries_no_individual_edges(self):
        view = real_view(self.raw_dir)
        network = view["network"]
        self.assertTrue(network["aggregated"])
        self.assertFalse(view["synthetic"])
        businesses = [n["label"] for n in network["nodes"] if n["kind"] == "business"]
        self.assertEqual(businesses, ["Business A", "Business B", "1 business (history only)"])
        for node in network["nodes"]:
            self.assertRegex(node["id"], r"^(reviewers-\d+|business-\d+|business-history-only)$")
        # Four reviewers in three connection patterns: every pattern is too
        # small to draw, so no reviewer node or link is emitted at all.
        self.assertEqual([n for n in network["nodes"] if n["kind"] == "reviewer"], [])
        self.assertEqual(network["links"], [])
        self.assertEqual(network["suppressed"], {"minimum_group_size": 5, "reviewers": 4, "links": 6})
        # Aggregate metrics are unaffected by what the network leaves out.
        self.assertEqual((view["metrics"]["reviewers"], view["metrics"]["edges"]), (4, 6))

    def test_small_reviewer_sets_are_suppressed_and_large_ones_merged(self):
        a, b, c = MARKER_BUSINESS_A, MARKER_BUSINESS_B, MARKER_BUSINESS_C
        pairs = [(marker_reviewer(n), a) for n in range(1, 7)]  # six reviewers of A only
        pairs += [(marker_reviewer(n), b) for n in range(7, 12)]  # five reviewers of B only
        pairs += [(marker_reviewer(n), c) for n in range(12, 16)]  # four reviewers of C only
        bridge = marker_reviewer(20)  # one reviewer linking all three
        pairs += [(bridge, a), (bridge, b), (bridge, c)]
        # Each business has its own collected review page, so each is its own node.
        responses = [
            ParsedResponse(
                f"{name}.json",
                MAPS_REVIEWS_ENGINE,
                observations=[
                    ReviewObservation(r, business, Source(f"{name}.json", MAPS_REVIEWS_ENGINE, 1))
                    for r, business in pairs
                    if business == business_id
                ],
                page=ReviewsPage(business_id, f"{name}.json", 0),
            )
            for name, business_id in (("a", a), ("b", b), ("c", c))
        ]
        view = build_view(build_graph(responses), REAL)
        network = view["network"]

        reviewers = [n for n in network["nodes"] if n["kind"] == "reviewer"]
        self.assertEqual([n["label"] for n in reviewers], ["6 reviewers", "5 reviewers"])
        self.assertTrue(all(n["count"] >= 5 for n in reviewers))
        # The set of four and the single bridging reviewer are counted, not drawn.
        self.assertEqual(network["suppressed"], {"minimum_group_size": 5, "reviewers": 5, "links": 7})
        self.assertEqual(sorted(link["count"] for link in network["links"]), [5, 6])
        drawn = sum(n["count"] for n in reviewers) + network["suppressed"]["reviewers"]
        self.assertEqual(drawn, view["metrics"]["reviewers"])
        self.assertEqual(
            sum(link["count"] for link in network["links"]) + network["suppressed"]["links"],
            view["metrics"]["edges"],
        )
        self.assertNotIn("1 reviewer", json.dumps(network))

    def test_history_status_is_reported_per_node_as_a_count(self):
        graph, _ = load_saved_graph(self.raw_dir)
        self.write(
            "google_maps_reviews_eeeeeeeeeeeeeeee.json",
            json.dumps({
                "search_parameters": {"engine": "google_maps_reviews", "data_id": MARKER_BUSINESS_A, "next_page_token": MARKER_TOKEN},
                "reviews": [_review(n, f"a-{n}") for n in range(5, 10)],
            }),
        )
        view = real_view(self.raw_dir)
        node = next(n for n in view["network"]["nodes"] if n["kind"] == "reviewer")
        # Reviewers 1, 2, 5-9 reviewed only A; none has a collected history.
        self.assertEqual((node["label"], node["incomplete"]), ("7 reviewers", True))
        self.assertIn("collected for 0 of 7", node["detail"][0])

    def test_reports_metrics_coverage_and_absence_of_overlap(self):
        view = real_view(self.raw_dir)
        self.assertEqual(
            {k: view["metrics"][k] for k in ("reviewers", "businesses", "edges", "pairs_sharing_two")},
            {"reviewers": 4, "businesses": 3, "edges": 6, "pairs_sharing_two": 0},
        )
        self.assertEqual(view["indicators"]["groups"], [])
        # One history collected: nothing can be compared, so no negative finding is stated.
        self.assertEqual(view["explanation"]["headline"], "Coordination cannot be assessed from this data.")
        self.assertFalse(view["explanation"]["assessment"]["assessable"])
        coverage = view["coverage"]
        self.assertEqual((coverage["businesses_with_pages"], coverage["history_only_businesses"]), (2, 1))
        self.assertEqual((coverage["reviewers_with_history"], coverage["reviewers"]), (1, 4))
        row = coverage["business_rows"][0]
        self.assertEqual((row["label"], row["captured"], row["reported_total"], row["not_collected"], row["further_pages"]), ("Business A", 3, 40, 37, True))

    def test_ratings_keep_reported_and_collected_apart_and_offer_no_adjusted_rating(self):
        ratings = real_view(self.raw_dir)["ratings"]
        self.assertEqual(ratings["distribution"], [{"rating": 5, "count": 5}, {"rating": 3.0, "count": 1}])
        first = ratings["businesses"][0]
        self.assertEqual((first["reported_rating"], first["reported_total"], first["captured_reviews"]), (4.5, 40, 3))
        self.assertNotIn("average", json.dumps(ratings).lower())
        self.assertFalse(ratings["adjusted_rating"]["available"])
        self.assertIn("Unavailable", ratings["adjusted_rating"]["reason"])

    def test_groups_in_real_data_are_reported_as_counts_only(self):
        graph = graph_of(
            (marker_reviewer(1), MARKER_BUSINESS_A), (marker_reviewer(2), MARKER_BUSINESS_A),
            (marker_reviewer(1), MARKER_BUSINESS_B), (marker_reviewer(2), MARKER_BUSINESS_B),
        )
        view = build_view(graph, REAL)
        group = view["indicators"]["groups"][0]
        self.assertEqual((group["reviewers"], group["shared_businesses"], group["pairs"]), (2, 2, 1))
        for key in ("members", "businesses", "node_ids"):
            self.assertNotIn(key, group)
        text = json.dumps(view)
        for private in (MARKER_BUSINESS_A, MARKER_BUSINESS_B, "10000000000000000"):
            self.assertNotIn(private, text)

    def test_missing_data_is_an_explicit_state_without_paths(self):
        view = real_view(Path(self._tmp.name) / "missing")
        self.assertEqual((view["mode"], view["available"], view["synthetic"]), (REAL, False, False))
        self.assertNotIn(self._tmp.name, json.dumps(view))
        self.assertNotIn("network", view)


class HeadlineTests(unittest.TestCase):
    """The headline must never read as a negative finding when little could be compared."""

    def view(self, pairs, histories, mode=REAL, **kwargs):
        page = ParsedResponse(
            "page.json",
            MAPS_REVIEWS_ENGINE,
            observations=[ReviewObservation(r, b, Source("page.json", MAPS_REVIEWS_ENGINE, 1)) for r, b in pairs],
        )
        fetched = [
            ParsedResponse(f"h{n}.json", MAPS_REVIEWS_ENGINE, history=ContributorHistory(reviewer, f"h{n}.json", 1))
            for n, reviewer in enumerate(histories)
        ]
        return build_view(build_graph([page, *fetched]), mode, **kwargs)

    def test_no_or_one_history_cannot_be_assessed(self):
        pairs = [(marker_reviewer(n), MARKER_BUSINESS_A) for n in range(1, 7)]
        for histories in ([], [marker_reviewer(1)]):
            with self.subTest(histories=len(histories)):
                explanation = self.view(pairs, histories)["explanation"]
                self.assertEqual(explanation["headline"], "Coordination cannot be assessed from this data.")
                self.assertFalse(explanation["assessment"]["assessable"])
                self.assertIn("cannot be assessed", explanation["assessment"]["statement"])
                self.assertNotIn("No reviewer pair", json.dumps(explanation))

    def test_partial_coverage_leads_with_the_limit(self):
        pairs = [(marker_reviewer(n), MARKER_BUSINESS_A) for n in range(1, 7)]
        explanation = self.view(pairs, [marker_reviewer(1), marker_reviewer(2)])["explanation"]
        self.assertEqual(
            explanation["headline"],
            "Coordination can be assessed for only 2 of 6 reviewers. No overlap was found among those 2.",
        )
        self.assertIn("does not rule coordination out", explanation["assessment"]["statement"])

    def test_full_coverage_may_state_that_no_pair_overlaps(self):
        pairs = [(marker_reviewer(1), MARKER_BUSINESS_A), (marker_reviewer(2), MARKER_BUSINESS_A)]
        explanation = self.view(pairs, [marker_reviewer(1), marker_reviewer(2)])["explanation"]
        self.assertEqual(explanation["headline"], "No reviewer pair shares 2 or more businesses.")

    def test_overlap_is_reported_as_a_pattern_whatever_the_coverage(self):
        pairs = [(marker_reviewer(n), b) for n in (1, 2) for b in (MARKER_BUSINESS_A, MARKER_BUSINESS_B)]
        explanation = self.view(pairs, [])["explanation"]
        self.assertEqual(explanation["headline"], "1 reviewer group shares 2 or more businesses (1 reviewer pair).")

    def test_unrelated_saved_collections_are_called_out_in_the_combined_view_only(self):
        pairs = [(marker_reviewer(1), MARKER_BUSINESS_A), (marker_reviewer(2), MARKER_BUSINESS_B)]
        note = "2 separate sets of businesses and reviewers"
        self.assertIn(note, " ".join(self.view(pairs, [])["explanation"]["points"]))
        self.assertIn("not one investigation", " ".join(self.view(pairs, [])["explanation"]["points"]))
        self.assertNotIn(note, " ".join(self.view(pairs, [], mode=INVESTIGATION)["explanation"]["points"]))
        connected = [(marker_reviewer(1), MARKER_BUSINESS_A), (marker_reviewer(2), MARKER_BUSINESS_A)]
        self.assertNotIn("separate sets", " ".join(self.view(connected, [])["explanation"]["points"]))

    def test_a_chosen_business_can_be_named_and_nothing_else_is(self):
        pages = [
            ParsedResponse(
                f"{name}.json", MAPS_REVIEWS_ENGINE,
                observations=[ReviewObservation(marker_reviewer(n), business, Source(f"{name}.json", MAPS_REVIEWS_ENGINE, 1)) for n in reviewers],
                page=ReviewsPage(business, f"{name}.json", len(reviewers)),
            )
            for name, business, reviewers in (("a", MARKER_BUSINESS_A, range(1, 7)), ("b", MARKER_BUSINESS_B, range(7, 9)))
        ]
        view = build_view(build_graph(pages), INVESTIGATION, business_titles={MARKER_BUSINESS_A: "Chosen Bakery"})
        self.assertEqual([row["label"] for row in view["coverage"]["business_rows"]], ["Business A", "Chosen Bakery"])
        self.assertEqual([b["label"] for b in view["ratings"]["businesses"]], ["Business A", "Chosen Bakery"])
        self.assertIn("Chosen Bakery", [n["label"] for n in view["network"]["nodes"]])
        text = json.dumps(view)
        for private in (MARKER_BUSINESS_A, MARKER_BUSINESS_B, "10000000000000000"):
            self.assertNotIn(private, text)


class ModeIsolationTests(SavedDataTestCase):
    def test_synthetic_view_is_labelled_and_holds_only_fictional_nodes(self):
        view = synthetic_view()
        self.assertEqual((view["mode"], view["synthetic"], view["network"]["aggregated"]), (SYNTHETIC, True, False))
        for node in view["network"]["nodes"]:
            self.assertTrue(node["id"].startswith("synthetic-"), node["id"])
        self.assertEqual(len(view["indicators"]["groups"]), 2)
        self.assertIn("members", view["indicators"]["groups"][0])

    def test_synthetic_view_does_not_depend_on_saved_data(self):
        # Built with no directory argument at all, and equal on every call.
        self.assertEqual(synthetic_view(), synthetic_view())
        self.assertNotIn(MARKER_BUSINESS_A, json.dumps(synthetic_view()))

    def test_real_view_contains_nothing_from_the_synthetic_fixture(self):
        text = json.dumps(real_view(self.raw_dir))
        self.assertNotIn("synthetic-", text)
        for name in synthetic.display_names().values():
            self.assertNotIn(name, text)

    def test_wording_states_limits_and_makes_no_accusation(self):
        for view in (real_view(self.raw_dir), synthetic_view()):
            text = json.dumps(view).lower()
            for claim in ("fake", "scam", "fraudulent", "review farm", "bought reviews", "% fake", "probability of"):
                self.assertNotIn(claim, text)
        self.assertIn("not proof of fraud", " ".join(synthetic_view()["explanation"]["points"]))
        self.assertIn("no coordination signal", " ".join(real_view(self.raw_dir)["explanation"]["points"]))


if __name__ == "__main__":
    unittest.main()
