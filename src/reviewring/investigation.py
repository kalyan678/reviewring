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
    CONTRIBUTOR_REVIEWS_DEFAULT_NUM,
    CONTRIBUTOR_REVIEWS_ENGINE,
    CONTRIBUTOR_REVIEWS_MAX_NUM,
    MAPS_REVIEWS_ENGINE,
    ParsedResponse,
)
from reviewring.parsers import ParseError, parse_contributor_reviews, parse_maps_reviews
from reviewring.serpapi_client import MissingApiKeyError, SerpApiError

MAPS_SEARCH_ENGINE = "google_maps"
DEFAULT_HISTORIES = 3
MAX_HISTORIES = 8

# Entries requested per contributor history (the documented `num` parameter).
# The default is above the API's own default of 10, so that an ordinary
# reviewer's whole history fits in one request, and well below the documented
# maximum to limit how much personal data is collected and stored. `num` is a
# ceiling, not a promise: the API may return fewer entries than asked for.
MIN_HISTORY_LIMIT = CONTRIBUTOR_REVIEWS_DEFAULT_NUM
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = CONTRIBUTOR_REVIEWS_MAX_NUM

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


def history_params(contributor_id: str, limit: int = CONTRIBUTOR_REVIEWS_DEFAULT_NUM) -> dict[str, Any]:
    """Contributor Reviews request for up to ``limit`` entries.

    At the API's default the parameter is left out, so the request is identical
    to the ones verified earlier and their saved responses are still reused. Any
    other limit is part of the request and therefore of its cache identity: a
    history saved at one limit is never reused or overwritten at another.
    """
    if not MIN_HISTORY_LIMIT <= limit <= MAX_HISTORY_LIMIT:
        raise ValueError("history limit outside the supported range")
    params: dict[str, Any] = {"engine": CONTRIBUTOR_REVIEWS_ENGINE, "contributor_id": contributor_id}
    if limit != CONTRIBUTOR_REVIEWS_DEFAULT_NUM:
        params["num"] = limit
    return params


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


def choose_reviewers(page: ParsedResponse, count: int, limit: int = DEFAULT_HISTORY_LIMIT) -> list[str]:
    """Which reviewers' histories to request: a fixed coverage rule, not a judgement.

    The aim is histories that can actually be compared, not the busiest
    accounts. Reviewers are taken in this order:

    1. Those whose reported review count is between 2 and ``limit``, in page
       order. Their whole history should fit in one request. Page order is used
       so that the choice is not ranked by activity.
    2. Those with no reported count, in page order.
    3. Those reporting more than ``limit`` reviews, fewest first. Their history
       will be a partial window, and very prolific reviewers share popular
       places for ordinary reasons, so they come last.

    A reviewer reporting a single review is skipped: that history would only
    repeat this business. Only the reported count and the position on the page
    are used; nothing about review content, rating, or identity.
    """
    seen: set[str] = set()
    candidates = []
    for position, reviewer in enumerate(page.reviewer_attributes):
        if reviewer.reviewer_id in seen:
            continue
        seen.add(reviewer.reviewer_id)
        reported = reviewer.reported_review_count
        if reported is None:
            candidates.append((2, 0, position, reviewer.reviewer_id))
        elif 2 <= reported <= limit:
            candidates.append((1, 0, position, reviewer.reviewer_id))
        elif reported > limit:
            candidates.append((3, reported, position, reviewer.reviewer_id))
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
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> Collection:
    """Collect one review page and up to ``histories`` contributor histories.

    At most ``max_requests`` new SerpApi requests are attempted; saved responses
    do not count. Collection stops at the first failure of any kind, keeping
    everything collected so far; it never raises.
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
        chosen = choose_reviewers(page, min(histories, MAX_HISTORIES), history_limit)
        result.histories_planned = len(chosen)
        report()
        for number, contributor_id in enumerate(chosen, start=1):
            history = parse_contributor_reviews(
                fetch(history_params(contributor_id, history_limit)), f"history-{number}"
            )
            if history.history is None or history.history.reviewer_id != contributor_id:
                raise ParseError("response is for a different contributor")
            result.parsed.append(history)
            result.histories_collected += 1
            report()
    except Exception as error:  # noqa: BLE001
        # Anything already parsed stays in `result`. The message is one of a
        # fixed set; exception text is never kept, since it could quote data.
        result.stopped = describe_error(error)
        try:
            report()
        except Exception:  # noqa: BLE001
            pass
    return result
