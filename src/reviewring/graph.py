"""Reviewer-business bipartite graph built from parsed responses.

Identity:
- reviewer node = contributor ID; business node = business data_id;
- edge = one (reviewer, business) pair, however many times it was observed.

Merge policy (nothing is overwritten or resolved):
- every observation of a pair is kept on its edge with its source;
- observations sharing a ``review_id`` are one review seen more than once;
- several distinct ``review_id`` values on one pair stay on one edge and are
  flagged, so repeat reviews never inflate the edge count;
- differing ratings or dates for what should be one review are flagged as
  conflicts and left for the caller to interpret.

The graph records who reviewed what in the captured sample. It computes no
coordination signal: two reviewers sharing a business is what sampling
reviewers from one business's review page produces by construction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations

from reviewring.models import (
    CONTRIBUTOR_REVIEWS_DEFAULT_NUM,
    MAPS_REVIEWS_ENGINE,
    BusinessAttributes,
    ContributorHistory,
    ParsedResponse,
    ReviewerAttributes,
    ReviewObservation,
    ReviewsPage,
)
from reviewring.privacy import pseudonym

# Conflict kinds reported by Edge.conflicts.
MULTIPLE_REVIEW_IDS = "multiple_review_ids"
RATING_CONFLICT = "rating"
ISO_DATE_CONFLICT = "iso_date"
# Relative date text varies with fetch time and response language, so a
# difference here is weak evidence; it is reported separately for that reason.
DATE_TEXT_DIFFERENCE = "date_text"

_COMPARED_FIELDS = (
    (RATING_CONFLICT, "rating"),
    (ISO_DATE_CONFLICT, "iso_date"),
    (DATE_TEXT_DIFFERENCE, "date_text"),
)


@dataclass(frozen=True)
class Edge:
    """One reviewer-business pair with every observation of it."""

    reviewer_id: str
    business_id: str
    observations: tuple[ReviewObservation, ...]

    @property
    def review_ids(self) -> frozenset[str]:
        return frozenset(o.review_id for o in self.observations if o.review_id is not None)

    @property
    def conflicts(self) -> tuple[str, ...]:
        """Kinds of disagreement among this edge's observations, sorted."""
        review_ids = self.review_ids
        kinds = set()
        if len(review_ids) > 1:
            kinds.add(MULTIPLE_REVIEW_IDS)
            # Different reviews may legitimately differ, so only observations of
            # the same review are compared; those without an ID are ambiguous
            # here and are left uncompared.
            groups = [[o for o in self.observations if o.review_id == rid] for rid in review_ids]
        else:
            groups = [list(self.observations)]
        for group in groups:
            for kind, name in _COMPARED_FIELDS:
                if len({getattr(o, name) for o in group} - {None}) > 1:
                    kinds.add(kind)
        return tuple(sorted(kinds))


@dataclass
class Graph:
    edges: dict[tuple[str, str], Edge] = field(default_factory=dict)
    # Attribute and coverage records are kept per source, never merged.
    reviewer_attributes: dict[str, list[ReviewerAttributes]] = field(default_factory=dict)
    business_attributes: dict[str, list[BusinessAttributes]] = field(default_factory=dict)
    pages: dict[str, list[ReviewsPage]] = field(default_factory=dict)
    histories: dict[str, list[ContributorHistory]] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def reviewer_ids(self) -> set[str]:
        return {reviewer_id for reviewer_id, _ in self.edges} | set(self.histories)

    @property
    def business_ids(self) -> set[str]:
        return {business_id for _, business_id in self.edges} | set(self.pages)


