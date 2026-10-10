"""EXP-001 / Call #4: one additional Google Maps Reviews page for the seed business.

Reads the pagination data SerpApi returned in the Call #2 Google Maps Reviews
response, validates it locally, and runs at most ONE request for the next page
through the shared cached SerpApi client, which handles the API key, caching,
sanitized raw-response persistence, and errors.

The next-page token is read from the Call #2 sanitized raw response at runtime;
it is never hard-coded, printed, or included in error messages.

Schema status:
- OBSERVED in Call #2:
  `search_parameters` with `engine`, `data_id`, `hl`; `serpapi_pagination` with
  `next` (a serpapi.com/search.json URL whose query holds `engine`, `data_id`,
  `hl`, `next_page_token`) and `next_page_token`; `reviews` whose `user`
  objects hold `contributor_id`, `reviews`, `photos`, `local_guide`.
- UNVERIFIED until Call #4 is run: whether the saved token is still accepted,
  how many reviews a second page returns, and whether the second page has the
  same schema. Reviewer metadata is therefore type-checked before printing.

Only reviewer metadata counts and flags are printed. This script makes no
determination about any reviewer, review, or business.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp001_maps_reviews_page2.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewring.serpapi_client import RAW_DIR, SerpApiError, cache_key, search  # noqa: E402
from reviewring.local_inputs import LocalInputError, raw_file, text  # noqa: E402

# Saved-response paths are private local inputs (local_inputs.json), resolved by main().

# Seed business selected after Call #1 (docs/EXPERIMENTS.md).
SEED_DATA_ID = ""  # private local input: set by main() from local_inputs.json

# Request contract: OBSERVED in the Call #2 response's `serpapi_pagination.next`.
ENGINE = "google_maps_reviews"
HL = "en"
PAGINATION_SCHEME = "https"
PAGINATION_HOSTNAME = "serpapi.com"
PAGINATION_PATH = "/search.json"
PAGINATION_PARAM_NAMES = ("engine", "data_id", "hl", "next_page_token")

MISSING = "MISSING"
REDACTED_TOKEN = "<next_page_token>"


class Call4SetupError(Exception):
    """A local precondition for Call #4 failed. Messages contain no token, URL, or personal data."""


