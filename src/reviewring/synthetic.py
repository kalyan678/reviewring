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

The demo can also be expanded, step by step, with the same eligibility rule as
a real investigation (``indicators.expansion_candidates``). An expansion step
adds the fictional first review page of the businesses that rule selects.
Nothing is requested from anywhere: the pages come from the same table.
"""

from __future__ import annotations

from typing import Any

from reviewring.graph import Graph, build_graph
from reviewring.indicators import MAX_EXPANSION_BUSINESSES, expansion_candidates
from reviewring.models import CONTRIBUTOR_REVIEWS_ENGINE, MAPS_REVIEWS_ENGINE, ParsedResponse
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
    # Reviewers below are on no initially collected page and have no collected
    # history, so they are unseen until a business they reviewed is expanded.
    15: {4: 5, 6: 5},
    16: {4: 5, 6: 5},
    17: {6: 4},
    18: {5: 4},
    19: {4: 3, 5: 2},
}

# Review pages the demo pretends to have captured: business number ->
# (reported review total, Google rating, further page offered).
PAGES = {
    1: (7, 4.6, False),
    2: (48, 4.2, True),  # partly captured: most reviewers not collected
    3: (6, 4.3, False),
}

# First review pages that exist only for expansion, in the same form. Which
# of them is added, and in what order, is decided by the eligibility rule.
EXPANSION_PAGES = {
    4: (9, 4.8, False),
    5: (60, 4.1, True),  # a busy grocer: most of its reviewers stay uncollected
    6: (8, 4.9, False),
}

# The demo offers at most this many expansion steps; the rule runs out sooner.
MAX_EXPANSION_STEPS = 4

# Reviewers whose contributor history the demo pretends to have fetched.
HISTORIES = (1, 2, 3, 4, 5, 6, 7, 9)


def _review_id(reviewer: int, business: int) -> str:
    return f"synthetic-review-{reviewer:02d}-{business:02d}"


def _page_response(business: int) -> dict[str, Any]:
    total, rating, more = (PAGES | EXPANSION_PAGES)[business]
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


def synthetic_collection(expansions: int = 0) -> tuple[list[ParsedResponse], list[list[ParsedResponse]]]:
    """The demo's initial collection, and the pages added by each expansion step.

    Each step takes the businesses the real eligibility rule puts first, at
    most ``MAX_EXPANSION_BUSINESSES`` of them, exactly as a confirmed real
    expansion would. A step that finds no eligible business ends the sequence,
    so fewer steps than asked for may be returned.
    """
    original = [
        parse_maps_reviews(_page_response(business), f"synthetic-page-{business:02d}")
        for business in PAGES
    ]
    original += [
        parse_contributor_reviews(_history_response(reviewer), f"synthetic-history-{reviewer:02d}")
        for reviewer in HISTORIES
    ]
    numbers = {business_id(number): number for number in BUSINESSES}
    steps: list[list[ParsedResponse]] = []
    for _ in range(expansions):
        collected = original + [page for step in steps for page in step]
        chosen = expansion_candidates(build_graph(collected))[:MAX_EXPANSION_BUSINESSES]
        if not chosen:
            break
        steps.append(
            [
                parse_maps_reviews(_page_response(numbers[chosen_id]), f"synthetic-expansion-page-{numbers[chosen_id]:02d}")
                for chosen_id in chosen
            ]
        )
    return original, steps


def build_synthetic_graph() -> Graph:
    """The demo's initial graph, built through the same parsers and merge as saved data."""
    return build_graph(synthetic_collection()[0])


def display_names() -> dict[str, str]:
    """Fictional display label for every synthetic node ID."""
    names = {business_id(number): name for number, (name, _) in BUSINESSES.items()}
    names.update({reviewer_id(number): f"Reviewer S{number:02d}" for number in REVIEWS})
    return names
