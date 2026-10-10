"""Deterministic overlap indicators computed from graph structure alone.

An indicator here is a structural observation: which reviewers share
businesses, and which sets of reviewers are linked by such sharing. It is not a
score, a probability, or a finding about anyone. Reviewers can share businesses
for ordinary reasons (neighbours using the same shops), and the graph is a
partial sample, so absence of overlap is not evidence of absence.

``MIN_SHARED_BUSINESSES`` is the exploratory rule from PROJECT_SPEC.md
Section 14. It is provisional, not a validated threshold.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from reviewring.graph import Graph
from reviewring.models import CONTRIBUTOR_REVIEWS_ENGINE

MIN_SHARED_BUSINESSES = 2


@dataclass(frozen=True)
class SharedPair:
    """Two reviewers and the businesses both have an edge to."""

    reviewers: tuple[str, str]
    businesses: tuple[str, ...]


@dataclass(frozen=True)
class OverlapGroup:
    """Reviewers linked, directly or through others, by pairs sharing enough businesses."""

    reviewers: tuple[str, ...]
    # Businesses reviewed by at least two members of the group.
    businesses: tuple[str, ...]
    pairs: int


def shared_business_pairs(graph: Graph, minimum: int = MIN_SHARED_BUSINESSES) -> list[SharedPair]:
    """Reviewer pairs sharing at least ``minimum`` businesses, in a stable order."""
    reviewers_of: dict[str, set[str]] = {}
    for reviewer_id, business_id in graph.edges:
        reviewers_of.setdefault(business_id, set()).add(reviewer_id)
    shared: dict[tuple[str, str], list[str]] = {}
    for business_id in sorted(reviewers_of):
        for pair in combinations(sorted(reviewers_of[business_id]), 2):
            shared.setdefault(pair, []).append(business_id)
    return [
        SharedPair(pair, tuple(businesses))
        for pair, businesses in sorted(shared.items())
        if len(businesses) >= minimum
    ]


def overlap_groups(graph: Graph, minimum: int = MIN_SHARED_BUSINESSES) -> list[OverlapGroup]:
    """Connected sets of reviewers under the shared-pair relation, largest first."""
    pairs = shared_business_pairs(graph, minimum)
    group_of: dict[str, set[str]] = {}
    for pair in pairs:
        merged = set(pair.reviewers)
        for reviewer_id in pair.reviewers:
            merged |= group_of.get(reviewer_id, set())
        for reviewer_id in merged:
            group_of[reviewer_id] = merged

    groups = []
    for members in {id(group): group for group in group_of.values()}.values():
        reviewed = Counter(
            business_id for reviewer_id, business_id in graph.edges if reviewer_id in members
        )
        groups.append(
            OverlapGroup(
                reviewers=tuple(sorted(members)),
                businesses=tuple(sorted(b for b, count in reviewed.items() if count >= 2)),
                pairs=sum(1 for pair in pairs if pair.reviewers[0] in members),
            )
        )
    return sorted(groups, key=lambda group: (-len(group.reviewers), group.reviewers))


def expansion_candidates(graph: Graph, minimum: int = MIN_SHARED_BUSINESSES) -> list[str]:
    """Businesses whose review page is worth collecting next, in a fixed order.

    A business qualifies when all of these hold:

    - it has no collected review page yet, so it is known only from histories;
    - two reviewers with a retrieved history both list it in that history;
    - those two reviewers are an overlapping pair under the product's own rule:
      they share at least ``minimum`` businesses, counted from observations only.

    A shared business counts toward the pair only where an observation supports
    it for both reviewers (a review page or a history entry); nothing is assumed
    because a reviewer was reached through some business. A business that two
    reviewers share without the pair reaching ``minimum``, or that only one
    retrieved history lists, does not qualify. Qualifying is a reason to look,
    never evidence that reviews were arranged.

    Order: most supporting reviewer pairs first, then by identifier, so the
    choice is deterministic and uses nothing about review content.
    """
    with_history = set(graph.histories)
    paged = set(graph.pages)
    in_history = {
        pair
        for pair, edge in graph.edges.items()
        if pair[0] in with_history
        and any(o.source.engine == CONTRIBUTOR_REVIEWS_ENGINE for o in edge.observations)
    }
    support: Counter[str] = Counter()
    for pair in shared_business_pairs(graph, minimum):
        first, second = pair.reviewers
        if first not in with_history or second not in with_history:
            continue
        for business_id in pair.businesses:
            if business_id not in paged and (first, business_id) in in_history and (second, business_id) in in_history:
                support[business_id] += 1
    return [business_id for business_id, _ in sorted(support.items(), key=lambda item: (-item[1], item[0]))]
