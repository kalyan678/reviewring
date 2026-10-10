"""EXP-002 / Call #6: first Google Maps Reviews page for one non-seed business.

Takes one business from the Call #5 contributor history, requests its first
page of reviews, and counts how many of its reviewers are among the 18 seed
reviewers captured in Calls #2 and #4. Runs at most ONE request through the
shared cached SerpApi client, which handles the API key, caching, sanitized
raw-response persistence, and errors.

Business selection is deterministic and uses only saved Call #5 data: among
history entries whose `place_info.data_id` is a non-empty string other than the
seed's, the first in history order whose `place_info.type` differs from the
seed entry's `place_info.type`; if no entry qualifies (or the seed's type is
unavailable), the first valid non-seed entry in history order. The selection
makes no determination about the business. Its data_id is read from the Call #5
sanitized raw response at runtime; it is never hard-coded or printed.

Schema status:
- OBSERVED request contract: `engine=google_maps_reviews`, `data_id`, `hl=en`
  (every `reviews_link` in Call #1; Call #2 `search_parameters`).
- OBSERVED in Calls #2/#4: `reviews` whose `user` objects hold `contributor_id`
  (string); `serpapi_pagination.next_page_token`; in Call #2 only, a top-level
  `place_info` object with a `reviews` key.
- OBSERVED in Call #5: `search_parameters.contributor_id`; history entries with
  `place_info.data_id` and `place_info.type`.
- UNVERIFIED: that a different business returns the same schema. Every field
  is checked before use and reported as missing, never assumed.

Only counts and page/position references are printed. One page of reviews is a
sample: finding no shared reviewer on it does not show that none exists, and a
shared reviewer is an observation, not a coordination signal on its own.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp002_business_reviews.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.serpapi_client import RAW_DIR, SerpApiError, cache_key, search  # noqa: E402
from reviewring.local_inputs import LocalInputError, raw_file, text  # noqa: E402

# Saved-response paths are private local inputs (local_inputs.json), resolved by main().

# Seed business selected after Call #1 (docs/EXPERIMENTS.md).
SEED_DATA_ID = ""  # private local input: set by main() from local_inputs.json

# Request contract: OBSERVED in Call #1 `reviews_link` values and Call #2.
ENGINE = "google_maps_reviews"
HL = "en"

RULE_CATEGORY = "first valid non-seed entry whose category differs from the seed's"
RULE_FALLBACK = "first valid non-seed entry (no category distinction available)"
MISSING = "MISSING"
REDACTED_DATA_ID = "<data_id>"


class Call6SetupError(Exception):
    """A local precondition for Call #6 failed. Messages contain no identifiers or personal data."""


