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

from reviewring.indicators import MAX_EXPANSION_BUSINESSES
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

# Review pages collected for the investigated business, including the first.
# Each further page is one request and is made only with a next-page token the
# previous response actually returned.
DEFAULT_REVIEW_PAGES = 1
MAX_REVIEW_PAGES = 4

# Ring expansion: first review pages of businesses shared within an observed
# repeated overlap. A separate, separately confirmed action; never recursive.

# A next-page token is opaque. It is only checked for a plausible form before
# being sent back, so that a malformed value is never forwarded.
_PAGE_TOKEN = re.compile(r"[A-Za-z0-9_\-=+/.%:~]{1,2048}")

PAGINATION_UNUSABLE = (
    "The next-page token returned by the service repeated an earlier one or was not usable, "
    "so no further review page was requested."
)

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


def review_page_params(data_id: str, token: str | None = None) -> dict[str, str]:
    """Google Maps Reviews request: the first page, or the page a returned token points to.

    The four parameter names are the ones observed in SerpApi's own pagination
    link. A token is part of the request and of its cache identity, so each
    page is saved, reused, and counted separately.
    """
    params = {"engine": MAPS_REVIEWS_ENGINE, "data_id": data_id, "hl": "en"}
    if token is not None:
        params["next_page_token"] = token
    return params


def usable_page_token(token: Any) -> bool:
    return isinstance(token, str) and _PAGE_TOKEN.fullmatch(token) is not None


def merge_review_pages(pages: list[ParsedResponse]) -> ParsedResponse:
    """The reviewers of several pages as one list, in page then position order, for selection."""
    merged = ParsedResponse(source_file="review-pages", engine=MAPS_REVIEWS_ENGINE)
    for page in pages:
        merged.reviewer_attributes.extend(page.reviewer_attributes)
    return merged


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
    pages_requested: int = DEFAULT_REVIEW_PAGES
    pages_collected: int = 0
    # After the last collected page: True = the service offered a further page,
    # False = it offered none, None = not known (nothing collected, or the
    # marker it returned could not be used).
    more_pages: bool | None = None
    pagination_note: str | None = None


TOKEN_NOT_USED = "The service provided a token for a further page, which was not used."


def page_statement(collection: Collection) -> str:
    """Pages asked for, pages retrieved, and what is known about further pages."""
    asked, got = collection.pages_requested, collection.pages_collected
    pages = lambda n: f"{n} review page{'' if n == 1 else 's'}"  # noqa: E731
    if got == 0:
        return f"No review page was collected ({pages(asked)} requested)."
    text = f"{got} of {pages(asked)} requested {'was' if got == 1 else 'were'} collected."
    if collection.pagination_note:
        return f"{text} {collection.pagination_note}"
    if collection.more_pages is False:
        # Only what was observed: no token came back. That is not evidence that
        # no further reviews exist.
        if got < asked:
            text += " The service did not provide another page token, so collection stopped before the requested page limit."
        else:
            text += " The service did not provide another page token."
        return text + " This does not establish that all reviews were retrieved."
    if got < asked:
        return f"{text} Collection stopped before the remaining pages were requested."
    # A token is an offer of a further page, not a page: nothing beyond the
    # collected pages was retrieved.
    return f"{text} {TOKEN_NOT_USED}"


def _budgeted_fetch(result: Collection, max_requests: int, search: Search, is_cached: IsCached) -> Search:
    """A fetch that counts saved responses and new requests, refusing new ones beyond the budget."""

    def fetch(params: dict[str, Any]) -> dict[str, Any]:
        if is_cached(params):
            result.cache_hits += 1
        else:
            if result.requests_sent >= max_requests:
                raise RequestBudgetExhausted
            result.requests_sent += 1
        return search(params)

    return fetch


def expand(
    data_ids: list[str],
    max_requests: int,
    search: Search,
    is_cached: IsCached,
    first_number: int = 1,
) -> Collection:
    """Collect the first review page of each given business, and nothing else.

    No further pages, no contributor histories, and nothing for businesses or
    reviewers discovered on the new pages: expansion is one hop. At most
    ``MAX_EXPANSION_BUSINESSES`` businesses and ``max_requests`` new requests.
    Stops at the first failure, keeping the pages already collected; never
    raises and never retries.
    """
    data_ids = list(dict.fromkeys(data_ids))[:MAX_EXPANSION_BUSINESSES]
    result = Collection(pages_requested=len(data_ids))
    fetch = _budgeted_fetch(result, max_requests, search, is_cached)
    try:
        for offset, data_id in enumerate(data_ids):
            page = parse_maps_reviews(fetch(review_page_params(data_id)), f"expansion-page-{first_number + offset}")
            if page.page is None or page.page.business_id != data_id or page.page.request_token is not None:
                raise ParseError("response is for a different business or page")
            result.parsed.append(page)
            result.pages_collected += 1
    except Exception as error:  # noqa: BLE001
        result.stopped = describe_error(error)
    return result


def collect(
    data_id: str,
    histories: int,
    max_requests: int,
    search: Search,
    is_cached: IsCached,
    on_progress: Callable[[Collection], None] | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    pages: int = DEFAULT_REVIEW_PAGES,
) -> Collection:
    """Collect up to ``pages`` review pages, then up to ``histories`` contributor histories.

    A further review page is requested only with a next-page token the previous
    response returned; tokens are never constructed, and one is never used
    twice. Reviewers are chosen from all collected pages together.

    At most ``max_requests`` new SerpApi requests are attempted; saved responses
    do not count. Collection stops at the first failure of any kind, keeping
    everything collected so far; it never raises and never retries.
    """
    pages = min(max(pages, 1), MAX_REVIEW_PAGES)
    result = Collection(pages_requested=pages)
    fetch = _budgeted_fetch(result, max_requests, search, is_cached)

    def report() -> None:
        if on_progress is not None:
            on_progress(result)

    try:
        review_pages: list[ParsedResponse] = []
        token: str | None = None
        used_tokens: set[str] = set()
        for number in range(1, pages + 1):
            page = parse_maps_reviews(fetch(review_page_params(data_id, token)), f"review-page-{number}")
            if page.page is None or page.page.business_id != data_id or page.page.request_token != token:
                raise ParseError("response is for a different business or page")
            result.parsed.append(page)
            review_pages.append(page)
            result.pages_collected = number
            next_token = page.page.next_token
            if next_token is None:
                result.more_pages = False
                break
            if next_token in used_tokens or not usable_page_token(next_token):
                result.more_pages = None
                result.pagination_note = PAGINATION_UNUSABLE
                break
            result.more_pages = True
            used_tokens.add(next_token)
            token = next_token
            report()
        chosen = choose_reviewers(merge_review_pages(review_pages), min(histories, MAX_HISTORIES), history_limit)
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
