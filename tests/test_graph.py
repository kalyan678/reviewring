"""Offline tests for reviewring.graph and reviewring.privacy.

Graphs are built from synthetic observations. No real identifier or secret is
used and nothing here touches the network.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring import privacy  # noqa: E402
from reviewring.graph import (  # noqa: E402
    DATE_TEXT_DIFFERENCE,
    ISO_DATE_CONFLICT,
    MULTIPLE_REVIEW_IDS,
    RATING_CONFLICT,
    build_graph,
    business_coverage,
    compute_metrics,
    pseudonymous_edges,
)
from reviewring.models import (  # noqa: E402
    CONTRIBUTOR_REVIEWS_ENGINE,
    MAPS_REVIEWS_ENGINE,
    BusinessAttributes,
    ContributorHistory,
    ParsedResponse,
    ReviewObservation,
    ReviewsPage,
    Source,
)

FAKE_KEY = b"synthetic-test-key-0123456789abcdef"
OTHER_KEY = b"another-synthetic-key-0123456789abcd"


def obs(reviewer: str, business: str, file: str = "a.json", engine: str = MAPS_REVIEWS_ENGINE, **fields: object) -> ReviewObservation:
    return ReviewObservation(reviewer, business, Source(file, engine, 1), **fields)


def response(file: str, observations: list[ReviewObservation], **fields: object) -> ParsedResponse:
    engine = observations[0].source.engine if observations else MAPS_REVIEWS_ENGINE
    return ParsedResponse(source_file=file, engine=engine, observations=observations, **fields)


def graph_of(*observations: ReviewObservation):
    """One response per source file, as the entry point supplies them."""
    by_file: dict[str, list[ReviewObservation]] = {}
    for observation in observations:
        by_file.setdefault(observation.source.file, []).append(observation)
    return build_graph(response(file, found) for file, found in by_file.items())


class MergeTests(unittest.TestCase):
    def test_same_review_seen_on_two_endpoints_is_one_edge_with_both_sources(self):
        graph = graph_of(
            obs("r1", "b1", "page.json", review_id="rev-1", rating=5.0, date_text="a month ago"),
            obs("r1", "b1", "history.json", CONTRIBUTOR_REVIEWS_ENGINE, review_id="rev-1", rating=5, date_text="a month ago"),
        )
        self.assertEqual(len(graph.edges), 1)
        edge = graph.edges[("r1", "b1")]
        self.assertEqual({o.source.file for o in edge.observations}, {"page.json", "history.json"})
        self.assertEqual(edge.review_ids, {"rev-1"})
        # 5.0 and 5 are the same rating.
        self.assertEqual(edge.conflicts, ())

    def test_observation_without_review_id_merges_on_reviewer_and_business(self):
        graph = graph_of(
            obs("r1", "b1", "page.json", review_id="rev-1", rating=4),
            obs("r1", "b1", "history.json", rating=4),
        )
        edge = graph.edges[("r1", "b1")]
        self.assertEqual(len(edge.observations), 2)
        self.assertEqual(edge.conflicts, ())

    def test_distinct_review_ids_on_one_pair_stay_one_flagged_edge(self):
        graph = graph_of(
            obs("r1", "b1", "page.json", review_id="rev-1", rating=5),
            obs("r1", "b1", "history.json", review_id="rev-2", rating=1),
        )
        self.assertEqual(len(graph.edges), 1)
        edge = graph.edges[("r1", "b1")]
        self.assertEqual(edge.review_ids, {"rev-1", "rev-2"})
        # Two different reviews may carry different ratings: not a rating conflict.
        self.assertEqual(edge.conflicts, (MULTIPLE_REVIEW_IDS,))
        self.assertEqual(compute_metrics(graph).edges, 1)

    def test_conflicting_values_for_one_review_are_flagged_and_all_kept(self):
        graph = graph_of(
            obs("r1", "b1", "page.json", review_id="rev-1", rating=5, iso_date="2026-01-01T00:00:00Z", date_text="a month ago"),
            obs("r1", "b1", "history.json", review_id="rev-1", rating=2, iso_date="2026-02-02T00:00:00Z", date_text="hace un mes"),
        )
        edge = graph.edges[("r1", "b1")]
        self.assertEqual(edge.conflicts, (DATE_TEXT_DIFFERENCE, ISO_DATE_CONFLICT, RATING_CONFLICT))
        self.assertEqual(sorted(o.rating for o in edge.observations), [2, 5])
        self.assertEqual(
            compute_metrics(graph).conflicts,
            {DATE_TEXT_DIFFERENCE: 1, ISO_DATE_CONFLICT: 1, RATING_CONFLICT: 1},
        )

    def test_a_missing_value_never_conflicts_with_a_known_one(self):
        graph = graph_of(
            obs("r1", "b1", "page.json", review_id="rev-1", rating=5, iso_date="2026-01-01T00:00:00Z"),
            obs("r1", "b1", "history.json", review_id="rev-1"),
        )
        self.assertEqual(graph.edges[("r1", "b1")].conflicts, ())

    def test_values_are_compared_within_a_review_when_a_pair_has_several(self):
        graph = graph_of(
            obs("r1", "b1", "a.json", review_id="rev-1", rating=5),
            obs("r1", "b1", "b.json", review_id="rev-1", rating=3),
            obs("r1", "b1", "c.json", review_id="rev-2", rating=1),
            obs("r1", "b1", "d.json", rating=4),
        )
        self.assertEqual(graph.edges[("r1", "b1")].conflicts, (MULTIPLE_REVIEW_IDS, RATING_CONFLICT))

    def test_the_same_source_file_cannot_be_merged_twice(self):
        parsed = response("page.json", [obs("r1", "b1", "page.json")])
        with self.assertRaisesRegex(ValueError, "more than once"):
            build_graph([parsed, parsed])

    def test_attribute_records_are_kept_per_source_not_merged(self):
        first = response("a.json", [obs("r1", "b1", "a.json")], business_attributes=[BusinessAttributes("b1", "a.json", "Clinic", 40)])
        second = response("b.json", [obs("r2", "b1", "b.json")], business_attributes=[BusinessAttributes("b1", "b.json", None, 41)])
        graph = build_graph([first, second])
        self.assertEqual([(a.category, a.reported_review_total) for a in graph.business_attributes["b1"]], [("Clinic", 40), (None, 41)])
        self.assertEqual(business_coverage(graph)[0].reported_review_totals, (40, 41))


class MetricsTests(unittest.TestCase):
    def test_counts_degrees_components_and_overlap(self):
        # Component 1: r1, r2, r3 around b1 and b2. Component 2: r4-b3.
        graph = graph_of(
            obs("r1", "b1"), obs("r2", "b1"), obs("r3", "b1"),
            obs("r1", "b2"), obs("r2", "b2"),
            obs("r4", "b3"),
        )
        metrics = compute_metrics(graph)
        self.assertEqual((metrics.reviewers, metrics.businesses, metrics.edges), (4, 3, 6))
        self.assertEqual(metrics.connected_components, 2)
        self.assertEqual(metrics.reviewer_degrees, {1: 2, 2: 2})
        self.assertEqual(metrics.business_degrees, {1: 1, 2: 1, 3: 1})
        # Pairs sharing b1: r1-r2, r1-r3, r2-r3. Only r1-r2 also shares b2.
        self.assertEqual(metrics.reviewer_pairs_sharing_a_business, 3)
        self.assertEqual(metrics.reviewer_pairs_sharing_two_or_more, 1)

    def test_repeated_observations_do_not_inflate_edges_degrees_or_overlap(self):
        graph = graph_of(
            obs("r1", "b1", "a.json", review_id="rev-1"),
            obs("r1", "b1", "b.json", review_id="rev-1"),
            obs("r1", "b1", "c.json", review_id="rev-9"),
            obs("r2", "b1", "a.json"),
        )
        metrics = compute_metrics(graph)
        self.assertEqual((metrics.edges, metrics.observations), (2, 4))
        self.assertEqual(metrics.edges_observed_more_than_once, 1)
        self.assertEqual(metrics.business_degrees, {2: 1})
        self.assertEqual(metrics.reviewer_pairs_sharing_a_business, 1)
        self.assertEqual(metrics.reviewer_pairs_sharing_two_or_more, 0)

    def test_same_string_as_reviewer_and_business_are_different_nodes(self):
        metrics = compute_metrics(graph_of(obs("x", "y"), obs("y", "z")))
        self.assertEqual((metrics.reviewers, metrics.businesses, metrics.connected_components), (2, 2, 2))

    def test_reviewer_with_an_empty_history_is_an_isolated_node(self):
        empty = ParsedResponse("h.json", CONTRIBUTOR_REVIEWS_ENGINE, history=ContributorHistory("r9", "h.json", 0))
        graph = build_graph([response("a.json", [obs("r1", "b1")]), empty])
        metrics = compute_metrics(graph)
        self.assertEqual((metrics.reviewers, metrics.connected_components), (2, 2))
        self.assertEqual(metrics.reviewer_degrees, {0: 1, 1: 1})

    def test_empty_graph(self):
        metrics = compute_metrics(build_graph([]))
        self.assertEqual((metrics.reviewers, metrics.businesses, metrics.edges, metrics.connected_components), (0, 0, 0, 0))


class CoverageTests(unittest.TestCase):
    def test_distinguishes_review_pages_from_history_only_businesses(self):
        page1 = response(
            "p1.json", [obs("r1", "b1", "p1.json"), obs("r2", "b1", "p1.json")],
            page=ReviewsPage("b1", "p1.json", 2, None, "token-1"),
            business_attributes=[BusinessAttributes("b1", "p1.json", "Clinic", 50)],
        )
        page2 = response(
            "p2.json", [obs("r3", "b1", "p2.json")],
            page=ReviewsPage("b1", "p2.json", 1, "token-1", "token-2"),
        )
        history = response(
            "h.json",
            [obs("r1", "b1", "h.json", CONTRIBUTOR_REVIEWS_ENGINE), obs("r1", "b2", "h.json", CONTRIBUTOR_REVIEWS_ENGINE)],
            history=ContributorHistory("r1", "h.json", 2),
        )
        coverage = {c.business_id: c for c in business_coverage(build_graph([page1, page2, history]))}

        reviewed = coverage["b1"]
        self.assertFalse(reviewed.history_only)
        self.assertEqual((reviewed.review_pages, reviewed.reviewers_from_pages), (2, 3))
        self.assertEqual(reviewed.reported_review_totals, (50,))
        # token-1 was followed by page 2; token-2 was not followed.
        self.assertEqual(reviewed.unfollowed_page_tokens, 1)

        history_only = coverage["b2"]
        self.assertTrue(history_only.history_only)
        self.assertEqual((history_only.review_pages, history_only.reviewers_from_pages), (0, 0))
        self.assertEqual(history_only.reported_review_totals, ())

    def test_reviewer_known_only_from_a_history_is_not_counted_as_page_coverage(self):
        page = response("p.json", [obs("r1", "b1", "p.json")], page=ReviewsPage("b1", "p.json", 1))
        history = response("h.json", [obs("r2", "b1", "h.json", CONTRIBUTOR_REVIEWS_ENGINE)])
        coverage = business_coverage(build_graph([page, history]))[0]
        self.assertEqual(coverage.reviewers_from_pages, 1)


class PseudonymTests(unittest.TestCase):
    def test_deterministic_for_the_same_key(self):
        first = privacy.pseudonym("reviewer", "100000000000000000001", FAKE_KEY)
        self.assertEqual(first, privacy.pseudonym("reviewer", "100000000000000000001", FAKE_KEY))
        self.assertRegex(first, r"^R_[0-9a-f]{16}$")
        self.assertNotIn("100000000000000000001", first)

    def test_domains_identifiers_and_keys_are_separated(self):
        reviewer = privacy.pseudonym("reviewer", "same-id", FAKE_KEY)
        business = privacy.pseudonym("business", "same-id", FAKE_KEY)
        self.assertRegex(business, r"^B_[0-9a-f]{16}$")
        self.assertNotEqual(reviewer[2:], business[2:])
        self.assertNotEqual(reviewer, privacy.pseudonym("reviewer", "other-id", FAKE_KEY))
        self.assertNotEqual(reviewer, privacy.pseudonym("reviewer", "same-id", OTHER_KEY))

    def test_unknown_domain_and_short_key_are_rejected(self):
        with self.assertRaises(ValueError):
            privacy.pseudonym("place", "same-id", FAKE_KEY)
        # Swapped arguments must not echo the raw identifier in the error.
        with self.assertRaises(ValueError) as ctx:
            privacy.pseudonym("100000000000000000001", "reviewer", FAKE_KEY)
        self.assertNotIn("100000000000000000001", str(ctx.exception))
        for key in (b"", b"too-short"):
            with self.subTest(key=key):
                with self.assertRaises(privacy.MissingPseudonymKeyError):
                    privacy.pseudonym("reviewer", "same-id", key)

    def test_missing_or_short_secret_fails_and_nothing_is_generated(self):
        for value in (None, "", "   ", "too-short"):
            with self.subTest(value=value):
                env = {} if value is None else {privacy.PSEUDONYM_KEY_ENV_VAR: value}
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(privacy, "load_dotenv") as load_dotenv:
                    with self.assertRaises(privacy.MissingPseudonymKeyError) as ctx:
                        privacy.load_pseudonym_key()
                    load_dotenv.assert_called_once()
                    self.assertIn(privacy.PSEUDONYM_KEY_ENV_VAR, str(ctx.exception))
                    # The environment is not modified to supply a key.
                    self.assertEqual(os.environ.get(privacy.PSEUDONYM_KEY_ENV_VAR), value)

    def test_configured_secret_is_loaded_from_the_environment(self):
        value = FAKE_KEY.decode()
        with mock.patch.dict(os.environ, {privacy.PSEUDONYM_KEY_ENV_VAR: f"  {value}  "}, clear=True), \
                mock.patch.object(privacy, "load_dotenv"):
            self.assertEqual(privacy.load_pseudonym_key(), FAKE_KEY)

    def test_exported_edges_carry_pseudonyms_and_no_raw_identifiers(self):
        graph = graph_of(
            obs("reviewer-raw-1", "business-raw-1", "a.json", review_id="rev-1", rating=5),
            obs("reviewer-raw-1", "business-raw-1", "b.json", review_id="rev-1", rating=4),
            obs("reviewer-raw-2", "business-raw-1", "a.json"),
        )
        rows = pseudonymous_edges(graph, FAKE_KEY)

        self.assertEqual(rows, pseudonymous_edges(graph, FAKE_KEY))
        self.assertEqual(rows, sorted(rows, key=lambda row: (row["reviewer"], row["business"])))
        self.assertEqual(len({row["business"] for row in rows}), 1)
        flagged = next(row for row in rows if row["observations"] == 2)
        self.assertEqual(flagged["reviewer"], privacy.pseudonym("reviewer", "reviewer-raw-1", FAKE_KEY))
        self.assertEqual((flagged["distinct_review_ids"], flagged["ratings"], flagged["conflicts"]), (1, [4, 5], [RATING_CONFLICT]))
        for raw in ("reviewer-raw", "business-raw", "rev-1", "a.json"):
            self.assertNotIn(raw, repr(rows))

    def test_export_requires_a_valid_key(self):
        with self.assertRaises(privacy.MissingPseudonymKeyError):
            pseudonymous_edges(graph_of(obs("r1", "b1")), b"")


if __name__ == "__main__":
    unittest.main()
