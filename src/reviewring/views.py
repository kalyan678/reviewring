"""View models: the only data the dashboard page ever receives.

This module is the privacy boundary between the graph and the browser.

- **Real mode** (``names=None``) emits aggregates only: counts, distributions,
  and a grouped network in which reviewers with the same connections are one
  node and businesses carry anonymous labels. No identifier, name, category,
  review ID, source filename, or per-reviewer edge is emitted.
- **Synthetic mode** (``names`` given) emits individual nodes, because every
  node is fictional (see ``reviewring.synthetic``).

The view states what was observed in the captured sample. It contains no score
or probability, and its wording never asserts wrongdoing.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from reviewring.graph import BusinessCoverage, Graph, business_coverage, compute_metrics
from reviewring.indicators import MIN_SHARED_BUSINESSES, OverlapGroup, overlap_groups
from reviewring.loader import RAW_DIR, SavedDataError, load_saved_graph
from reviewring.synthetic import build_synthetic_graph, display_names

REAL = "real"
SYNTHETIC = "synthetic"
INVESTIGATION = "investigation"  # one live investigation; aggregated like REAL

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Real-data network only: a reviewer node is drawn when at least this many
# reviewers share its connection pattern. Smaller sets, including single
# reviewers, are counted in the totals but not drawn. A cautious display
# choice, not a formal anonymity guarantee.
MIN_AGGREGATE_GROUP_SIZE = 5


def _plural(count: int, noun: str) -> str:
    if count == 1:
        return f"1 {noun}"
    return f"{count} {noun}es" if noun.endswith("s") else f"{count} {noun}s"


def _business_labels(
    coverage: list[BusinessCoverage], names: dict[str, str] | None, titles: dict[str, str]
) -> dict[str, str]:
    """Display label per business: fictional name, a title the user chose from
    a public listing, or otherwise an anonymous letter."""
    if names is not None:
        return {row.business_id: names[row.business_id] for row in coverage}
    with_pages = sorted(
        (row for row in coverage if not row.history_only),
        key=lambda row: (-row.reviewers_from_pages, -row.review_pages, row.business_id),
    )
    letters = iter(_LETTERS)
    return {
        row.business_id: titles.get(row.business_id) or f"Business {next(letters, index + 1)}"
        for index, row in enumerate(with_pages)
    }


def _single(values: tuple[int, ...]) -> int | None:
    """A reported value when exactly one was observed; otherwise unknown."""
    return values[0] if len(values) == 1 else None


def _edge_rating(edge: Any) -> int | float | None:
    """The edge's rating when its observations agree on one; otherwise None."""
    ratings = {o.rating for o in edge.observations if o.rating is not None}
    return ratings.pop() if len(ratings) == 1 else None


def _distribution(ratings: list[int | float]) -> list[dict[str, Any]]:
    counts = Counter(ratings)
    return [{"rating": rating, "count": counts[rating]} for rating in sorted(counts, reverse=True)]


def _coverage_section(graph: Graph, coverage: list[BusinessCoverage], labels: dict[str, str]) -> dict[str, Any]:
    rows = []
    for row in coverage:
        if row.history_only:
            continue
        reported = _single(row.reported_review_totals)
        rows.append(
            {
                "label": labels[row.business_id],
                "pages": row.review_pages,
                "captured": row.reviewers_from_pages,
                "reported_total": reported,
                "not_collected": max(reported - row.reviewers_from_pages, 0) if reported is not None else None,
                "further_pages": row.unfollowed_page_tokens > 0,
            }
        )
    rows.sort(key=lambda item: item["label"])
    reviewers = len(graph.reviewer_ids)
    return {
        "businesses_with_pages": len(rows),
        "history_only_businesses": sum(1 for row in coverage if row.history_only),
        "reviewers": reviewers,
        "reviewers_with_history": len(graph.histories),
        "business_rows": rows,
    }


def _ratings_section(graph: Graph, coverage: list[BusinessCoverage], labels: dict[str, str], groups: list[OverlapGroup]) -> dict[str, Any]:
    rated = {pair: _edge_rating(edge) for pair, edge in graph.edges.items()}
    known = [rating for rating in rated.values() if rating is not None]
    businesses = []
    for row in coverage:
        if row.history_only:
            continue
        reported_ratings = {
            record.reported_rating
            for record in graph.business_attributes.get(row.business_id, [])
            if record.reported_rating is not None
        }
        captured = [
            rating
            for (_, business_id), rating in rated.items()
            if business_id == row.business_id and rating is not None
        ]
        businesses.append(
            {
                "label": labels[row.business_id],
                "reported_rating": reported_ratings.pop() if len(reported_ratings) == 1 else None,
                "reported_total": _single(row.reported_review_totals),
                "captured_reviews": len(captured),
                "captured_distribution": _distribution(captured),
            }
        )
    businesses.sort(key=lambda item: item["label"])
    if groups:
        reason = (
            "Not computed in this version. Removing a group's reviews would be a what-if "
            "scenario, not a corrected rating, and it would need most of a business's reviews "
            "to be captured first."
        )
    else:
        reason = (
            "Unavailable: no reviewer group meets the overlap indicator, so there is no group "
            "whose reviews could be set aside, and only a small part of the reviews is captured."
        )
    return {
        "reviews_with_rating": len(known),
        "reviews_without_usable_rating": len(rated) - len(known),
        "distribution": _distribution(known),
        "businesses": businesses,
        "adjusted_rating": {"available": False, "reason": reason},
    }


