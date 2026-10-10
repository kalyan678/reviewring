"""EXP-000 / Call #3: Contributor Reviews chain validation.

Tests whether a contributor ID taken from the Call #2 Google Maps Reviews
response can retrieve that contributor's review history through SerpApi
Contributor Reviews. Runs at most ONE request through the shared cached SerpApi
client, which handles the API key, caching, sanitized raw-response persistence,
and errors.

Contributor selection is deterministic and privacy-neutral: the first review,
in Call #2 response order, whose `user.contributor_id` is a non-empty string.
It is never based on reviewer identity, review content, rating, activity,
Local Guide status, or suspicion. The contributor ID is read from the Call #2
sanitized raw response at runtime; it is never hard-coded, printed, or
included in error messages.

Schema status:
- OBSERVED in Call #2:
  top-level `reviews` (list); review objects with `user` (object); `user`
  objects with `contributor_id` (string). Only these fields are relied on.
- VERIFIED FROM DOCUMENTATION: the request contract (engine and parameter
  name); see CONTRIBUTOR_REVIEWS_ENGINE below.
- UNVERIFIED until Call #3 is run: every Contributor Reviews response field,
  including the history collection and any business/place identifier.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp000_contributor_reviews.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.serpapi_client import RAW_DIR, SerpApiError, cache_key, search  # noqa: E402
from reviewring.local_inputs import LocalInputError, raw_file  # noqa: E402

# Saved-response paths are private local inputs (local_inputs.json), resolved by main().

# Request contract: VERIFIED FROM DOCUMENTATION (not yet observed in a live call).
# Source: https://serpapi.com/google-maps-contributor-reviews-api
# The Call #1/#2 raw responses contain no SerpApi link to this engine, so the
# engine and parameter names come from the official documentation. Both are
# still checked before search(); if either is unset the script fails locally.
# No optional parameters (sorting, pagination, language) are added.
CONTRIBUTOR_REVIEWS_ENGINE: str | None = "google_maps_contributor_reviews"
CONTRIBUTOR_ID_PARAM: str | None = "contributor_id"

MAX_HISTORY_ENTRIES = 5
NOT_ESTABLISHED = "NOT ESTABLISHED"
REDACTED_CONTRIBUTOR_ID = "<contributor_id>"

# UNVERIFIED: no Contributor Reviews response has been captured, so no field
# name is mapped to these labels. They are identified manually from the keys
# printed for each entry.
HISTORY_ENTRY_LABELS = [
    "Review ID",
    "Rating",
    "Date",
    "ISO date",
    "Review text/snippet",
    "Business/place title presence",
    "Business/place category/type",
    "Business/place address presence",
    "Business/place GPS coordinates presence",
]


class Call3SetupError(Exception):
    """A local precondition for Call #3 failed. Messages contain no personal data."""


def load_call2_response(path: Path) -> dict[str, Any]:
    """Read and parse the Call #2 sanitized raw response, requiring a JSON object."""
    if not path.is_file():
        raise Call3SetupError(f"Call #2 raw response not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Chained exceptions are suppressed: decode errors can quote file content.
        raise Call3SetupError(
            f"Call #2 raw response could not be read as JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise Call3SetupError(
            f"Call #2 raw response top level is {type(data).__name__}, expected an object"
        )
    return data


def select_contributor_id(response: dict[str, Any]) -> str:
    """Return `user.contributor_id` of the first review that has a usable one.

    Reviews that are not objects, have no `user` object, or have a missing,
    empty, or non-string `contributor_id` are skipped.
    """
    if "reviews" not in response:
        raise Call3SetupError("Call #2 raw response has no 'reviews' collection")
    reviews = response["reviews"]
    if not isinstance(reviews, list):
        raise Call3SetupError(
            f"Call #2 'reviews' is {type(reviews).__name__}, expected a list"
        )
    for review in reviews:
        if not isinstance(review, dict):
            continue
        user = review.get("user")
        if not isinstance(user, dict):
            continue
        contributor_id = user.get("contributor_id")
        if isinstance(contributor_id, str) and contributor_id.strip():
            return contributor_id
    raise Call3SetupError(
        f"No review among {len(reviews)} in the Call #2 response has a non-empty "
        "string user.contributor_id"
    )


def build_params(
    contributor_id: str, engine: str | None, contributor_id_param: str | None
) -> dict[str, str]:
    """Build the single Contributor Reviews request, or fail if the contract is unset."""
    if not engine or not contributor_id_param:
        raise Call3SetupError(
            "Contributor Reviews request contract is not established: the engine "
            "name and contributor-ID parameter name must be set from confirmed "
            "evidence (see CONTRIBUTOR_REVIEWS_ENGINE / CONTRIBUTOR_ID_PARAM)"
        )
    return {"engine": engine, contributor_id_param: contributor_id}


def _print_history_entry(number: int, entry: Any) -> None:
    print()
    print(f"--- History entry {number} ---")
    if not isinstance(entry, dict):
        print(f"Entry is not an object (got {type(entry).__name__})")
        return
    for label in HISTORY_ENTRY_LABELS:
        print(f"{label}: {NOT_ESTABLISHED}")
    print(f"Business/place identifier: {NOT_ESTABLISHED}")
    print("Available history-entry keys:")
    print(list(entry.keys()))

    # Keys only, never values: nested objects may hold names, links, or text.
    nested = {key: value for key, value in entry.items() if isinstance(value, dict)}
    if not nested:
        print("Nested objects: none")
    for key, value in nested.items():
        print(f"Available keys in nested object '{key}':")
        print(list(value.keys()))


def print_summary(response: dict[str, Any], params: dict[str, str]) -> None:
    print("Experiment:")
    print("EXP-000 / Call #3")
    print()
    print("Purpose:")
    print("Contributor Reviews chain validation")
    print()
    print("Contributor selection:")
    print("First review in Call #2 response with a non-empty contributor_id")
    print()
    print("Contributor ID loaded: YES")
    print(f"Cache key prefix: {cache_key(params)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()
    print(f"Top-level JSON keys: {list(response.keys())}")
    print(f"History collection: {NOT_ESTABLISHED} (identify from the collections below)")

    # The history collection's name is unverified, so every top-level list is
    # inspected the same way: entry count plus keys of the first few entries.
    collections = {key: value for key, value in response.items() if isinstance(value, list)}
    if not collections:
        print("Top-level list collections: none")
    for key, entries in collections.items():
        print()
        print(f"=== Top-level list '{key}': {len(entries)} entries ===")
        for number, entry in enumerate(entries[:MAX_HISTORY_ENTRIES], start=1):
            _print_history_entry(number, entry)


def main(call2_path: Path | None = None) -> int:
    # Output may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    try:
        call2_path = call2_path or raw_file("call2_reviews_page1")
        contributor_id = select_contributor_id(load_call2_response(call2_path))
        params = build_params(contributor_id, CONTRIBUTOR_REVIEWS_ENGINE, CONTRIBUTOR_ID_PARAM)
    except (Call3SetupError, LocalInputError) as exc:
        print(f"EXP-000 / Call #3 not run: {exc}", file=sys.stderr)
        return 1

    try:
        response = search(params)
    except SerpApiError as exc:
        # The client never exposes the API key; SerpApi error text could echo
        # the request, so the contributor ID is redacted here too.
        message = str(exc).replace(contributor_id, REDACTED_CONTRIBUTOR_ID)
        print(f"EXP-000 / Call #3 failed: {message}", file=sys.stderr)
        return 1
    print_summary(response, params)
    return 0


if __name__ == "__main__":
    sys.exit(main())
