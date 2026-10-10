"""Parsers for saved Google Maps Reviews and Contributor Reviews responses.

Pure functions over already-loaded JSON: no file or network access. The
reviewer or business that a response is *about* is taken from the request
metadata SerpApi echoes in ``search_parameters``, never inferred from entries:

- Google Maps Reviews: business from ``search_parameters.data_id``; each
  review's reviewer from ``user.contributor_id``.
- Contributor Reviews: reviewer from ``search_parameters.contributor_id``;
  each entry's business from ``place_info.data_id``. Entries carry no reviewer
  identifier.

Entries that lack a usable identifier are skipped and counted in
``ParsedResponse.skipped``. Optional fields of the wrong type become ``None``.
Error messages never quote response content.
"""

from __future__ import annotations

from typing import Any

from reviewring.models import (
    CONTRIBUTOR_REVIEWS_ENGINE,
    MAPS_REVIEWS_ENGINE,
    BusinessAttributes,
    ContributorHistory,
    ParsedResponse,
    ReviewerAttributes,
    ReviewObservation,
    ReviewsPage,
    Source,
)


class ParseError(Exception):
    """A response cannot be attributed to a reviewer or business. Messages contain no response content."""


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _whole_number(value: Any) -> int | None:
    """An integer given as a number or as digits (request parameters are echoed as text)."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return _integer(value)


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _search_parameters(response: Any, engine: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ParseError(f"response is {type(response).__name__}, expected an object")
    params = response.get("search_parameters")
    if not isinstance(params, dict):
        raise ParseError("response has no 'search_parameters' object")
    if params.get("engine") != engine:
        raise ParseError(f"search_parameters.engine is not '{engine}'")
    return params


def _entries(response: dict[str, Any], parsed: ParsedResponse) -> list[Any]:
    reviews = response.get("reviews")
    if reviews is None:
        parsed.skipped["reviews_collection_missing"] += 1
        return []
    if not isinstance(reviews, list):
        raise ParseError(f"'reviews' is {type(reviews).__name__}, expected a list")
    return reviews


def _next_token(response: dict[str, Any]) -> str | None:
    return _text(_object(response.get("serpapi_pagination")).get("next_page_token"))


def _observation(
    entry: dict[str, Any], reviewer_id: str, business_id: str, source: Source
) -> ReviewObservation:
    return ReviewObservation(
        reviewer_id=reviewer_id,
        business_id=business_id,
        source=source,
        review_id=_text(entry.get("review_id")),
        rating=_number(entry.get("rating")),
        date_text=_text(entry.get("date")),
        iso_date=_text(entry.get("iso_date")),
    )


def parse_maps_reviews(response: Any, source_file: str) -> ParsedResponse:
    """Parse one Google Maps Reviews page: many reviewers of one business."""
    params = _search_parameters(response, MAPS_REVIEWS_ENGINE)
    business_id = _text(params.get("data_id"))
    if business_id is None:
        raise ParseError("search_parameters.data_id is not a non-empty string")

    parsed = ParsedResponse(source_file=source_file, engine=MAPS_REVIEWS_ENGINE)
    reviews = _entries(response, parsed)
    place_info = _object(response.get("place_info"))
    parsed.business_attributes.append(
        BusinessAttributes(
            business_id=business_id,
            source_file=source_file,
            category=_text(place_info.get("type")),
            reported_review_total=_integer(place_info.get("reviews")),
            reported_rating=_number(place_info.get("rating")),
        )
    )
    parsed.page = ReviewsPage(
        business_id=business_id,
        source_file=source_file,
        reviews_listed=len(reviews),
        request_token=_text(params.get("next_page_token")),
        next_token=_next_token(response),
    )

    for position, review in enumerate(reviews, start=1):
        if not isinstance(review, dict):
            parsed.skipped["entry_not_an_object"] += 1
            continue
        user = _object(review.get("user"))
        reviewer_id = _text(user.get("contributor_id"))
        if reviewer_id is None:
            parsed.skipped["missing_contributor_id"] += 1
            continue
        source = Source(source_file, MAPS_REVIEWS_ENGINE, position)
        parsed.observations.append(_observation(review, reviewer_id, business_id, source))
        parsed.reviewer_attributes.append(
            ReviewerAttributes(
                reviewer_id=reviewer_id,
                source_file=source_file,
                reported_review_count=_integer(user.get("reviews")),
                photo_count=_integer(user.get("photos")),
                local_guide=_flag(user.get("local_guide")),
            )
        )
    return parsed


def parse_contributor_reviews(response: Any, source_file: str) -> ParsedResponse:
    """Parse one Contributor Reviews response: many businesses reviewed by one reviewer."""
    params = _search_parameters(response, CONTRIBUTOR_REVIEWS_ENGINE)
    reviewer_id = _text(params.get("contributor_id"))
    if reviewer_id is None:
        raise ParseError("search_parameters.contributor_id is not a non-empty string")

    parsed = ParsedResponse(source_file=source_file, engine=CONTRIBUTOR_REVIEWS_ENGINE)
    entries = _entries(response, parsed)
    contributor = _object(response.get("contributor"))
    parsed.reviewer_attributes.append(
        ReviewerAttributes(
            reviewer_id=reviewer_id,
            source_file=source_file,
            local_guide=_flag(contributor.get("local_guide")),
        )
    )
    parsed.history = ContributorHistory(
        reviewer_id=reviewer_id,
        source_file=source_file,
        entries_listed=len(entries),
        next_token=_next_token(response),
        requested_limit=_whole_number(params.get("num")),
        reported_total=_integer(_object(contributor.get("contributions")).get("reviews")),
    )

    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            parsed.skipped["entry_not_an_object"] += 1
            continue
        place_info = _object(entry.get("place_info"))
        business_id = _text(place_info.get("data_id"))
        if business_id is None:
            parsed.skipped["missing_business_data_id"] += 1
            continue
        source = Source(source_file, CONTRIBUTOR_REVIEWS_ENGINE, position)
        parsed.observations.append(_observation(entry, reviewer_id, business_id, source))
        parsed.business_attributes.append(
            BusinessAttributes(
                business_id=business_id,
                source_file=source_file,
                category=_text(place_info.get("type")),
            )
        )
    return parsed


PARSERS = {
    MAPS_REVIEWS_ENGINE: parse_maps_reviews,
    CONTRIBUTOR_REVIEWS_ENGINE: parse_contributor_reviews,
}