def _indicator_section(graph: Graph, groups: list[OverlapGroup], names: dict[str, str] | None) -> dict[str, Any]:
    categories = {
        business_id: {record.category for record in records if record.category is not None}
        for business_id, records in graph.business_attributes.items()
    }
    rows = []
    for index, group in enumerate(groups, start=1):
        row: dict[str, Any] = {
            "label": f"Group {index}",
            "reviewers": len(group.reviewers),
            "shared_businesses": len(group.businesses),
            "pairs": group.pairs,
            "distinct_categories": len(set().union(*(categories.get(b, set()) for b in group.businesses))),
            "histories_fetched": sum(1 for reviewer_id in group.reviewers if reviewer_id in graph.histories),
        }
        if names is not None:
            row["members"] = [names[reviewer_id] for reviewer_id in group.reviewers]
            row["businesses"] = [names[business_id] for business_id in group.businesses]
            row["node_ids"] = list(group.reviewers) + list(group.businesses)
        rows.append(row)
    return {
        "minimum_shared_businesses": MIN_SHARED_BUSINESSES,
        "pairs": sum(group.pairs for group in groups),
        "groups": rows,
    }


def _detailed_network(graph: Graph, coverage: list[BusinessCoverage], groups: list[OverlapGroup], names: dict[str, str]) -> dict[str, Any]:
    """One node per reviewer and business. Synthetic data only."""
    group_of = {
        node_id: index
        for index, group in enumerate(groups, start=1)
        for node_id in group.reviewers
    }
    shared_by_group = {business_id for group in groups for business_id in group.businesses}
    degree = Counter(reviewer_id for reviewer_id, _ in graph.edges)
    nodes = []
    for reviewer_id in sorted(graph.reviewer_ids, key=lambda r: (group_of.get(r, len(groups) + 1), r)):
        fetched = reviewer_id in graph.histories
        nodes.append(
            {
                "id": reviewer_id,
                "kind": "reviewer",
                "label": names[reviewer_id],
                "count": 1,
                "group": group_of.get(reviewer_id),
                "incomplete": not fetched,
                "detail": [
                    f"Reviewed {_plural(degree[reviewer_id], 'business')} in the collected data.",
                    "Review history collected." if fetched else "Review history not collected: other reviews are unknown.",
                ],
            }
        )
    for row in sorted(coverage, key=lambda c: (c.business_id not in shared_by_group, c.business_id)):
        category = next(
            (record.category for record in graph.business_attributes.get(row.business_id, []) if record.category),
            None,
        )
        reported = _single(row.reported_review_totals)
        if row.history_only:
            coverage_text = "No review page collected: seen only in reviewer histories, so its other reviewers are unknown."
        elif reported is not None and reported > row.reviewers_from_pages:
            coverage_text = f"{row.reviewers_from_pages} of {reported} reported reviews collected; the other reviewers are unknown."
        else:
            coverage_text = f"All {row.reviewers_from_pages} reported reviews collected."
        complete = not row.history_only and reported is not None and reported <= row.reviewers_from_pages
        nodes.append(
            {
                "id": row.business_id,
                "kind": "business",
                "label": names[row.business_id],
                "count": 1,
                "group": None,
                "incomplete": not complete,
                "detail": [f"Category: {category}." if category else "Category not reported.", coverage_text],
            }
        )
    links = [{"source": r, "target": b, "count": 1} for r, b in sorted(graph.edges)]
    return {"aggregated": False, "nodes": nodes, "links": links}


