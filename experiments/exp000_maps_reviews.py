"""EXP-000 / Call #2: Google Maps Reviews schema validation.

Runs ONE Google Maps Reviews request for the seed business selected after
Call #1, through the shared cached SerpApi client, and prints a small
privacy-safe diagnostic summary of the review-level schema. The client handles
the API key, caching, sanitized raw-response persistence, and errors.

The seed was selected for experimental suitability only. ReviewRing has made
no determination about this business or its reviews.

This script does not test whether any identifier works with Contributor
Reviews; that is Call #3.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp000_maps_reviews.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.serpapi_client import RAW_DIR, SerpApiError, cache_key, search  # noqa: E402
from reviewring.local_inputs import LocalInputError, text  # noqa: E402

# The seed business is a private local input (local_inputs.json), set by main().
BUSINESS = ""
SEED_DATA_ID = ""

# Parameter names taken verbatim from the `reviews_link` SerpApi returned for
# the seed business in the Call #1 sanitized raw response:
#   serpapi.com/search.json?data_id=<seed>&engine=google_maps_reviews&hl=en
# No sort/ordering parameter is included: none is established in the repository.
PARAMS: dict[str, str] = {}

MAX_REVIEWS = 5
NOT_RETURNED = "NOT RETURNED"
RETURNED = "RETURNED"
NOT_ESTABLISHED = "NOT ESTABLISHED IN REPOSITORY (identify from keys below)"

# Chosen from documentation before Call #2 ran; Call #2 has since OBSERVED all
# of these names in the response (see docs/EXPERIMENTS.md, Call #2 results).
REVIEWS_KEY = "reviews"
REVIEW_VALUE_FIELDS = [
    ("Review ID", "review_id"),
    ("Rating", "rating"),
    ("Date", "date"),
    ("ISO date", "iso_date"),
]
# Review text may contain personal information: presence only, never content.
REVIEW_PRESENCE_FIELDS = [
    ("Review text/snippet", "snippet"),
]
# Reviewer-level fields were deliberately NOT mapped to any key name before
# Call #2 ran. Call #2 has since OBSERVED them in the nested `user` object
# (`contributor_id`, `name`, `reviews`, `photos`, `local_guide`). The mapping is
# left unchanged here so this script stays a faithful record of Call #2.
REVIEWER_LABELS = [
    "Reviewer/contributor identifier",
    "Reviewer display name",
    "Reviewer review count",
    "Reviewer photo count",
    "Local Guide indicator",
]


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _print_review(number: int, review: Any) -> None:
    print()
    print(f"--- Review {number} ---")
    if not isinstance(review, dict):
        print(f"Review is not an object (got {type(review).__name__})")
        return

    for label, field in REVIEW_VALUE_FIELDS:
        value = _format_value(review[field]) if field in review else NOT_RETURNED
        print(f"{label}: {value}")
    for label, field in REVIEW_PRESENCE_FIELDS:
        print(f"{label}: {RETURNED if field in review else NOT_RETURNED}")
    for label in REVIEWER_LABELS:
        print(f"{label}: {NOT_ESTABLISHED}")

    print("Available review keys:")
    print(list(review.keys()))

    # Keys only, never values: nested objects may hold names, links, or avatars.
    nested = {key: value for key, value in review.items() if isinstance(value, dict)}
    if not nested:
        print("Nested objects: none")
    for key, value in nested.items():
        print(f"Available keys in nested object '{key}':")
        print(list(value.keys()))


def print_summary(response: dict[str, Any]) -> None:
    print("Experiment:")
    print("EXP-000 / Call #2")
    print()
    print("Business:")
    print(BUSINESS)
    print()
    print("Seed data_id:")
    print(SEED_DATA_ID)
    print()
    print(f"Cache key prefix: {cache_key(PARAMS)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()
    print(f"Top-level JSON keys: {list(response.keys())}")

    reviews = response.get(REVIEWS_KEY)
    print(f"'{REVIEWS_KEY}' collection exists: {REVIEWS_KEY in response}")
    if reviews is None:
        print("Number of reviews in this response: 0")
        return
    if not isinstance(reviews, list):
        print(f"'{REVIEWS_KEY}' is not a list (got {type(reviews).__name__}); not inspecting")
        return
    print(f"Number of reviews in this response: {len(reviews)}")

    for number, review in enumerate(reviews[:MAX_REVIEWS], start=1):
        _print_review(number, review)


def main() -> int:
    # Output may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    global BUSINESS, SEED_DATA_ID
    try:
        BUSINESS, SEED_DATA_ID = text("seed_business_name"), text("seed_data_id")
    except LocalInputError as exc:
        print(f"EXP-000 / Call #2 not run: {exc}", file=sys.stderr)
        return 1
    PARAMS.update(engine="google_maps_reviews", data_id=SEED_DATA_ID, hl="en")
    try:
        response = search(PARAMS)
    except SerpApiError as exc:
        # The client's error messages never contain the API key or request URL.
        print(f"EXP-000 / Call #2 failed: {exc}", file=sys.stderr)
        return 1
    print_summary(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