def load_raw_response(path: Path, label: str) -> dict[str, Any]:
    """Read and parse a sanitized raw response, requiring a JSON object."""
    if not path.is_file():
        raise Call6SetupError(f"{label} raw response not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Chained exceptions are suppressed: decode errors can quote file content.
        raise Call6SetupError(
            f"{label} raw response could not be read as JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise Call6SetupError(
            f"{label} raw response top level is {type(data).__name__}, expected an object"
        )
    return data


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reviews(response: dict[str, Any], label: str) -> list[Any]:
    reviews = response.get("reviews")
    if not isinstance(reviews, list):
        raise Call6SetupError(f"{label} raw response has no 'reviews' list")
    return reviews


def _contributor_id(review: Any) -> str | None:
    user = review.get("user") if isinstance(review, dict) else None
    contributor_id = user.get("contributor_id") if isinstance(user, dict) else None
    return contributor_id if _valid_id(contributor_id) else None


def _place_field(entry: Any, field: str) -> str | None:
    place_info = entry.get("place_info") if isinstance(entry, dict) else None
    value = place_info.get(field) if isinstance(place_info, dict) else None
    return value if _valid_id(value) else None


def select_business(call5: dict[str, Any]) -> tuple[int, str, str]:
    """Return (1-based history position, data_id, rule applied) for the business to expand."""
    entries = _reviews(call5, "Call #5")
    seed_types = {
        _place_field(entry, "type") for entry in entries if _place_field(entry, "data_id") == SEED_DATA_ID
    } - {None}
    candidates = [
        (position, data_id, _place_field(entry, "type"))
        for position, entry in enumerate(entries, start=1)
        if (data_id := _place_field(entry, "data_id")) is not None and data_id != SEED_DATA_ID
    ]
    if not candidates:
        raise Call6SetupError(
            f"No entry among {len(entries)} in the Call #5 history has a non-empty string "
            "place_info.data_id other than the seed business"
        )
    if seed_types:
        for position, data_id, category in candidates:
            if category is not None and category not in seed_types:
                return position, data_id, RULE_CATEGORY
    position, data_id, _ = candidates[0]
    return position, data_id, RULE_FALLBACK


def seed_reviewer_refs(pages: list[tuple[int, list[Any]]]) -> dict[str, list[str]]:
    """Map each seed reviewer's contributor ID to its page/position references."""
    refs: dict[str, list[str]] = {}
    for page, reviews in pages:
        for position, review in enumerate(reviews, start=1):
            contributor_id = _contributor_id(review)
            if contributor_id is not None:
                refs.setdefault(contributor_id, []).append(f"page {page}/position {position}")
    return refs


def call5_contributor_id(call5: dict[str, Any]) -> str:
    search_parameters = call5.get("search_parameters")
    contributor_id = (
        search_parameters.get("contributor_id") if isinstance(search_parameters, dict) else None
    )
    if not _valid_id(contributor_id):
        raise Call6SetupError("Call #5 search_parameters.contributor_id is not a non-empty string")
    return contributor_id


def build_params(data_id: str) -> dict[str, str]:
    return {"engine": ENGINE, "data_id": data_id, "hl": HL}


def _reported_review_count(response: dict[str, Any]) -> int | str:
    """The business's reported review total. Non-integer values are never echoed."""
    place_info = response.get("place_info")
    value = place_info.get("reviews") if isinstance(place_info, dict) else None
    if value is None:
        return MISSING
    if isinstance(value, bool) or not isinstance(value, int):
        return f"MALFORMED ({type(value).__name__})"
    return value


def _has_next_page_token(response: dict[str, Any]) -> bool:
    pagination = response.get("serpapi_pagination")
    return isinstance(pagination, dict) and _valid_id(pagination.get("next_page_token"))


def print_summary(
    response: dict[str, Any],
    params: dict[str, str],
    position: int,
    rule: str,
    seed_refs: dict[str, list[str]],
    expanded_contributor_id: str,
) -> None:
    print("Experiment:")
    print("EXP-002 / Call #6")
    print()
    print("Purpose:")
    print("First Google Maps Reviews page for one non-seed business from the Call #5 history")
    print()
    print(f"Selected business: Call #5 history position {position}")
    print(f"Selection rule applied: {rule}")
    print("Business data_id loaded: YES")
    print(f"Seed reviewers with a valid contributor ID (Calls #2 and #4): {len(seed_refs)}")
    print()
    print(f"Cache key prefix: {cache_key(params)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()

    reviews = response.get("reviews")
    if not isinstance(reviews, list):
        state = MISSING if reviews is None else f"not a list (got {type(reviews).__name__})"
        print(f"'reviews' collection: {state}")
        reviews = []
    positions: dict[str, list[int]] = {}
    for review_position, review in enumerate(reviews, start=1):
        contributor_id = _contributor_id(review)
        if contributor_id is not None:
            positions.setdefault(contributor_id, []).append(review_position)
    overlap = [contributor_id for contributor_id in positions if contributor_id in seed_refs]

    print(f"Business reported review count: {_reported_review_count(response)}")
    print(f"Number of reviews in this response: {len(reviews)}")
    print(f"Valid contributor IDs: {sum(len(found) for found in positions.values())} of {len(reviews)}")
    print(f"Distinct contributor IDs: {len(positions)}")
    print(f"Contributors also among the seed reviewers: {len(overlap)}")
    for contributor_id in overlap:
        print(
            f"  this response position(s) {positions[contributor_id]} = "
            f"seed {', '.join(seed_refs[contributor_id])}"
        )
    print(
        "Call #5 contributor appears in this response: "
        f"{'YES' if expanded_contributor_id in positions else 'NO'}"
    )
    print(f"Further pagination token present: {'YES' if _has_next_page_token(response) else 'NO'}")


def main(
    call2_path: Path | None = None,
    call4_path: Path | None = None,
    call5_path: Path | None = None,
) -> int:
    # Output may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    global SEED_DATA_ID
    try:
        SEED_DATA_ID = SEED_DATA_ID or text("seed_data_id")
        call2_path = call2_path or raw_file("call2_reviews_page1")
        call4_path = call4_path or raw_file("call4_reviews_page2")
        call5_path = call5_path or raw_file("call5_contributor_history")
        call5 = load_raw_response(call5_path, "Call #5")
        position, data_id, rule = select_business(call5)
        expanded_contributor_id = call5_contributor_id(call5)
        seed_refs = seed_reviewer_refs(
            [
                (1, _reviews(load_raw_response(call2_path, "Call #2"), "Call #2")),
                (2, _reviews(load_raw_response(call4_path, "Call #4"), "Call #4")),
            ]
        )
    except (Call6SetupError, LocalInputError) as exc:
        print(f"EXP-002 / Call #6 not run: {exc}", file=sys.stderr)
        return 1
    params = build_params(data_id)

    try:
        response = search(params)
    except SerpApiError as exc:
        # The client never exposes the API key; SerpApi error text could echo
        # the request, so the business data_id is redacted here too.
        message = str(exc).replace(data_id, REDACTED_DATA_ID)
        print(f"EXP-002 / Call #6 failed: {message}", file=sys.stderr)
        return 1
    print_summary(response, params, position, rule, seed_refs, expanded_contributor_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
