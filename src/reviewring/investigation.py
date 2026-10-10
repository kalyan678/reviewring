"""Bounded collection for one business investigation.

One first page of Google Maps Reviews for the business, then the contributor
history of at most a chosen number of that page's reviewers. Nothing else is
requested: no further review pages, no pages for businesses found in
histories, no recursion.

This module holds no state and performs no HTTP itself. The caller supplies
``search`` (the cached SerpApi client) and ``is_cached``; every response comes
back through the existing parsers, so the result merges with
``reviewring.graph.build_graph`` like any saved response.

Request parameters are the ones verified in docs/EXPERIMENTS.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reviewring.models import (
    CONTRIBUTOR_REVIEWS_ENGINE,
    MAPS_REVIEWS_ENGINE,
    ParsedResponse,
)
from reviewring.parsers import ParseError, parse_contributor_reviews, parse_maps_reviews
from reviewring.serpapi_client import MissingApiKeyError, SerpApiError

MAPS_SEARCH_ENGINE = "google_maps"
DEFAULT_HISTORIES = 3
MAX_HISTORIES = 8

Search = Callable[[dict[str, Any]], dict[str, Any]]
IsCached = Callable[[dict[str, Any]], bool]


class RequestBudgetExhausted(Exception):
    """A new SerpApi request would exceed the confirmed budget. Nothing was sent."""


class RequestCeilingReached(SerpApiError):
    """The server's overall ceiling on new SerpApi requests was reached. Nothing was sent."""


def search_params(query: str) -> dict[str, str]:
    return {"engine": MAPS_SEARCH_ENGINE, "q": query, "type": "search"}


def review_page_params(data_id: str) -> dict[str, str]:
    return {"engine": MAPS_REVIEWS_ENGINE, "data_id": data_id, "hl": "en"}


def history_params(contributor_id: str) -> dict[str, str]:
    return {"engine": CONTRIBUTOR_REVIEWS_ENGINE, "contributor_id": contributor_id}


def describe_error(error: Exception) -> str:
    """A fixed, user-safe description. SerpApi's own text is never passed on:
    it can echo request parameters such as a contributor ID."""
    if isinstance(error, RequestBudgetExhausted):
        return "The confirmed request budget was used up before collection finished."
    if isinstance(error, RequestCeilingReached):
        return "This server has reached its safety limit on new SerpApi requests. Restart it to continue."
    if isinstance(error, MissingApiKeyError):
        return "No SerpApi key is configured. Add SERPAPI_API_KEY to the .env file and restart."
    if isinstance(error, ParseError):
        return "SerpApi returned a response that could not be used."
    if isinstance(error, SerpApiError):
        text = str(error)
        if text.startswith("SerpApi request failed"):
            return "The request to SerpApi failed or timed out. It may still have used a credit."
        status = re.match(r"SerpApi (?:error|returned) \(?HTTP (\d{3})", text)
        if status:
            return f"SerpApi rejected the request (HTTP {status.group(1)})."
        return "SerpApi returned a response that could not be used."
    return "Collection stopped because of an unexpected error."


def choose_reviewers(page: ParsedResponse, count: int) -> list[str]:
    """Which reviewers' histories to request: a fixed rule, not a judgement.

    Reviewers reporting two or more reviews come first, most reviews first, then
    those with no reported count; ties keep page order. A reviewer reporting a
    single review is skipped, because that history would only repeat this
    business. Nothing about review content, rating, or identity is used.
    """
    seen: set[str] = set()
    candidates = []
    for position, reviewer in enumerate(page.reviewer_attributes):
        if reviewer.reviewer_id in seen:
            continue
        seen.add(reviewer.reviewer_id)
        reported = reviewer.reported_review_count
        if reported is None or reported >= 2:
            candidates.append((reported is None, -(reported or 0), position, reviewer.reviewer_id))
    return [reviewer_id for *_, reviewer_id in sorted(candidates)[: max(count, 0)]]


@dataclass
class Collection:
    """What one investigation collected, including when it stopped early."""

    parsed: list[ParsedResponse] = field(default_factory=list)
    requests_sent: int = 0  # new SerpApi requests attempted
    cache_hits: int = 0  # answered from saved responses, no request
    histories_planned: int = 0
    histories_collected: int = 0
    stopped: str | None = None  # user-safe reason when collection ended early


def collect(
    data_id: str,
    histories: int,
    max_requests: int,
    search: Search,
    is_cached: IsCached,
    on_progress: Callable[[Collection], None] | None = None,
) -> Collection:
    """Collect one review page and up to ``histories`` contributor histories.

    At most ``max_requests`` new SerpApi requests are attempted; saved responses
    do not count. Collection stops at the first failure, keeping everything
    collected so far.
    """
    result = Collection()

    def fetch(params: dict[str, Any]) -> dict[str, Any]:
        if is_cached(params):
            result.cache_hits += 1
        else:
            if result.requests_sent >= max_requests:
                raise RequestBudgetExhausted
            result.requests_sent += 1
        return search(params)

    def report() -> None:
        if on_progress is not None:
            on_progress(result)

    try:
        page = parse_maps_reviews(fetch(review_page_params(data_id)), "review-page")
        if page.page is None or page.page.business_id != data_id:
            raise ParseError("response is for a different business")
        result.parsed.append(page)
        chosen = choose_reviewers(page, min(histories, MAX_HISTORIES))
        result.histories_planned = len(chosen)
        report()
        for number, contributor_id in enumerate(chosen, start=1):
            history = parse_contributor_reviews(fetch(history_params(contributor_id)), f"history-{number}")
            if history.history is None or history.history.reviewer_id != contributor_id:
                raise ParseError("response is for a different contributor")
            result.parsed.append(history)
            result.histories_collected += 1
            report()
    except (SerpApiError, ParseError, RequestBudgetExhausted) as error:
        result.stopped = describe_error(error)
        report()
    return result