def build_graph(responses: Iterable[ParsedResponse]) -> Graph:
    """Merge parsed responses into one graph. Each source file may be given only once."""
    graph = Graph()
    observations: dict[tuple[str, str], list[ReviewObservation]] = {}
    seen_files: set[str] = set()
    for parsed in responses:
        if parsed.source_file in seen_files:
            raise ValueError(f"Source file given more than once: {parsed.source_file}")
        seen_files.add(parsed.source_file)
        for observation in parsed.observations:
            pair = (observation.reviewer_id, observation.business_id)
            observations.setdefault(pair, []).append(observation)
        for reviewer in parsed.reviewer_attributes:
            graph.reviewer_attributes.setdefault(reviewer.reviewer_id, []).append(reviewer)
        for business in parsed.business_attributes:
            graph.business_attributes.setdefault(business.business_id, []).append(business)
        if parsed.page is not None:
            graph.pages.setdefault(parsed.page.business_id, []).append(parsed.page)
        if parsed.history is not None:
            graph.histories.setdefault(parsed.history.reviewer_id, []).append(parsed.history)
        graph.skipped.update(parsed.skipped)
    graph.edges = {
        pair: Edge(pair[0], pair[1], tuple(found)) for pair, found in observations.items()
    }
    return graph


@dataclass(frozen=True)
class GraphMetrics:
    """Aggregate, identity-free measures of a graph."""

    reviewers: int
    businesses: int
    edges: int
    observations: int
    connected_components: int
    reviewer_degrees: dict[int, int]  # number of businesses -> number of reviewers
    business_degrees: dict[int, int]  # number of reviewers -> number of businesses
    reviewer_pairs_sharing_a_business: int
    reviewer_pairs_sharing_two_or_more: int
    edges_observed_more_than_once: int
    conflicts: dict[str, int]  # conflict kind -> number of edges


def _component_count(nodes: set[tuple[str, str]], links: Iterable[tuple[tuple[str, str], tuple[str, str]]]) -> int:
    parent = {node: node for node in nodes}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in links:
        parent[find(left)] = find(right)
    return len({find(node) for node in nodes})


def compute_metrics(graph: Graph) -> GraphMetrics:
    reviewers_of: dict[str, set[str]] = {business_id: set() for business_id in graph.business_ids}
    businesses_of: dict[str, set[str]] = {reviewer_id: set() for reviewer_id in graph.reviewer_ids}
    for reviewer_id, business_id in graph.edges:
        reviewers_of[business_id].add(reviewer_id)
        businesses_of[reviewer_id].add(business_id)

    # Kind-tagged nodes: a reviewer ID and a business ID can never collide.
    nodes = {("reviewer", r) for r in businesses_of} | {("business", b) for b in reviewers_of}
    links = ((("reviewer", r), ("business", b)) for r, b in graph.edges)

    shared: Counter[tuple[str, str]] = Counter()
    for reviewers in reviewers_of.values():
        shared.update(combinations(sorted(reviewers), 2))

    conflicts: Counter[str] = Counter()
    for edge in graph.edges.values():
        conflicts.update(edge.conflicts)

    return GraphMetrics(
        reviewers=len(businesses_of),
        businesses=len(reviewers_of),
        edges=len(graph.edges),
        observations=sum(len(edge.observations) for edge in graph.edges.values()),
        connected_components=_component_count(nodes, links),
        reviewer_degrees=dict(sorted(Counter(len(b) for b in businesses_of.values()).items())),
        business_degrees=dict(sorted(Counter(len(r) for r in reviewers_of.values()).items())),
        reviewer_pairs_sharing_a_business=len(shared),
        reviewer_pairs_sharing_two_or_more=sum(1 for count in shared.values() if count >= 2),
        edges_observed_more_than_once=sum(
            1 for edge in graph.edges.values() if len(edge.observations) > 1
        ),
        conflicts=dict(sorted(conflicts.items())),
    )


@dataclass(frozen=True)
class BusinessCoverage:
    """How much of one business's reviewer list has been captured."""

    business_id: str
    review_pages: int
    reviewers_from_pages: int
    reported_review_totals: tuple[int, ...]  # distinct reported values; empty = not reported
    unfollowed_page_tokens: int

    @property
    def history_only(self) -> bool:
        """Known only from contributor histories: its other reviewers are unobserved."""
        return self.review_pages == 0


