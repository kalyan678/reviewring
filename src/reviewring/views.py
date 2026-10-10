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
from dataclasses import replace
from pathlib import Path
from typing import Any

from reviewring.graph import (
    HISTORY_APPARENTLY_COMPLETE,
    HISTORY_PARTIAL,
    HISTORY_UNKNOWN,
    BusinessCoverage,
    Graph,
    build_graph,
    business_coverage,
    compute_metrics,
    history_coverage,
)
from reviewring.indicators import (
    MAX_EXPANSION_BUSINESSES,
    MIN_SHARED_BUSINESSES,
    OverlapGroup,
    expansion_candidates,
    overlap_groups,
    shared_business_pairs,
)
from reviewring.loader import RAW_DIR, SavedDataError, load_saved_graph
from reviewring.models import ParsedResponse
from reviewring.synthetic import display_names, synthetic_collection

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
    if noun.endswith("y"):
        return f"{count} {noun[:-1]}ies"
    return f"{count} {noun}es" if noun.endswith("s") else f"{count} {noun}s"


def _business_labels(
    coverage: list[BusinessCoverage], names: dict[str, str] | None, titles: dict[str, str], expanded: frozenset[str] = frozenset()
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
    labels, expansions = {}, 0
    for index, row in enumerate(with_pages):
        if row.business_id in expanded:
            # Reached through a small set of reviewers: numbered, never named.
            expansions += 1
            labels[row.business_id] = f"Expanded business {expansions}"
        else:
            labels[row.business_id] = titles.get(row.business_id) or f"Business {next(letters, index + 1)}"
    return labels


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


def _share_band(fraction: float) -> str:
    """A coarse share, so that a few reviewers' reported totals are harder to work back."""
    if fraction < 0.05:
        return "under 5%"
    if fraction < 0.25:
        return "between 5% and 25%"
    if fraction < 0.75:
        return "between 25% and 75%"
    return "over 75%"


def _history_windows(graph: Graph) -> dict[str, Any]:
    """How much of the retrieved reviewers' histories was actually retrieved, in aggregate.

    A retrieved history is a window, not necessarily the whole history. Exact
    reported totals are reviewer-level data, so they are summed and shown only
    when enough histories are involved; otherwise a coarse share is given.
    """
    rows = history_coverage(graph)
    count = len(rows)
    by_status = Counter(row.status for row in rows)
    retrieved = sum(row.entries_returned for row in rows)
    totals_known = count > 0 and all(row.reported_total is not None for row in rows)
    reported = sum(max(row.reported_total, row.entries_returned) for row in rows) if totals_known else None
    share = _share_band(retrieved / reported) if reported else None
    exact = count >= MIN_AGGREGATE_GROUP_SIZE
    limits = sorted({row.limit for row in rows})

    if count == 0:
        statement = "No review histories were retrieved."
    else:
        described = [
            (by_status[HISTORY_PARTIAL], "partial"),
            (by_status[HISTORY_UNKNOWN], "of unknown completeness"),
            (by_status[HISTORY_APPARENTLY_COMPLETE], "apparently complete"),
        ]
        statement = (
            f"{count} review {'history' if count == 1 else 'histories'} retrieved: "
            + ", ".join(f"{number} {label}" for number, label in described if number)
            + "."
        )
        one = count == 1
        if one:
            statement += f" The request asked for at most {limits[0]} entries."
        elif len(limits) == 1:
            statement += f" Each request asked for at most {limits[0]} entries."
        else:
            statement += (
                f" The requests used different entry limits, from {limits[0]} to {limits[-1]} entries per history."
            )
        holds = f" The retrieved {'window holds' if one else 'windows hold'} {_plural(retrieved, 'entry')}"
        whose = "this reviewer reports" if one else "these reviewers report"
        if reported and exact:
            statement += f"{holds} of about {reported} reported ({share})."
        elif share:
            statement += f"{holds}, {share} of the reviews {whose}."
        else:
            statement += f"{holds}; how many reviews {'this reviewer has' if one else 'these reviewers have'} is not known."
        statement += (
            " A history counts as partial when the reviewer reports more reviews than were returned or a "
            "further page is offered. Reported totals are approximate, and the absence of a next-page "
            "marker does not mean a history is complete."
        )
    return {
        "retrieved": count,
        "partial": by_status[HISTORY_PARTIAL],
        "unknown": by_status[HISTORY_UNKNOWN],
        "apparently_complete": by_status[HISTORY_APPARENTLY_COMPLETE],
        "entry_limits": limits,
        "entries_retrieved": retrieved,
        "entries_reported": reported if exact else None,
        "share": share,
        "statement": statement,
    }


def _coverage_section(
    graph: Graph, coverage: list[BusinessCoverage], labels: dict[str, str],
    expanded: frozenset[str] = frozenset(), withheld: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    rows = []
    for row in coverage:
        if row.history_only:
            continue
        reported = _single(row.reported_review_totals)
        rows.append(
            {
                "label": labels[row.business_id],
                "expanded": row.business_id in expanded,
                "withheld": row.business_id in withheld,
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
        "history_windows": _history_windows(graph),
        "business_rows": rows,
    }


def _ratings_section(
    graph: Graph, coverage: list[BusinessCoverage], labels: dict[str, str], groups: list[OverlapGroup],
    expanded: frozenset[str] = frozenset(), withheld: frozenset[str] = frozenset(),
) -> dict[str, Any]:
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
                "expanded": row.business_id in expanded,
                "withheld": row.business_id in withheld,
                "reported_rating": (
                    reported_ratings.pop() if len(reported_ratings) == 1 and row.business_id not in withheld else None
                ),
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


def _size_band(count: int) -> str:
    """A coarse size, so small exact combinations cannot be matched against public pages."""
    if count <= 2:
        return str(count)
    if count <= 4:
        return "3 to 4"
    if count <= 9:
        return "5 to 9"
    return "10 or more"


def _category_spread(count: int) -> str:
    if count == 0:
        return "categories that were not reported"
    if count == 1:
        return "a single category"
    return "2 to 3 categories" if count <= 3 else "4 or more categories"


def _overlap_evidence(
    graph: Graph, groups: list[OverlapGroup], categories: dict[str, set[str]], coverage: dict[str, Any], detailed: bool
) -> dict[str, Any]:
    """What the overlap indicator found, as statements that name no one.

    Real data (``detailed`` false) never gets a member list, a per-reviewer
    link, a named or listed set of shared businesses, or category names. When
    any group has fewer than ``MIN_AGGREGATE_GROUP_SIZE`` reviewers, the number
    of reviewers and how they group are withheld, and business counts are given
    as bands. The result depends only on the shape of the overlap, not on which
    reviewers or businesses are involved.

    This limits disclosure; it does not make a small group anonymous. The pair
    count still indicates how small a set is, and anyone with the same public
    review pages could work out its members (docs/ARCHITECTURE.md).
    """
    pairs = shared_business_pairs(graph)
    involved_reviewers = {reviewer_id for pair in pairs for reviewer_id in pair.reviewers}
    involved_businesses = {business_id for pair in pairs for business_id in pair.businesses}
    category_count = len(set().union(*(categories.get(b, set()) for b in involved_businesses))) if pairs else 0
    strongest = max((len(pair.businesses) for pair in pairs), default=0)
    # Per group, not in total: two small groups must not be summed into a
    # count that reveals their sizes.
    withhold = not detailed and any(len(group.reviewers) < MIN_AGGREGATE_GROUP_SIZE for group in groups)
    windows = coverage["history_windows"]
    limited = windows["partial"] + windows["unknown"]
    minimum = MIN_SHARED_BUSINESSES

    compared = (
        f"Only retrieved data was compared: review histories for {coverage['reviewers_with_history']} of "
        f"{coverage['reviewers']} reviewers"
        + (f", {limited} of them partial or of unknown completeness" if limited else "")
        + "."
    )
    # Overlap between reviewers is only observable through retrieved histories.
    assessed = bool(pairs) or coverage["reviewers_with_history"] >= 2
    if not assessed:
        retrieved = coverage["reviewers_with_history"]
        observed = [
            "Overlap was not assessed. It can only be seen between reviewers whose review histories were "
            f"retrieved, and {'none was' if retrieved == 0 else 'only 1 was'}."
        ]
        limits = ["This is not a finding of no overlap: nothing was compared."]
    elif not pairs:
        observed = [f"No reviewer pair shares {minimum} or more businesses within the retrieved data."]
        limits = [compared + " This is not a finding that no overlap exists."]
    else:
        observed = [
            f"{_plural(len(pairs), 'reviewer pair')} {'shares' if len(pairs) == 1 else 'share'} {minimum} or more "
            "businesses within the retrieved data."
        ]
        if withhold:
            observed.append(
                "At least one group has fewer than 5 reviewers, so how many reviewers are involved, who they "
                "are, and how they group are not shown."
            )
        else:
            observed.append(
                f"{len(involved_reviewers)} reviewers are involved, in {_plural(len(groups), 'group')}."
            )
        shared_total = len(involved_businesses) if detailed else _size_band(len(involved_businesses))
        most = strongest if detailed else _size_band(strongest)
        observed.append(
            f"Across those pairs, {shared_total} businesses are shared; the most any one pair shares is {most}."
        )
        observed.append(f"The shared businesses fall in {_category_spread(category_count)}.")
        limits = [
            "Shared businesses alone do not show that reviews were arranged. Neighbours, colleagues, and people "
            "following the same recommendations share businesses too.",
            compared + " More overlap, or an ordinary explanation for this one, may lie outside it.",
        ]
        if withhold:
            limits.append(
                "Detail is withheld for sets of fewer than 5 reviewers: no member list, no per-reviewer links, "
                "and no list of which businesses they share. This limits what is disclosed; it does not make "
                "them anonymous. The number of pairs indicates how small a set is, and someone with the same "
                "public review pages could work out who is in it."
            )
    return {
        "assessed": assessed,
        "observed": bool(pairs),
        "pairs": len(pairs),
        "reviewers_involved": None if withhold else len(involved_reviewers),
        "businesses_shared": (len(involved_businesses) if detailed else _size_band(len(involved_businesses))) if pairs else 0,
        "strongest_pair": (strongest if detailed else _size_band(strongest)) if pairs else 0,
        "category_spread": _category_spread(category_count) if pairs else None,
        "detail_withheld": withhold,
        "observed_statements": observed,
        "limit_statements": limits,
    }


def _indicator_section(
    graph: Graph, groups: list[OverlapGroup], names: dict[str, str] | None, coverage: dict[str, Any]
) -> dict[str, Any]:
    categories = {
        business_id: {record.category for record in records if record.category is not None}
        for business_id, records in graph.business_attributes.items()
    }
    rows = []
    withheld = 0
    for index, group in enumerate(groups, start=1):
        if names is None and len(group.reviewers) < MIN_AGGREGATE_GROUP_SIZE:
            # Real data: nothing is published about a set this small, not even its size.
            withheld += 1
            continue
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
        "group_count": len(groups),
        "groups": rows,
        "groups_withheld": withheld,
        "evidence": _overlap_evidence(graph, groups, categories, coverage, detailed=names is not None),
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
            detail.append(
                f"{_plural(row.reviewers_from_pages, 'review')} collected; "
                + ("its reported total is withheld." if labels[row.business_id].startswith("Expanded business") else "reported total unknown.")
            )
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


def _pair_population(mode: str, pairs: int, history_pairs: int, coverage: dict[str, Any]) -> str:
    """Who the overlapping pairs are among, and how much is known about them.

    Pairs are counted across every collected reviewer. A pair can rest on
    review pages alone, so it need not be between reviewers whose histories
    were retrieved. For real data a mixed split is described, not counted.
    """
    reviewers, with_history = coverage["reviewers"], coverage["reviewers_with_history"]
    if history_pairs == pairs:
        between = (
            "That pair is between two reviewers with a retrieved history."
            if pairs == 1
            else "Every such pair is between two reviewers with a retrieved history."
        )
    elif history_pairs == 0:
        between = (
            "No such pair is between two reviewers with a retrieved history: each rests on collected "
            "review pages for at least one of its reviewers."
        )
    elif mode == SYNTHETIC:
        between = (
            f"{history_pairs} of them {'is' if history_pairs == 1 else 'are'} between two reviewers with a "
            f"retrieved history; the other {pairs - history_pairs} "
            f"{'involves' if pairs - history_pairs == 1 else 'involve'} a reviewer seen only on collected review pages."
        )
    else:
        between = (
            "Some are between two reviewers with a retrieved history; others involve a reviewer seen only "
            "on collected review pages."
        )
    known = (
        f"Review histories were retrieved for all {reviewers} reviewers."
        if with_history == reviewers
        else f"Review histories were retrieved for {with_history} of the {reviewers} reviewers; for the "
        "others, only the collected review pages are known."
    )
    return f"{between} {known}"


def _explanation(
    mode: str, metrics: Any, coverage: dict[str, Any], indicators: dict[str, Any], history_pairs: int = 0
) -> dict[str, Any]:
    minimum = indicators["minimum_shared_businesses"]
    history_gap = coverage["reviewers"] - coverage["reviewers_with_history"]
    compared = coverage["reviewers_with_history"]
    if indicators["group_count"]:
        headline = (
            f"{_plural(indicators['group_count'], 'reviewer group')} "
            f"{'shares' if indicators['group_count'] == 1 else 'share'} {minimum} or more businesses "
            f"({_plural(indicators['pairs'], 'reviewer pair')})."
        )
    elif compared < 2:
        # Nothing could be compared: an absence of overlap here is not a result.
        headline = "Coordination cannot be assessed from this data."
    else:
        # Never "no overlap": only the retrieved windows were compared.
        headline = "No repeated overlap observed in the retrieved review windows."
    windows = coverage["history_windows"]
    limited = windows["partial"] + windows["unknown"]
    points = [
        f"{metrics.reviewer_pairs_sharing_a_business} reviewer pairs share at least one business. "
        "That is expected: reviewers were collected from business review pages, so everyone on "
        "a page shares that business.",
        f"Review histories were retrieved for {coverage['reviewers_with_history']} of "
        f"{coverage['reviewers']} reviewers. For the other {history_gap}, only the business they "
        "were found on is known, so overlap among them cannot be seen yet.",
    ]
    if limited:
        points.append(
            f"{limited} of the {windows['retrieved']} retrieved "
            f"{'history is' if windows['retrieved'] == 1 else 'histories are'} a limited window "
            "or of unknown completeness, not the reviewer's full history"
            + (
                f" (the {'window covers' if windows['retrieved'] == 1 else 'windows cover'} {windows['share']} "
                f"of the reviews {'that reviewer reports' if windows['retrieved'] == 1 else 'those reviewers report'})"
                if windows["share"]
                else ""
            )
            + ". A business shared outside a retrieved window would not be seen."
        )
    if indicators["group_count"]:
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

    if indicators["group_count"]:
        # Pairs are counted across all collected reviewers, not only those with a history.
        assessment = {
            "assessable": True,
            "statement": (
                f"Across the {coverage['reviewers']} reviewers in the collected data, "
                f"{_plural(indicators['pairs'], 'pair')} "
                f"{'shares' if indicators['pairs'] == 1 else 'share'} {minimum} or more businesses within the "
                "collected review pages and retrieved history windows. "
                f"{_pair_population(mode, indicators['pairs'], history_pairs, coverage)} "
                "That is a pattern to look into, not a finding of coordination."
            ),
        }
    # Without overlap, an absence is only meaningful between reviewers whose histories were collected.
    elif compared < 2:
        assessment = {
            "assessable": False,
            "statement": (
                "Coordination cannot be assessed from this data. Overlap can only be seen between "
                f"reviewers whose review histories were collected, and {compared} "
                f"{'was' if compared == 1 else 'were'}."
            ),
        }
    else:
        assessment = {
            "assessable": True,
            "statement": (
                f"No repeated overlap was observed among the {compared} reviewers with a retrieved history"
                + (
                    f", {limited} of which {'is' if limited == 1 else 'are'} partial or of unknown completeness"
                    if limited
                    else ""
                )
                + ". "
                + (f"The other {history_gap} reviewers could not be compared, and b" if history_gap else "B")
                + "usinesses outside the retrieved windows could not be compared, so this does not rule "
                "coordination out."
            ),
        }
    return {"headline": headline, "points": points, "assessment": assessment}


def build_view(
    graph: Graph,
    mode: str,
    names: dict[str, str] | None = None,
    business_titles: dict[str, str] | None = None,
    expanded_businesses: frozenset[str] = frozenset(),
    withhold_expanded: bool = True,
) -> dict[str, Any]:
    """Everything the dashboard shows for one graph.

    ``names`` (a label for every node) must be None for real data.
    ``business_titles`` may name businesses the user picked from a public
    listing; it never names a reviewer, and other businesses stay anonymous.
    ``expanded_businesses`` are businesses reached through reviewers' shared
    histories. They are numbered, never named, and their reported review total
    and rating are withheld, because those public figures could identify the
    business and, through it, the few reviewers who share it.
    ``withhold_expanded=False`` is for fictional data only: expanded businesses
    are then marked as expanded but shown like any other.
    """
    metrics = compute_metrics(graph)
    withheld = expanded_businesses if withhold_expanded else frozenset()
    coverage_rows = [
        replace(row, reported_review_totals=()) if row.business_id in withheld else row
        for row in business_coverage(graph)
    ]
    labels = _business_labels(coverage_rows, names, business_titles or {}, withheld)
    groups = overlap_groups(graph)
    coverage = _coverage_section(graph, coverage_rows, labels, expanded_businesses, withheld)
    indicators = _indicator_section(graph, groups, names, coverage)
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
        "explanation": _explanation(
            mode, metrics, coverage, indicators,
            history_pairs=sum(
                1 for pair in shared_business_pairs(graph) if all(r in graph.histories for r in pair.reviewers)
            ),
        ),
        "coverage": coverage,
        "indicators": indicators,
        "ratings": _ratings_section(graph, coverage_rows, labels, groups, expanded_businesses, withheld),
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


def expansion_summary(
    original: list[ParsedResponse], pages: list[ParsedResponse], exact: bool = False
) -> dict[str, list[str]]:
    """What expansion pages added to a collection, and what that does not show.

    Stated as before and after, from the merged graph. ``exact`` is for
    fictional data only; for real data a small number of reviewers seen again
    is banded and the statement says that the businesses are not identified.
    """
    before = compute_metrics(build_graph(original))
    after = compute_metrics(build_graph(original + pages))
    earlier = {o.reviewer_id for parsed in original for o in parsed.observations}
    seen_again = len(earlier & {o.reviewer_id for page in pages for o in page.observations})
    count = len(pages)
    reviews = sum(len(page.observations) for page in pages)
    again = (
        "None of the reviewers already in this investigation appears"
        if seen_again == 0
        else "Fewer than 5 of the reviewers already in this investigation appear"
        if seen_again < MIN_AGGREGATE_GROUP_SIZE and not exact
        else f"{seen_again} of the reviewers already in this investigation appear"
    )
    return {
        "findings": [
            f"{count} shared business{'' if count == 1 else 'es'} {'was' if count == 1 else 'were'} expanded, "
            f"each by its first review page: {reviews} review{'' if reviews == 1 else 's'} collected.",
            f"Reviewers in the investigation: {before.reviewers} before expansion, {after.reviewers} after.",
            f"{again} on the newly collected page{'' if count == 1 else 's'}.",
            f"Reviewer pairs sharing {MIN_SHARED_BUSINESSES} or more businesses: "
            f"{before.reviewer_pairs_sharing_two_or_more} before "
            f"expansion, {after.reviewer_pairs_sharing_two_or_more} after.",
        ],
        "limits": [
            "A reviewer appearing on a newly collected page shows that they reviewed that business. It does "
            "not show that reviews were arranged.",
            "The counts after expansion are higher because more was collected. The increase describes what "
            "was retrieved, not a change in anyone's behaviour.",
            "Only the first review page of each expanded business was collected, so reviewers who are not "
            "on it were not seen.",
            "No review histories were collected for reviewers first seen during expansion, so overlap among "
            "them was not assessed.",
            (
                "This is fictional data, so the expanded businesses are named and the counts are exact. For "
                "real data they are not named, their reported review totals and ratings are withheld, and "
                "small counts are banded."
            )
            if exact
            else (
                "Which businesses were expanded is not shown, and their reported review totals and ratings are "
                "withheld. This limits what is disclosed about the reviewers who share them; it does not make "
                "those reviewers anonymous."
            ),
        ],
    }


def _name_list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def synthetic_view(expansions: int = 0) -> dict[str, Any]:
    """Individual-level view of the fictional demo graph. Reads nothing from disk.

    ``expansions`` is how many expansion steps to apply. Each uses the real
    eligibility rule on fictional pages and sends no request; fewer are applied
    when no business is eligible any more.
    """
    names = display_names()
    original, steps = synthetic_collection(expansions)
    pages = [page for step in steps for page in step]
    graph = build_graph(original + pages)
    expanded = [page.page.business_id for page in pages]
    view = build_view(graph, SYNTHETIC, names, expanded_businesses=frozenset(expanded), withhold_expanded=False)

    upcoming = [names[business_id] for business_id in expansion_candidates(graph)[:MAX_EXPANSION_BUSINESSES]]
    if upcoming:
        one = len(upcoming) == 1
        statement = (
            f"Expansion is available. {_name_list(upcoming)} {'is' if one else 'are'} listed in the retrieved "
            f"histories of both reviewers of at least one pair that shares {MIN_SHARED_BUSINESSES} or more "
            f"businesses, and {'has' if one else 'have'} no review page yet. Expanding collects "
            f"{'its' if one else 'their'} first review page, to see who else reviewed "
            f"{'it' if one else 'them'}. In this demo the page is fictional and built on this computer: no "
            "request is sent and no credit is used. With real data each page is one SerpApi request that you "
            "confirm first, and the businesses are chosen by the server without being named."
        )
    else:
        statement = (
            "No further business qualifies for expansion: no remaining business is listed in the retrieved "
            "histories of both reviewers of an overlapping pair. Expansion stops here; it never continues "
            "from reviewers first seen on an expanded page, because their histories were not collected."
        )
    previous = build_graph(original + [page for step in steps[:-1] for page in step]) if steps else graph
    view["expansion"] = {
        "synthetic": True,
        "available": bool(upcoming),
        "max_businesses": MAX_EXPANSION_BUSINESSES if upcoming else 0,
        "steps": len(steps),
        "performed": len(pages),
        "last": None,
        "statement": statement,
        # Nodes that the latest step added or turned into a collected page.
        "new_nodes": sorted((graph.reviewer_ids - previous.reviewer_ids) | {p.page.business_id for p in steps[-1]}) if steps else [],
        **(expansion_summary(original, pages, exact=True) if pages else {"findings": [], "limits": []}),
    }
    return view
