"""EXP-001 / Call #5: Contributor Reviews for one higher-activity reviewer.

Retrieves the review history of one contributor taken from the Call #4 Google
Maps Reviews page and reports how many distinct businesses it contains. Runs at
most ONE request through the shared cached SerpApi client, which handles the
API key, caching, sanitized raw-response persistence, and errors.

Contributor selection is fixed and deterministic: one position in the Call #4
response, chosen because that reviewer reports more reviews than the others, so
a longer history gives more entries to inspect. No determination is made about
this reviewer. The position and the expected reported review count are private
local inputs (local_inputs.json; see reviewring.local_inputs): a pinned
position points at one person, so it is not part of the repository. The script
re-validates the selection (reported review count is the expected integer,
contributor ID is a non-empty string) and stops without a request if that contributor was
already queried in Call #3. The contributor ID is read from the Call #4
sanitized raw response at runtime; it is never hard-coded, printed, or included
in error messages.

Schema status:
- OBSERVED in Calls #2/#4: `search_parameters.data_id`; `reviews` whose `user`
  objects hold `contributor_id` (string) and `reviews` (integer).
- OBSERVED in Call #3 (one history entry from one contributor): request
  parameters `engine`, `contributor_id`; top-level `reviews`; entry keys
  `place_info`, `date`, `rating`; `place_info` keys `data_id`, `type`.
- UNVERIFIED: that these keys are present on every history entry. Each is
  checked per entry and reported as present or missing, never assumed.

Only counts and presence flags are printed.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp001_contributor_reviews.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.serpapi_client import RAW_DIR, SerpApiError, cache_key, search  # noqa: E402
from reviewring.local_inputs import LocalInputError, integer, raw_file, text  # noqa: E402

# Saved-response paths are private local inputs (local_inputs.json), resolved by main().

# Seed business selected after Call #1 (docs/EXPERIMENTS.md).
SEED_DATA_ID = ""  # private local input: set by main() from local_inputs.json

# Request contract: OBSERVED in the Call #3 response's `search_parameters`.
CONTRIBUTOR_REVIEWS_ENGINE = "google_maps_contributor_reviews"
CONTRIBUTOR_ID_PARAM = "contributor_id"

# Selection (1-based position in the Call #4 `reviews`, and the review count that
# reviewer reported): private local inputs, set by main() from local_inputs.json.
SELECTED_POSITION = 0
EXPECTED_REPORTED_REVIEWS = 0

REDACTED_CONTRIBUTOR_ID = "<contributor_id>"


class Call5SetupError(Exception):
    """A local precondition for Call #5 failed. Messages contain no personal data."""