def _aggregated_network(graph: Graph, coverage: list[BusinessCoverage], groups: list[OverlapGroup], labels: dict[str, str]) -> dict[str, Any]:
    """Reviewers with identical connections merged into one node. Safe for real data."""
    node_of: dict[str, str] = {}
    nodes = []
    with_pages = sorted((row for row in coverage if not row.history_only), key=lambda row: labels[row.business_id])
    for index, row in enumerate(with_pages, start=1):
        node_of[row.business_id] = f"business-{index}"
        reported = _single(row.reported_review_totals)
        detail = [f"{_plural(row.review_pages, 'review page')} collected."]
        if reported is None:
            detail.append(f"{_plural(row.reviewers_from_pages, 'review')} collected; reported total unknown.")
        elif reported > row.reviewers_from_pages:
            detail.append(
                f"{row.reviewers_from_pages} of {reported} reported reviews collected; "
                f"{reported - row.reviewers_from_pages} not collected."
            )
        else:
            detail.append(f"All {reported} reported reviews collected.")
        nodes.append(
            {
                "id": node_of[row.business_id],
                "kind": "business",
                "label": labels[row.business_id],
                "count": 1,
                "group": None,
                "incomplete": reported is None or reported > row.reviewers_from_pages,
                "detail": detail,
            }
        )
    history_only = [row.business_id for row in coverage if row.history_only]
    if history_only:
        for business_id in history_only:
            node_of[business_id] = "business-history-only"
        nodes.append(
            {
                "id": "business-history-only",
                "kind": "business",
                "label": f"{_plural(len(history_only), 'business')} (history only)",
                "count": len(history_only),
                "group": None,
                "incomplete": True,
                "detail": ["No review page collected for these, so their other reviewers are unknown."],
            }
        )

    flagged = {reviewer_id for group in groups for reviewer_id in group.reviewers}
    # Reviewers are merged when they connect to the same business nodes; members
    # of a node are indistinguishable in the view.
    members: dict[tuple[tuple[str, ...], bool], list[str]] = {}
    for reviewer_id in graph.reviewer_ids:
        targets = tuple(sorted({node_of[b] for r, b in graph.edges if r == reviewer_id}))
        members.setdefault((targets, reviewer_id in flagged), []).append(reviewer_id)
    links: Counter[tuple[str, str]] = Counter()
    suppressed_reviewers = suppressed_links = 0
    ordered = sorted(members.items(), key=lambda item: (-len(item[1]), item[0]))
    for index, ((targets, in_group), reviewer_ids) in enumerate(ordered, start=1):
        edge_targets = [node_of[b] for r, b in graph.edges if r in reviewer_ids]
        if len(reviewer_ids) < MIN_AGGREGATE_GROUP_SIZE:
            # A connection pattern held by only a few reviewers would single
            # them out, so neither the node nor its links are emitted.
            suppressed_reviewers += len(reviewer_ids)
            suppressed_links += len(edge_targets)
            continue
        node_id = f"reviewers-{index}"
        links.update((node_id, target) for target in edge_targets)
        with_history = sum(1 for reviewer_id in reviewer_ids if reviewer_id in graph.histories)
        nodes.append(
            {
                "id": node_id,
                "kind": "reviewer",
                "label": _plural(len(reviewer_ids), "reviewer"),
                "count": len(reviewer_ids),
                "group": 1 if in_group else None,
                "incomplete": with_history < len(reviewer_ids),
                "detail": [
                    f"Review history collected for all {with_history}."
                    if with_history == len(reviewer_ids)
                    else f"Review history collected for {with_history} of {len(reviewer_ids)}; "
                    "what else the others reviewed is unknown.",
                    "Part of a group meeting the overlap indicator." if in_group else "Not part of any group meeting the overlap indicator.",
                ],
            }
        )
    nodes.sort(key=lambda node: node["kind"] != "reviewer")
    return {
        "aggregated": True,
        "nodes": nodes,
        "links": [{"source": s, "target": t, "count": count} for (s, t), count in sorted(links.items())],
        "suppressed": {
            "minimum_group_size": MIN_AGGREGATE_GROUP_SIZE,
            "reviewers": suppressed_reviewers,
            "links": suppressed_links,
        },
    }