def load_call2_response(path: Path) -> dict[str, Any]:
    """Read and parse the Call #2 sanitized raw response, requiring a JSON object."""
    if not path.is_file():
        raise Call4SetupError(f"Call #2 raw response not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Chained exceptions are suppressed: decode errors can quote file content.
        raise Call4SetupError(
            f"Call #2 raw response could not be read as JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(data, dict):
        raise Call4SetupError(
            f"Call #2 raw response top level is {type(data).__name__}, expected an object"
        )
    return data


def _saved_data_id(response: dict[str, Any]) -> str:
    search_parameters = response.get("search_parameters")
    if not isinstance(search_parameters, dict):
        raise Call4SetupError("Call #2 raw response has no 'search_parameters' object")
    if search_parameters.get("engine") != ENGINE:
        raise Call4SetupError(f"Call #2 search_parameters.engine is not '{ENGINE}'")
    if search_parameters.get("hl") != HL:
        raise Call4SetupError(f"Call #2 search_parameters.hl is not '{HL}'")
    if search_parameters.get("data_id") != SEED_DATA_ID:
        raise Call4SetupError("Call #2 search_parameters.data_id is not the seed business data_id")
    return SEED_DATA_ID


def _validate_pagination_url(url: Any, expected: dict[str, str]) -> None:
    """Check the saved next-page URL against ``expected`` without ever quoting it."""
    if not isinstance(url, str) or not url.strip():
        raise Call4SetupError("Call #2 serpapi_pagination.next is not a non-empty string")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        raise Call4SetupError("Call #2 serpapi_pagination.next is not a valid URL") from None
    if parts.scheme != PAGINATION_SCHEME:
        raise Call4SetupError(f"Pagination URL scheme is not '{PAGINATION_SCHEME}'")
    if hostname != PAGINATION_HOSTNAME:
        raise Call4SetupError(f"Pagination URL hostname is not '{PAGINATION_HOSTNAME}'")
    if parts.path != PAGINATION_PATH:
        raise Call4SetupError(f"Pagination URL path is not '{PAGINATION_PATH}'")

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    names = [name for name, _ in pairs]
    missing = [name for name in PAGINATION_PARAM_NAMES if name not in names]
    # Unexpected names are counted, not quoted: a malformed URL could carry anything.
    unexpected = sum(1 for name in names if name not in PAGINATION_PARAM_NAMES)
    if missing or unexpected or len(names) != len(set(names)):
        raise Call4SetupError(
            "Pagination URL query parameters do not match the observed contract "
            f"(missing: {missing or 'none'}; unexpected: {unexpected}; "
            f"duplicated: {len(names) - len(set(names))})"
        )
    query = dict(pairs)
    if query["data_id"] != expected["data_id"]:
        raise Call4SetupError("Pagination URL data_id does not match the Call #2 business")
    for name in ("engine", "hl", "next_page_token"):
        if query[name] != expected[name]:
            raise Call4SetupError(f"Pagination URL '{name}' does not match the saved value")


def build_params(response: dict[str, Any]) -> dict[str, str]:
    """Build the single next-page request from the Call #2 response, or fail locally."""
    data_id = _saved_data_id(response)
    pagination = response.get("serpapi_pagination")
    if not isinstance(pagination, dict):
        raise Call4SetupError("Call #2 raw response has no 'serpapi_pagination' object")
    token = pagination.get("next_page_token")
    if not isinstance(token, str) or not token.strip():
        raise Call4SetupError(
            "Call #2 serpapi_pagination.next_page_token is not a non-empty string"
        )
    params = {"engine": ENGINE, "data_id": data_id, "hl": HL, "next_page_token": token}
    _validate_pagination_url(pagination.get("next"), params)
    return params


def _count(user: dict[str, Any], field: str) -> int | str:
    """A reported count, or an explicit marker. Non-integer values are never echoed."""
    value = user.get(field)
    if value is None:
        return MISSING
    if isinstance(value, bool) or not isinstance(value, int):
        return f"MALFORMED ({type(value).__name__})"
    return value


def _flag(user: dict[str, Any], field: str) -> bool | str:
    value = user.get(field)
    if value is None:
        return MISSING
    if not isinstance(value, bool):
        return f"MALFORMED ({type(value).__name__})"
    return value


def _has_next_page_token(response: dict[str, Any]) -> bool:
    pagination = response.get("serpapi_pagination")
    if not isinstance(pagination, dict):
        return False
    token = pagination.get("next_page_token")
    return isinstance(token, str) and bool(token.strip())


def print_summary(response: dict[str, Any], params: dict[str, str]) -> None:
    print("Experiment:")
    print("EXP-001 / Call #4")
    print()
    print("Purpose:")
    print("One additional Google Maps Reviews page for the seed business")
    print()
    print(f"Cache key prefix: {cache_key(params)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()

    reviews = response.get("reviews")
    if not isinstance(reviews, list):
        state = MISSING if reviews is None else f"not a list (got {type(reviews).__name__})"
        print(f"'reviews' collection: {state}")
        reviews = []
    print(f"Number of reviews in this response: {len(reviews)}")
    print()

    print("pos | user.reviews | user.photos | user.local_guide | valid contributor_id")
    review_counts: list[int | str] = []
    valid_ids = 0
    for position, review in enumerate(reviews, start=1):
        user = review.get("user") if isinstance(review, dict) else None
        if not isinstance(user, dict):
            user = {}
        contributor_id = user.get("contributor_id")
        has_valid_id = isinstance(contributor_id, str) and bool(contributor_id.strip())
        valid_ids += has_valid_id
        reported = _count(user, "reviews")
        review_counts.append(reported)
        print(
            f"{position:>3} | {reported!s:>12} | {_count(user, 'photos')!s:>11} | "
            f"{_flag(user, 'local_guide')!s:>16} | {'YES' if has_valid_id else 'NO'}"
        )

    numeric = [count for count in review_counts if isinstance(count, int)]
    distribution = Counter(review_counts)
    print()
    print(f"Valid contributor IDs: {valid_ids} of {len(reviews)}")
    print("Distribution of reported review counts (count: reviewers):")
    for value in sorted(set(numeric)) + sorted(v for v in distribution if isinstance(v, str)):
        print(f"  {value}: {distribution[value]}")
    print(f"Reviewers with reported review count >= 2: {sum(count >= 2 for count in numeric)}")
    print(f"Reviewers with reported review count >= 5: {sum(count >= 5 for count in numeric)}")
    print(f"Further pagination token present: {'YES' if _has_next_page_token(response) else 'NO'}")


def main(call2_path: Path | None = None) -> int:
    # Output may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    global SEED_DATA_ID
    try:
        SEED_DATA_ID = SEED_DATA_ID or text("seed_data_id")
        call2_path = call2_path or raw_file("call2_reviews_page1")
        params = build_params(load_call2_response(call2_path))
    except (Call4SetupError, LocalInputError) as exc:
        print(f"EXP-001 / Call #4 not run: {exc}", file=sys.stderr)
        return 1

    try:
        response = search(params)
    except SerpApiError as exc:
        # The client never exposes the API key; SerpApi error text could echo
        # the request, so the pagination token is redacted here too.
        message = str(exc).replace(params["next_page_token"], REDACTED_TOKEN)
        print(f"EXP-001 / Call #4 failed: {message}", file=sys.stderr)
        return 1
    print_summary(response, params)
    return 0


if __name__ == "__main__":
    sys.exit(main())