def load_raw_response(path: Path, label: str) -> dict[str, Any]:
    """Read and parse a sanitized raw response, requiring a JSON object."""
    if not path.is_file():
        raise Call5SetupError(f"{label} raw response not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Chained exceptions are suppressed: decode errors can quote file content.
        raise Call5SetupError(
            f"{label} raw response could not be read as JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise Call5SetupError(
            f"{label} raw response top level is {type(data).__name__}, expected an object"
        )
    return data


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reviews(response: dict[str, Any], label: str) -> list[Any]:
    reviews = response.get("reviews")
    if not isinstance(reviews, list):
        raise Call5SetupError(f"{label} raw response has no 'reviews' list")
    return reviews


def _contributor_id(review: Any) -> str | None:
    user = review.get("user") if isinstance(review, dict) else None
    contributor_id = user.get("contributor_id") if isinstance(user, dict) else None
    return contributor_id if _valid_id(contributor_id) else None


def select_contributor_id(call4: dict[str, Any]) -> str:
    """Return the contributor ID at SELECTED_POSITION of Call #4 after re-validating it."""
    search_parameters = call4.get("search_parameters")
    if not isinstance(search_parameters, dict) or search_parameters.get("data_id") != SEED_DATA_ID:
        raise Call5SetupError("Call #4 search_parameters.data_id is not the seed business data_id")
    reviews = _reviews(call4, "Call #4")
    if len(reviews) < SELECTED_POSITION:
        raise Call5SetupError(
            f"Call #4 has {len(reviews)} reviews; position {SELECTED_POSITION} does not exist"
        )
    review = reviews[SELECTED_POSITION - 1]
    user = review.get("user") if isinstance(review, dict) else None
    if not isinstance(user, dict):
        raise Call5SetupError(f"Call #4 review at position {SELECTED_POSITION} has no 'user' object")
    reported = user.get("reviews")
    if isinstance(reported, bool) or not isinstance(reported, int) or reported != EXPECTED_REPORTED_REVIEWS:
        raise Call5SetupError(
            f"Call #4 review at position {SELECTED_POSITION}: user.reviews is not the "
            f"integer {EXPECTED_REPORTED_REVIEWS}"
        )
    contributor_id = _contributor_id(review)
    if contributor_id is None:
        raise Call5SetupError(
            f"Call #4 review at position {SELECTED_POSITION}: user.contributor_id is not "
            "a non-empty string"
        )
    return contributor_id


def check_not_already_queried(contributor_id: str, call3: dict[str, Any]) -> None:
    """Fail if Call #3 already requested this contributor, or if that cannot be determined."""
    search_parameters = call3.get("search_parameters")
    queried = (
        search_parameters.get(CONTRIBUTOR_ID_PARAM) if isinstance(search_parameters, dict) else None
    )
    if not _valid_id(queried):
        raise Call5SetupError(
            "Call #3 search_parameters.contributor_id is not a non-empty string; cannot "
            "confirm the selected contributor was not already queried"
        )
    if queried == contributor_id:
        raise Call5SetupError("The selected contributor was already queried in Call #3")


def other_positions(contributor_id: str, reviews: list[Any], skip: int | None = None) -> list[int]:
    """1-based positions in ``reviews`` holding ``contributor_id``, excluding ``skip``."""
    return [
        position
        for position, review in enumerate(reviews, start=1)
        if position != skip and _contributor_id(review) == contributor_id
    ]


def build_params(contributor_id: str) -> dict[str, str]:
    return {"engine": CONTRIBUTOR_REVIEWS_ENGINE, CONTRIBUTOR_ID_PARAM: contributor_id}


def _presence(container: Any, field: str) -> str:
    present = isinstance(container, dict) and container.get(field) is not None
    return "PRESENT" if present else "MISSING"


def _has_next_page_token(response: dict[str, Any]) -> bool:
    pagination = response.get("serpapi_pagination")
    return isinstance(pagination, dict) and _valid_id(pagination.get("next_page_token"))


def print_summary(
    response: dict[str, Any], params: dict[str, str], page1_positions: list[int], page2_positions: list[int]
) -> None:
    print("Experiment:")
    print("EXP-001 / Call #5")
    print()
    print("Purpose:")
    print("Contributor Reviews history for one higher-activity reviewer")
    print()
    print("Contributor selection:")
    print(
        f"Call #4 review at position {SELECTED_POSITION} "
        f"(reported review count {EXPECTED_REPORTED_REVIEWS})"
    )
    print("Contributor ID loaded: YES")
    print(f"Same contributor at Call #2 positions: {page1_positions or 'none'}")
    print(f"Same contributor at other Call #4 positions: {page2_positions or 'none'}")
    print()
    print(f"Cache key prefix: {cache_key(params)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()

    entries = response.get("reviews")
    if not isinstance(entries, list):
        state = "MISSING" if entries is None else f"not a list (got {type(entries).__name__})"
        print(f"'reviews' collection: {state}")
        entries = []
    print(f"Number of history entries: {len(entries)}")
    print()

    print("pos | valid data_id | seed business | rating  | date    | category/type")
    data_ids: list[str] = []
    for position, entry in enumerate(entries, start=1):
        place_info = entry.get("place_info") if isinstance(entry, dict) else None
        data_id = place_info.get("data_id") if isinstance(place_info, dict) else None
        if _valid_id(data_id):
            data_ids.append(data_id)
            valid, is_seed = "YES", "YES" if data_id == SEED_DATA_ID else "NO"
        else:
            # Without an identifier the business cannot be compared with the seed.
            valid, is_seed = "NO", "UNKNOWN"
        print(
            f"{position:>3} | {valid:<13} | {is_seed:<13} | {_presence(entry, 'rating'):<7} | "
            f"{_presence(entry, 'date'):<7} | {_presence(place_info, 'type')}"
        )

    distinct = set(data_ids)
    print()
    print(f"Entries with a valid place_info.data_id: {len(data_ids)} of {len(entries)}")
    print(f"Distinct business data_ids: {len(distinct)}")
    print(f"Seed business appears: {'YES' if SEED_DATA_ID in distinct else 'NO'}")
    print(f"Businesses other than the seed: {len(distinct - {SEED_DATA_ID})}")
    print(f"Further pagination token present: {'YES' if _has_next_page_token(response) else 'NO'}")


def main(
    call2_path: Path | None = None,
    call3_path: Path | None = None,
    call4_path: Path | None = None,
) -> int:
    # Output may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    global SEED_DATA_ID, SELECTED_POSITION, EXPECTED_REPORTED_REVIEWS
    try:
        SEED_DATA_ID = SEED_DATA_ID or text("seed_data_id")
        SELECTED_POSITION = SELECTED_POSITION or integer("call5_selected_position")
        EXPECTED_REPORTED_REVIEWS = EXPECTED_REPORTED_REVIEWS or integer("call5_expected_reported_reviews")
        call2_path = call2_path or raw_file("call2_reviews_page1")
        call3_path = call3_path or raw_file("call3_contributor_history")
        call4_path = call4_path or raw_file("call4_reviews_page2")
        call4 = load_raw_response(call4_path, "Call #4")
        contributor_id = select_contributor_id(call4)
        check_not_already_queried(contributor_id, load_raw_response(call3_path, "Call #3"))
        call2_reviews = _reviews(load_raw_response(call2_path, "Call #2"), "Call #2")
    except (Call5SetupError, LocalInputError) as exc:
        print(f"EXP-001 / Call #5 not run: {exc}", file=sys.stderr)
        return 1
    page1_positions = other_positions(contributor_id, call2_reviews)
    page2_positions = other_positions(contributor_id, call4["reviews"], skip=SELECTED_POSITION)
    params = build_params(contributor_id)

    try:
        response = search(params)
    except SerpApiError as exc:
        # The client never exposes the API key; SerpApi error text could echo
        # the request, so the contributor ID is redacted here too.
        message = str(exc).replace(contributor_id, REDACTED_CONTRIBUTOR_ID)
        print(f"EXP-001 / Call #5 failed: {message}", file=sys.stderr)
        return 1
    print_summary(response, params, page1_positions, page2_positions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