def _explanation(mode: str, metrics: Any, coverage: dict[str, Any], indicators: dict[str, Any]) -> dict[str, Any]:
    minimum = indicators["minimum_shared_businesses"]
    history_gap = coverage["reviewers"] - coverage["reviewers_with_history"]
    compared = coverage["reviewers_with_history"]
    if indicators["groups"]:
        headline = (
            f"{_plural(len(indicators['groups']), 'reviewer group')} "
            f"{'shares' if len(indicators['groups']) == 1 else 'share'} {minimum} or more businesses "
            f"({_plural(indicators['pairs'], 'reviewer pair')})."
        )
    elif compared < 2:
        # Nothing could be compared: an absence of overlap here is not a result.
        headline = "Coordination cannot be assessed from this data."
    elif history_gap:
        headline = (
            f"Coordination can be assessed for only {compared} of {coverage['reviewers']} "
            f"reviewers. No overlap was found among those {compared}."
        )
    else:
        headline = f"No reviewer pair shares {minimum} or more businesses."
    points = [
        f"{metrics.reviewer_pairs_sharing_a_business} reviewer pairs share at least one business. "
        "That is expected: reviewers were collected from business review pages, so everyone on "
        "a page shares that business.",
        f"Review histories are collected for {coverage['reviewers_with_history']} of "
        f"{coverage['reviewers']} reviewers. For the other {history_gap}, only the business they "
        "were found on is known, so overlap among them cannot be seen yet.",
    ]
    if indicators["groups"]:
        caution = (
            "Sharing several unrelated businesses is a structural pattern worth a closer look. "
            "It is not proof of fraud or of any arrangement between reviewers: neighbours, "
            "colleagues, or people following the same recommendations produce it too."
        )
        if mode == SYNTHETIC:
            caution += (
                " One group in this demo is exactly that everyday case, two reviewers who both "
                "used a cafe and a grocer."
            )
        points.append(caution)
    else:
        points.append(
            "This is a small sample and shows no coordination signal. It neither supports nor "
            "rules out coordinated reviewing; it only shows that the collection chain works."
        )
    if mode == REAL and metrics.connected_components > 1:
        points.append(
            f"This view combines everything saved on this computer. It holds "
            f"{metrics.connected_components} separate sets of businesses and reviewers with no "
            "reviewer in common. They come from separate collections and are not one investigation."
        )

    # Overlap is only observable between reviewers whose histories were collected.
    if compared < 2:
        assessment = {
            "assessable": False,
            "statement": (
                "Coordination cannot be assessed from this data. Overlap can only be seen between "
                f"reviewers whose review histories were collected, and {compared} "
                f"{'was' if compared == 1 else 'were'}."
            ),
        }
    elif indicators["groups"]:
        assessment = {
            "assessable": True,
            "statement": (
                f"Among the {compared} reviewers with a collected history, "
                f"{_plural(indicators['pairs'], 'pair')} "
                f"{'shares' if indicators['pairs'] == 1 else 'share'} {minimum} or more businesses. That is "
                "a pattern to look into, not a finding of coordination."
            ),
        }
    else:
        assessment = {
            "assessable": True,
            "statement": (
                f"No overlap was found among the {compared} reviewers with a collected history. "
                f"The other {history_gap} could not be compared, so this does not rule "
                "coordination out."
            ),
        }
    return {"headline": headline, "points": points, "assessment": assessment}


def build_view(
    graph: Graph,
    mode: str,
    names: dict[str, str] | None = None,
    business_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Everything the dashboard shows for one graph.

    ``names`` (a label for every node) must be None for real data.
    ``business_titles`` may name businesses the user picked from a public
    listing; it never names a reviewer, and other businesses stay anonymous.
    """
    metrics = compute_metrics(graph)
    coverage_rows = business_coverage(graph)
    labels = _business_labels(coverage_rows, names, business_titles or {})
    groups = overlap_groups(graph)
    coverage = _coverage_section(graph, coverage_rows, labels)
    indicators = _indicator_section(graph, groups, names)
    network = (
        _aggregated_network(graph, coverage_rows, groups, labels)
        if names is None
        else _detailed_network(graph, coverage_rows, groups, names)
    )
    return {
        "mode": mode,
        "available": True,
        "synthetic": mode == SYNTHETIC,
        "metrics": {
            "reviewers": metrics.reviewers,
            "businesses": metrics.businesses,
            "edges": metrics.edges,
            "observations": metrics.observations,
            "connected_components": metrics.connected_components,
            "pairs_sharing_one": metrics.reviewer_pairs_sharing_a_business,
            "pairs_sharing_two": metrics.reviewer_pairs_sharing_two_or_more,
            "reviewer_degrees": [{"businesses": d, "reviewers": n} for d, n in metrics.reviewer_degrees.items()],
            "business_degrees": [{"reviewers": d, "businesses": n} for d, n in metrics.business_degrees.items()],
        },
        "explanation": _explanation(mode, metrics, coverage, indicators),
        "coverage": coverage,
        "indicators": indicators,
        "ratings": _ratings_section(graph, coverage_rows, labels, groups),
        "network": network,
    }


def real_view(raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Aggregate view of the locally saved responses, or an explicit unavailable state."""
    try:
        graph, report = load_saved_graph(raw_dir)
    except SavedDataError as exc:
        return {"mode": REAL, "available": False, "synthetic": False, "reason": str(exc)}
    view = build_view(graph, REAL)
    view["sources"] = {
        "responses_used": report.responses_used,
        "unusable_files": report.unusable_files,
        "duplicate_responses": report.duplicate_responses,
    }
    return view


def synthetic_view() -> dict[str, Any]:
    """Individual-level view of the fictional demo graph. Reads nothing from disk."""
    return build_view(build_synthetic_graph(), SYNTHETIC, display_names())
