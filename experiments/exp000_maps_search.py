"""EXP-000 / Call #1: seed-market business discovery.

Runs ONE Google Maps Search through the shared cached SerpApi client and prints
a small diagnostic summary of the returned candidates. The client handles the
API key, caching, sanitized raw-response persistence, and errors; this script
only reads the returned JSON. It does not select a seed business.

The search query is a private local input (local_inputs.json; see
reviewring.local_inputs). Nothing here is part of the ReviewRing architecture.

Run from the project root (uses one SerpApi credit unless the response is
already cached):
    .venv/Scripts/python experiments/exp000_maps_search.py
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

QUERY = ""  # private local input: set by main() from local_inputs.json
PARAMS: dict[str, str] = {}

MAX_CANDIDATES = 5
NOT_RETURNED = "NOT RETURNED"

# Labels mapped to the field names we expect from SerpApi's documentation.
# These are unverified assumptions: each candidate's "Available keys" line
# shows the actual names, so a mismatch shows up as NOT RETURNED plus a
# different key in that list. No alternative names are guessed.
CANDIDATE_FIELDS = [
    ("Title", "title"),
    ("Data ID", "data_id"),
    ("Type/category", "type"),
    ("Rating", "rating"),
    ("Review count", "reviews"),
    ("Address", "address"),
    ("GPS coordinates", "gps_coordinates"),
]


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_summary(response: dict[str, Any]) -> None:
    print("Experiment:")
    print("EXP-000 / Call #1")
    print()
    print("Query:")
    print(QUERY)
    print()
    print(f"Cache key prefix: {cache_key(PARAMS)[:16]}")
    print(f"Sanitized raw responses: {RAW_DIR}")
    print()
    print(f"Top-level JSON keys: {list(response.keys())}")

    local_results = response.get("local_results")
    print(f"local_results exists: {'local_results' in response}")
    if local_results is None:
        print("Number of local_results: 0")
        return
    if not isinstance(local_results, list):
        print(f"local_results is not a list (got {type(local_results).__name__}); not inspecting")
        return
    print(f"Number of local_results: {len(local_results)}")

    for number, candidate in enumerate(local_results[:MAX_CANDIDATES], start=1):
        print()
        print(f"--- Candidate {number} ---")
        if not isinstance(candidate, dict):
            print(f"Candidate is not an object (got {type(candidate).__name__})")
            continue
        for label, field in CANDIDATE_FIELDS:
            value = _format_value(candidate[field]) if field in candidate else NOT_RETURNED
            print(f"{label}: {value}")
        print("Available keys:")
        print(list(candidate.keys()))


def main() -> int:
    # Business names may contain non-ASCII characters; never crash on output.
    sys.stdout.reconfigure(errors="replace")
    global QUERY
    try:
        QUERY = text("search_query")
    except LocalInputError as exc:
        print(f"EXP-000 / Call #1 not run: {exc}", file=sys.stderr)
        return 1
    PARAMS.update(engine="google_maps", q=QUERY, type="search")
    try:
        response = search(PARAMS)
    except SerpApiError as exc:
        # The client's error messages never contain the API key or request URL.
        print(f"EXP-000 / Call #1 failed: {exc}", file=sys.stderr)
        return 1
    print_summary(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
