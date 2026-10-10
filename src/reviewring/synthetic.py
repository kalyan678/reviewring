"""SYNTHETIC demo data: fictional businesses and reviewers. Not real data.

Everything here is invented to demonstrate what a multi-reviewer,
multi-business overlap looks like in the graph. No business, reviewer, rating,
or identifier comes from a SerpApi response, and none is a modified real
record. Identifiers carry a ``synthetic-`` prefix so they cannot be mistaken
for real ones.

The fixture is a table of who reviewed what, plus which review pages and
contributor histories the demo pretends were collected. It is rendered into
response-shaped dictionaries and passed through the real parsers and merge, so
the demo exercises the same pipeline as saved data. The table is constant, so
the graph is identical on every run.
"""

from __future__ import annotations

from typing import Any

from reviewring.graph import Graph, build_graph
from reviewring.models import CONTRIBUTOR_REVIEWS_ENGINE, MAPS_REVIEWS_ENGINE
from reviewring.parsers import parse_contributor_reviews, parse_maps_reviews


def business_id(number: int) -> str:
    return f"synthetic-business-{number:02d}"


def reviewer_id(number: int) -> str:
    return f"synthetic-reviewer-{number:02d}"


# Fictional businesses: number -> (name, category).
BUSINESSES = {
    1: ("Harbourline Dental Studio", "Dental clinic"),
    2: ("Copper Kettle Cafe", "Cafe"),
    3: ("Northgate Auto Repair", "Auto repair shop"),
    4: ("Lumen Yoga Loft", "Yoga studio"),
    5: ("Fernbank Grocers", "Grocery store"),
    6: ("Quill and Ledger Tax Advisers", "Tax consultant"),
    7: ("Pinecrest Veterinary Care", "Veterinarian"),
}

# Fictional reviews: reviewer number -> {business number: rating}.
#
# Reviewers 1-5 all reviewed the same unrelated businesses (dentist, garage,
# yoga studio, tax adviser) with top ratings: the pattern the demo is about.
# Reviewers 6 and 7 both reviewed the cafe and the grocer, an everyday overlap
# that the same structural indicator also picks up.
REVIEWS = {
    1: {1: 5, 3: 5, 4: 5, 6: 5},
    2: {1: 5, 3: 5, 4: 5, 6: 5},
    3: {1: 5, 3: 5, 4: 5, 6: 5},
    4: {1: 5, 3: 5, 4: 5, 6: 5},
    5: {1: 5, 3: 5, 6: 5},
    6: {2: 4, 5: 3},
    7: {2: 5, 5: 4},
    8: {2: 4},
    9: {5: 3, 7: 5},
    10: {1: 2},
    11: {1: 4, 2: 5},
    13: {2: 3, 7: 5},
    14: {3: 1},
}

# Review pages the demo pretends to have captured: business number ->
# (reported review total, Google rating, further page offered).
PAGES = {
    1: (7, 4.6, False),
    2: (48, 4.2, True),  # partly captured: most reviewers not collected
    3: (6, 4.3, False),
}

# Reviewers whose contributor history the demo pretends to have fetched.
HISTORIES = (1, 2, 3, 4, 5, 6, 7, 9)


def _review_id(reviewer: int, business: int) -> str:
    return f"synthetic-review-{reviewer:02d}-{business:02d}"


def _page_response(business: int) -> dict[str, Any]:
    total, rating, more = PAGES[business]
    response: dict[str, Any] = {
        "search_parameters": {"engine": MAPS_REVIEWS_ENGINE, "data_id": business_id(business)},
        "place_info": {"type": BUSINESSES[business][1], "reviews": total, "rating": rating},
        "reviews": [
            {
                "review_id": _review_id(reviewer, business),
                "rating": float(ratings[business]),
                "user": {"contributor_id": reviewer_id(reviewer), "reviews": len(ratings)},
            }
            for reviewer, ratings in REVIEWS.items()
            if business in ratings
        ],
    }
    if more:
        response["serpapi_pagination"] = {"next_page_token": f"synthetic-token-{business:02d}"}
    return response


def _history_response(reviewer: int) -> dict[str, Any]:
    return {
        "search_parameters": {
            "engine": CONTRIBUTOR_REVIEWS_ENGINE,
            "contributor_id": reviewer_id(reviewer),
        },
        "reviews": [
            {
                "review_id": _review_id(reviewer, business),
                "rating": rating,
                "place_info": {"data_id": business_id(business), "type": BUSINESSES[business][1]},
            }
            for business, rating in REVIEWS[reviewer].items()
        ],
    }


def build_synthetic_graph() -> Graph:
    """The demo graph, built through the same parsers and merge as saved data."""
    parsed = [
        parse_maps_reviews(_page_response(business), f"synthetic-page-{business:02d}")
        for business in PAGES
    ]
    parsed += [
        parse_contributor_reviews(_history_response(reviewer), f"synthetic-history-{reviewer:02d}")
        for reviewer in HISTORIES
    ]
    return build_graph(parsed)


def display_names() -> dict[str, str]:
    """Fictional display label for every synthetic node ID."""
    names = {business_id(number): name for number, (name, _) in BUSINESSES.items()}
    names.update({reviewer_id(number): f"Reviewer S{number:02d}" for number in REVIEWS})
    return names
