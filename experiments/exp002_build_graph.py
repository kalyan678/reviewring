"""EXP-002: build the reviewer-business graph from the six saved responses.

Offline only. Reads the sanitized raw responses of Calls #1-#6 from data/raw/,
parses the Google Maps Reviews and Contributor Reviews responses, merges them
into one bipartite graph, and prints aggregate metrics. It never imports or
calls the SerpApi client and makes no network request.

Only counts are printed: no reviewer or business identifier, name, category,
or position. The aggregate report needs no pseudonyms, so it does not require
REVIEWRING_PSEUDONYM_KEY.

The metrics describe the captured sample, not reviewer behaviour. Reviewers
were sampled from business review pages, so pairs of reviewers sharing that
business exist by construction and are not a coordination signal.

Run from the project root:
    .venv/Scripts/python experiments/exp002_build_graph.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# pyproject.toml has no build backend yet, so the src/ package is not installed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reviewring.graph import Graph, build_graph, business_coverage, compute_metrics  # noqa: E402
from reviewring.models import CONTRIBUTOR_REVIEWS_ENGINE, MAPS_REVIEWS_ENGINE  # noqa: E402
from reviewring.local_inputs import LocalInputError, raw_file  # noqa: E402
from reviewring.parsers import PARSERS, ParseError  # noqa: E402

RAW_DIR = PROJECT_ROOT / "data" / "raw"
MAPS_SEARCH_ENGINE = "google_maps"

# The six responses of Calls #1-#6 and the engine each must hold. Their filenames
# are private local inputs (local_inputs.json): a saved-response name embeds an
# unkeyed hash of its request, so the names are not part of the repository.
RAW_FILE_ENGINES = {
    "call1_search": MAPS_SEARCH_ENGINE,
    "call2_reviews_page1": MAPS_REVIEWS_ENGINE,
    "call3_contributor_history": CONTRIBUTOR_REVIEWS_ENGINE,
    "call4_reviews_page2": MAPS_REVIEWS_ENGINE,
    "call5_contributor_history": CONTRIBUTOR_REVIEWS_ENGINE,
    "call6_business_reviews": MAPS_REVIEWS_ENGINE,
}
RAW_FILES: dict[str, str] = {}  # filename -> engine; filled by main()


class GraphInputError(Exception):
    """A saved response is missing or unusable. Messages contain no response content."""


def load_raw_response(raw_dir: Path, name: str, engine: str) -> dict[str, Any]:
    """Read one expected raw response, checking its location, shape, and engine."""
    path = raw_dir / name
    if path.resolve().parent != raw_dir.resolve() or not path.is_file():
        raise GraphInputError(f"Raw response not found in {raw_dir}: {name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Chained exceptions are suppressed: decode errors can quote file content.
        raise GraphInputError(
            f"Raw response could not be read as JSON ({type(exc).__name__}): {name}"
        ) from None
    if not isinstance(data, dict):
        raise GraphInputError(f"Raw response top level is {type(data).__name__}: {name}")
    search_parameters = data.get("search_parameters")
    if not isinstance(search_parameters, dict) or search_parameters.get("engine") != engine:
        raise GraphInputError(f"Raw response is not a '{engine}' response: {name}")
    return data


def load_graph(raw_dir: Path = RAW_DIR) -> tuple[Graph, dict[str, int]]:
    """Build the graph from the expected raw files. Returns it with a per-engine file count."""
    parsed, files_by_engine = [], {}
    for name, engine in RAW_FILES.items():
        response = load_raw_response(raw_dir, name, engine)
        files_by_engine[engine] = files_by_engine.get(engine, 0) + 1
        # Google Maps Search lists businesses, not reviewers: it adds no edge.
        if engine in PARSERS:
            try:
                parsed.append(PARSERS[engine](response, name))
            except ParseError as exc:
                raise GraphInputError(f"{exc}: {name}") from None
    return build_graph(parsed), files_by_engine


def _distribution(degrees: dict[int, int], unit: str, of: str) -> None:
    for degree, count in degrees.items():
        print(f"  {degree} {unit}: {count} {of}")


def print_summary(graph: Graph, files_by_engine: dict[str, int]) -> None:
    metrics = compute_metrics(graph)
    print("ReviewRing graph foundation: aggregate metrics (captured sample only)")
    print()
    print("Source responses read:")
    for engine, count in files_by_engine.items():
        note = " (no reviewer data; adds no edge)" if engine not in PARSERS else ""
        print(f"  {engine}: {count}{note}")
    print(f"Entries skipped while parsing: {sum(graph.skipped.values())}")
    for reason, count in sorted(graph.skipped.items()):
        print(f"  {reason}: {count}")
    print()
    print(f"Reviewers: {metrics.reviewers}")
    print(f"Businesses: {metrics.businesses}")
    print(f"Unique reviewer-business edges: {metrics.edges}")
    print(f"Review observations merged into those edges: {metrics.observations}")
    print(f"Edges observed more than once: {metrics.edges_observed_more_than_once}")
    print(f"Connected components: {metrics.connected_components}")
    print()
    print("Reviewer degree distribution:")
    _distribution(metrics.reviewer_degrees, "business(es)", "reviewer(s)")
    print("Business degree distribution:")
    _distribution(metrics.business_degrees, "reviewer(s)", "business(es)")
    print()
    print(f"Reviewer pairs sharing at least one business: {metrics.reviewer_pairs_sharing_a_business}")
    print(f"Reviewer pairs sharing at least two businesses: {metrics.reviewer_pairs_sharing_two_or_more}")
    print()
    print(f"Edges with conflicting observations: {sum(1 for e in graph.edges.values() if e.conflicts)}")
    for kind, count in metrics.conflicts.items():
        print(f"  {kind}: {count}")
    print()

    coverage = business_coverage(graph)
    with_pages = [c for c in coverage if not c.history_only]
    print("Coverage:")
    print(f"  Businesses with at least one Google Maps Reviews page: {len(with_pages)}")
    print(f"  Businesses seen only in contributor histories: {len(coverage) - len(with_pages)}")
    # Rows are ordered by size and carry no identifier.
    for row in sorted(with_pages, key=lambda c: (-c.reviewers_from_pages, -c.review_pages)):
        totals = "/".join(str(total) for total in row.reported_review_totals) or "MISSING"
        print(
            f"    pages captured: {row.review_pages} | reviewers captured: "
            f"{row.reviewers_from_pages} | reported review total: {totals} | "
            f"pagination tokens not followed: {row.unfollowed_page_tokens}"
        )
    print(f"  Reviewers with a contributor history fetched: {len(graph.histories)} of {metrics.reviewers}")
    print(
        "  Contributor histories offering a further page: "
        f"{sum(1 for h in graph.histories.values() for record in h if record.next_token is not None)}"
    )


def main(raw_dir: Path = RAW_DIR) -> int:
    try:
        if not RAW_FILES:
            RAW_FILES.update({raw_file(key).name: engine for key, engine in RAW_FILE_ENGINES.items()})
        graph, files_by_engine = load_graph(raw_dir)
    except (GraphInputError, LocalInputError) as exc:
        print(f"Graph not built: {exc}", file=sys.stderr)
        return 1
    print_summary(graph, files_by_engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
