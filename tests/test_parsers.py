"""Offline tests for reviewring.parsers.

Payloads are synthetic and shaped like the fields observed in the captured
Google Maps Reviews and Contributor Reviews responses. No real identifier is
used and nothing here touches the network.

Run from the project root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.models import CONTRIBUTOR_REVIEWS_ENGINE, MAPS_REVIEWS_ENGINE, Source  # noqa: E402
from reviewring.parsers import (  # noqa: E402
    ParseError,
    parse_contributor_reviews,
    parse_maps_reviews,
)

BUSINESS_A = "0x00000000000000aa:0x00000000000000a1"
BUSINESS_B = "0x00000000000000bb:0x00000000000000b1"
REVIEWER_1 = "100000000000000000001"
REVIEWER_2 = "100000000000000000002"
FAKE_NAME = "Test Reviewer Name"
FAKE_SNIPPET = "Private review text that must never be parsed."
FAKE_ADDRESS = "1 Private Street, Nowhere"
FAKE_TOKEN = "FAKE-TOKEN-0001"


def maps_review(contributor_id: object, review_id: object = "rev-1", **overrides: object) -> dict:
    review = {
        "position": 1,
        "rating": 5.0,
        "date": "2 months ago",
        "iso_date": "2026-08-01T00:00:00Z",
        "review_id": review_id,
        "snippet": FAKE_SNIPPET,
        "user": {
            "name": FAKE_NAME,
            "link": "https://example.invalid/profile",
            "contributor_id": contributor_id,
            "thumbnail": "https://example.invalid/avatar.png",
            "local_guide": True,
            "reviews": 4,
            "photos": 0,
        },
    }
    review.update(overrides)
    return review


def maps_response(reviews: object, data_id: object = BUSINESS_A, **overrides: object) -> dict:
    response = {
        "search_parameters": {"engine": MAPS_REVIEWS_ENGINE, "data_id": data_id, "hl": "en"},
        "place_info": {"title": "Private Title", "address": FAKE_ADDRESS, "type": "Clinic", "reviews": 40},
        "reviews": reviews,
    }
    response.update(overrides)
    return response


def history_entry(data_id: object, review_id: object = "rev-1", **overrides: object) -> dict:
    entry = {
        "place_info": {
            "title": "Private Title",
            "address": FAKE_ADDRESS,
            "gps_coordinates": {"latitude": 1.5, "longitude": 2.5},
            "type": "Clinic",
            "data_id": data_id,
        },
        "date": "2 months ago",
        "snippet": FAKE_SNIPPET,
        "review_id": review_id,
        "rating": 5,
    }
    entry.update(overrides)
    return entry


def history_response(entries: object, contributor_id: object = REVIEWER_1, **overrides: object) -> dict:
    response = {
        "search_parameters": {"engine": CONTRIBUTOR_REVIEWS_ENGINE, "contributor_id": contributor_id},
        "contributor": {"name": FAKE_NAME, "local_guide": False},
        "reviews": entries,
    }
    response.update(overrides)
    return response


class MapsReviewsParserTests(unittest.TestCase):
    def test_business_comes_from_request_metadata_and_reviewer_from_each_review(self):
        response = maps_response([maps_review(REVIEWER_1, "rev-1"), maps_review(REVIEWER_2, "rev-2")])
        parsed = parse_maps_reviews(response, "page.json")

        self.assertEqual(
            [(o.reviewer_id, o.business_id, o.review_id) for o in parsed.observations],
            [(REVIEWER_1, BUSINESS_A, "rev-1"), (REVIEWER_2, BUSINESS_A, "rev-2")],
        )
        first = parsed.observations[0]
        self.assertEqual(first.source, Source("page.json", MAPS_REVIEWS_ENGINE, 1))
        self.assertEqual((first.rating, first.date_text, first.iso_date), (5.0, "2 months ago", "2026-08-01T00:00:00Z"))
        self.assertEqual(parsed.skipped, {})

    def test_reviewer_and_business_attributes_use_observed_fields(self):
        parsed = parse_maps_reviews(maps_response([maps_review(REVIEWER_1)]), "page.json")
        reviewer = parsed.reviewer_attributes[0]
        self.assertEqual((reviewer.reported_review_count, reviewer.photo_count, reviewer.local_guide), (4, 0, True))
        business = parsed.business_attributes[0]
        self.assertEqual((business.business_id, business.category, business.reported_review_total), (BUSINESS_A, "Clinic", 40))

    def test_reported_business_rating_is_kept_when_numeric(self):
        for value, expected in ((4.9, 4.9), (5, 5), ("4.9", None), (True, None), (None, None)):
            with self.subTest(value=value):
                response = maps_response([], place_info={"rating": value})
                self.assertEqual(parse_maps_reviews(response, "page.json").business_attributes[0].reported_rating, expected)

    def test_missing_optional_fields_are_none_not_defaults(self):
        review = {"user": {"contributor_id": REVIEWER_1}}
        response = maps_response([review])
        del response["place_info"]
        parsed = parse_maps_reviews(response, "page.json")

        observation = parsed.observations[0]
        self.assertEqual(
            (observation.review_id, observation.rating, observation.date_text, observation.iso_date),
            (None, None, None, None),
        )
        reviewer = parsed.reviewer_attributes[0]
        self.assertEqual((reviewer.reported_review_count, reviewer.photo_count, reviewer.local_guide), (None, None, None))
        business = parsed.business_attributes[0]
        self.assertEqual((business.category, business.reported_review_total), (None, None))

    def test_wrong_typed_optional_fields_are_none(self):
        review = maps_review(REVIEWER_1, review_id=123, rating="5", date=7, iso_date=["x"])
        review["user"].update(reviews="4", photos=True, local_guide="yes")
        response = maps_response([review], place_info={"type": 5, "reviews": "40"})
        parsed = parse_maps_reviews(response, "page.json")

        observation = parsed.observations[0]
        self.assertEqual(
            (observation.review_id, observation.rating, observation.date_text, observation.iso_date),
            (None, None, None, None),
        )
        reviewer = parsed.reviewer_attributes[0]
        self.assertEqual((reviewer.reported_review_count, reviewer.photo_count, reviewer.local_guide), (None, None, None))
        self.assertEqual(parsed.business_attributes[0].category, None)
        self.assertEqual(parsed.business_attributes[0].reported_review_total, None)

    def test_reviews_without_a_usable_contributor_id_are_skipped_and_counted(self):
        reviews = [
            maps_review(REVIEWER_1),
            maps_review(""),
            maps_review(None),
            maps_review(12345),
            {"review_id": "no-user"},
            {"user": "not-an-object"},
            "not-an-object",
            None,
        ]
        parsed = parse_maps_reviews(maps_response(reviews), "page.json")
        self.assertEqual([o.reviewer_id for o in parsed.observations], [REVIEWER_1])
        self.assertEqual(parsed.skipped, {"missing_contributor_id": 5, "entry_not_an_object": 2})
        # Coverage counts what the response listed, not what was usable.
        self.assertEqual(parsed.page.reviews_listed, 8)

    def test_pagination_tokens_are_recorded_for_coverage(self):
        response = maps_response([], serpapi_pagination={"next": "https://example.invalid", "next_page_token": FAKE_TOKEN})
        response["search_parameters"]["next_page_token"] = "FAKE-TOKEN-0000"
        page = parse_maps_reviews(response, "page.json").page
        self.assertEqual((page.request_token, page.next_token), ("FAKE-TOKEN-0000", FAKE_TOKEN))

        page = parse_maps_reviews(maps_response([]), "page.json").page
        self.assertEqual((page.request_token, page.next_token), (None, None))

    def test_missing_reviews_collection_is_empty_and_noted(self):
        response = maps_response([])
        del response["reviews"]
        parsed = parse_maps_reviews(response, "page.json")
        self.assertEqual(parsed.observations, [])
        self.assertEqual(parsed.skipped, {"reviews_collection_missing": 1})
        self.assertEqual(parsed.page.reviews_listed, 0)

    def test_unattributable_responses_fail_without_quoting_content(self):
        cases = [
            ("not-an-object", "expected an object"),
            ({"reviews": []}, "no 'search_parameters' object"),
            (maps_response([], data_id=""), "data_id is not a non-empty string"),
            (maps_response([], data_id=None), "data_id is not a non-empty string"),
            (maps_response([], data_id=123), "data_id is not a non-empty string"),
            (maps_response({"0": maps_review(REVIEWER_1)}), "'reviews' is dict"),
            (history_response([]), f"engine is not '{MAPS_REVIEWS_ENGINE}'"),
        ]
        for response, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ParseError, message) as ctx:
                    parse_maps_reviews(response, "page.json")
                for private in (REVIEWER_1, BUSINESS_A, FAKE_NAME, FAKE_SNIPPET):
                    self.assertNotIn(private, str(ctx.exception))


class ContributorReviewsParserTests(unittest.TestCase):
    def test_reviewer_comes_from_request_metadata_and_business_from_each_entry(self):
        response = history_response([history_entry(BUSINESS_A, "rev-1"), history_entry(BUSINESS_B, "rev-2")])
        parsed = parse_contributor_reviews(response, "history.json")

        self.assertEqual(
            [(o.reviewer_id, o.business_id, o.review_id) for o in parsed.observations],
            [(REVIEWER_1, BUSINESS_A, "rev-1"), (REVIEWER_1, BUSINESS_B, "rev-2")],
        )
        second = parsed.observations[1]
        self.assertEqual(second.source, Source("history.json", CONTRIBUTOR_REVIEWS_ENGINE, 2))
        # Contributor Reviews entries have no iso_date.
        self.assertEqual((second.rating, second.date_text, second.iso_date), (5, "2 months ago", None))
        self.assertEqual([b.business_id for b in parsed.business_attributes], [BUSINESS_A, BUSINESS_B])
        self.assertEqual(parsed.reviewer_attributes[0].local_guide, False)

    def test_an_identifier_inside_an_entry_is_never_used_as_the_reviewer(self):
        entry = history_entry(BUSINESS_A, user={"contributor_id": REVIEWER_2}, contributor_id=REVIEWER_2)
        parsed = parse_contributor_reviews(history_response([entry]), "history.json")
        self.assertEqual([o.reviewer_id for o in parsed.observations], [REVIEWER_1])

    def test_requested_limit_and_reported_total_are_read_when_present(self):
        for echoed, expected in ((50, 50), ("50", 50), (None, None), ("many", None), (True, None), (12.5, None)):
            with self.subTest(num=echoed):
                response = history_response([history_entry(BUSINESS_A)])
                if echoed is not None:
                    response["search_parameters"]["num"] = echoed
                self.assertEqual(parse_contributor_reviews(response, "h.json").history.requested_limit, expected)
        contributor_cases = [
            ({"contributions": {"reviews": 512, "photos": 9}}, 512),
            ({"contributions": {"opiniones": 512}}, None),  # key names vary with language
            ({"contributions": {"reviews": "512"}}, None),
            ({"contributions": "none"}, None),
            ({}, None),
        ]
        for contributor, expected in contributor_cases:
            with self.subTest(contributor=contributor):
                response = history_response([history_entry(BUSINESS_A)], contributor=contributor)
                self.assertEqual(parse_contributor_reviews(response, "h.json").history.reported_total, expected)

    def test_history_length_is_what_was_listed_not_a_reported_count(self):
        parsed = parse_contributor_reviews(history_response([history_entry(BUSINESS_A)] * 3), "history.json")
        self.assertEqual(parsed.history.entries_listed, 3)
        self.assertIsNone(parsed.reviewer_attributes[0].reported_review_count)

    def test_entries_without_a_usable_business_id_are_skipped_and_counted(self):
        entries = [
            history_entry(BUSINESS_A),
            history_entry(""),
            history_entry(None),
            history_entry(99),
            {"rating": 5},
            {"place_info": "not-an-object"},
            "not-an-object",
        ]
        parsed = parse_contributor_reviews(history_response(entries), "history.json")
        self.assertEqual([o.business_id for o in parsed.observations], [BUSINESS_A])
        self.assertEqual(parsed.skipped, {"missing_business_data_id": 5, "entry_not_an_object": 1})
        self.assertEqual(parsed.history.entries_listed, 7)

    def test_missing_contributor_object_and_pagination_are_none(self):
        response = history_response([history_entry(BUSINESS_A)])
        del response["contributor"]
        parsed = parse_contributor_reviews(response, "history.json")
        self.assertIsNone(parsed.reviewer_attributes[0].local_guide)
        self.assertIsNone(parsed.history.next_token)

    def test_unattributable_responses_fail_without_quoting_content(self):
        cases = [
            (None, "expected an object"),
            (history_response([], contributor_id=""), "contributor_id is not a non-empty string"),
            (history_response([], contributor_id=None), "contributor_id is not a non-empty string"),
            (history_response([], contributor_id=5), "contributor_id is not a non-empty string"),
            (history_response("entries"), "'reviews' is str"),
            (maps_response([]), f"engine is not '{CONTRIBUTOR_REVIEWS_ENGINE}'"),
        ]
        for response, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ParseError, message) as ctx:
                    parse_contributor_reviews(response, "history.json")
                for private in (REVIEWER_1, BUSINESS_A, FAKE_NAME, FAKE_SNIPPET):
                    self.assertNotIn(private, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
