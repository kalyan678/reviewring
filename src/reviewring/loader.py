"""Load the reviewer-business graph from locally saved raw responses.

Offline only: reads ``data/raw/*.json`` and never imports the SerpApi client.
Responses are recognised by the engine in their own ``search_parameters``, not
by filename. Raw filenames embed an unkeyed hash of the request parameters
(which include contributor IDs), so no filename appears in this module, in its
errors, or in ``LoadReport``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from reviewring.graph import Graph, build_graph
from reviewring.parsers import PARSERS, ParseError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"


class SavedDataError(Exception):
    """No usable saved responses. Messages contain no filenames or response content."""


@dataclass(frozen=True)
class LoadReport:
    """Counts describing what was read. Holds no filenames or identifiers."""

    files_found: int
    responses_used: int
    files_without_reviewer_data: int  # e.g. Google Maps Search responses
    duplicate_responses: int  # same request saved more than once
    unusable_files: int  # unreadable, malformed, or unattributable


def load_saved_graph(raw_dir: Path = RAW_DIR) -> tuple[Graph, LoadReport]:
    """Parse every saved Maps Reviews / Contributor Reviews response into one graph."""
    if not raw_dir.is_dir():
        raise SavedDataError("The saved-responses directory does not exist.")
    paths = sorted(raw_dir.glob("*.json"))
    parsed, seen_requests = [], set()
    without_reviewer_data = duplicates = unusable = 0
    for path in paths:
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            unusable += 1
            continue
        params = response.get("search_parameters") if isinstance(response, dict) else None
        engine = params.get("engine") if isinstance(params, dict) else None
        if not isinstance(engine, str):
            unusable += 1
            continue
        if engine not in PARSERS:
            without_reviewer_data += 1
            continue
        # The same request saved twice would double every observation.
        request = json.dumps(params, sort_keys=True, default=str)
        if request in seen_requests:
            duplicates += 1
            continue
        seen_requests.add(request)
        try:
            parsed.append(PARSERS[engine](response, path.name))
        except ParseError:
            unusable += 1
    if not parsed:
        raise SavedDataError(
            "No usable saved Google Maps Reviews or Contributor Reviews responses were found."
        )
    report = LoadReport(
        files_found=len(paths),
        responses_used=len(parsed),
        files_without_reviewer_data=without_reviewer_data,
        duplicate_responses=duplicates,
        unusable_files=unusable,
    )
    return build_graph(parsed), report