def business_coverage(graph: Graph) -> list[BusinessCoverage]:
    used_tokens = {
        page.request_token for pages in graph.pages.values() for page in pages
    } - {None}
    coverage = []
    for business_id in sorted(graph.business_ids):
        pages = graph.pages.get(business_id, [])
        totals = {
            record.reported_review_total
            for record in graph.business_attributes.get(business_id, [])
            if record.reported_review_total is not None
        }
        coverage.append(
            BusinessCoverage(
                business_id=business_id,
                review_pages=len(pages),
                reviewers_from_pages=sum(
                    1
                    for (_, edge_business), edge in graph.edges.items()
                    if edge_business == business_id
                    and any(o.source.engine == MAPS_REVIEWS_ENGINE for o in edge.observations)
                ),
                reported_review_totals=tuple(sorted(totals)),
                unfollowed_page_tokens=sum(
                    1 for page in pages if page.next_token is not None and page.next_token not in used_tokens
                ),
            )
        )
    return coverage


# How complete a retrieved contributor history appears to be.
HISTORY_PARTIAL = "partial"  # something observed says more exists
HISTORY_APPARENTLY_COMPLETE = "apparently_complete"  # nothing observed says more exists
HISTORY_UNKNOWN = "unknown"  # the window was filled and no total is available

# A reported review total can differ slightly from what a history returns
# (observed: reported one fewer than returned), so a gap of this size is not
# treated as evidence of truncation.
REPORTED_COUNT_TOLERANCE = 1


@dataclass(frozen=True)
class HistoryCoverage:
    """How much of one reviewer's history was retrieved. Never a statement of certainty."""

    reviewer_id: str
    entries_returned: int
    unique_businesses: int
    limit: int  # the requested `num`, or the documented default when none was sent
    limit_reached: bool
    pagination_offered: bool
    reported_total: int | None  # None = not reported anywhere
    status: str


def history_coverage(graph: Graph) -> list[HistoryCoverage]:
    """Coverage of each retrieved history, using the largest window per reviewer.

    A missing next-page marker is NOT treated as completeness: the API returned
    10-entry windows for profiles reporting hundreds of reviews with no marker.
    """
    coverage = []
    for reviewer_id in sorted(graph.histories):
        history = max(graph.histories[reviewer_id], key=lambda h: h.entries_listed)
        reported = history.reported_total
        if reported is None:
            # Fall back to the count shown on a review page, when unambiguous.
            page_counts = {
                record.reported_review_count
                for record in graph.reviewer_attributes.get(reviewer_id, [])
                if record.reported_review_count is not None
            }
            reported = page_counts.pop() if len(page_counts) == 1 else None
        limit = history.requested_limit or CONTRIBUTOR_REVIEWS_DEFAULT_NUM
        limit_reached = history.entries_listed >= limit
        pagination_offered = history.next_token is not None
        more_reported = reported is not None and reported - history.entries_listed > REPORTED_COUNT_TOLERANCE
        if pagination_offered or more_reported:
            status = HISTORY_PARTIAL
        elif limit_reached and reported is None:
            status = HISTORY_UNKNOWN
        else:
            status = HISTORY_APPARENTLY_COMPLETE
        coverage.append(
            HistoryCoverage(
                reviewer_id=reviewer_id,
                entries_returned=history.entries_listed,
                unique_businesses=len(
                    {
                        business_id
                        for (edge_reviewer, business_id), edge in graph.edges.items()
                        if edge_reviewer == reviewer_id
                        and any(o.source.file == history.source_file for o in edge.observations)
                    }
                ),
                limit=limit,
                limit_reached=limit_reached,
                pagination_offered=pagination_offered,
                reported_total=reported,
                status=status,
            )
        )
    return coverage


def pseudonymous_edges(graph: Graph, key: bytes) -> list[dict[str, object]]:
    """Edge list safe to export: keyed pseudonyms in place of raw identifiers.

    Carries no names, text, dates, or source positions. A pseudonymous edge list
    still describes who reviewed what; treat it as sensitive and do not publish
    it alongside anything that identifies a business.
    """
    rows = [
        {
            "reviewer": pseudonym("reviewer", edge.reviewer_id, key),
            "business": pseudonym("business", edge.business_id, key),
            "observations": len(edge.observations),
            "distinct_review_ids": len(edge.review_ids),
            "ratings": sorted({o.rating for o in edge.observations if o.rating is not None}),
            "conflicts": list(edge.conflicts),
        }
        for edge in graph.edges.values()
    ]
    return sorted(rows, key=lambda row: (row["reviewer"], row["business"]))
