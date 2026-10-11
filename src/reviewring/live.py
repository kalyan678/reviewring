"""In-memory state for live investigations run from the dashboard.

Every action that can spend a SerpApi credit takes two steps: a *plan* that
states the maximum number of new requests, then an execution that names that
plan. Plans are single-use and expire, and the budget in the plan is the one
enforced during execution, so a confirmation in the browser cannot be skipped
or enlarged.

Nothing here is written to disk and nothing is logged. Business data_ids and
contributor IDs stay in this process: the browser sees only random references,
public business listing details, and aggregate views. Raw responses are stored
only by the existing SerpApi client (sanitized, in git-ignored directories).
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from reviewring import serpapi_client
from reviewring.graph import build_graph
from reviewring.indicators import MIN_SHARED_BUSINESSES, expansion_candidates
from reviewring.investigation import (
    DEFAULT_HISTORIES,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_REVIEW_PAGES,
    MAX_EXPANSION_BUSINESSES,
    MAX_HISTORIES,
    MAX_HISTORY_LIMIT,
    MAX_REVIEW_PAGES,
    MIN_HISTORY_LIMIT,
    Collection,
    IsCached,
    RequestCeilingReached,
    Search,
    choose_reviewers,
    collect,
    describe_error,
    expand,
    history_params,
    merge_review_pages,
    page_statement,
    review_page_params,
    search_params,
    usable_page_token,
)
from reviewring.models import ParsedResponse
from reviewring.parsers import ParseError, parse_maps_reviews
from reviewring.serpapi_client import SerpApiError
from reviewring.views import INVESTIGATION, build_view, expansion_summary

# Safety net across all plans: new requests one server process may attempt.
PROCESS_REQUEST_CEILING = 30
# ReviewRing counts requests. What SerpApi bills for them depends on the user's
# plan and is not something this code knows, so plans state it as a possibility.
BILLING_NOTE = "Each request sent may use a search credit from your SerpApi plan."
PLAN_LIFETIME_SECONDS = 600
MAX_STORED = 20  # plans, searches, and investigations kept in memory
MAX_RESULTS = 20
MIN_QUERY_LENGTH, MAX_QUERY_LENGTH = 3, 120


class LiveError(Exception):
    """A request that cannot be served. ``message`` is safe to show in the browser."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Plan:
    kind: str
    max_requests: int
    payload: dict[str, Any]
    created: float = field(default_factory=time.monotonic)


@dataclass
class _Investigation:
    title: str
    max_requests: int
    histories_requested: int
    collection: Collection = field(default_factory=Collection)
    state: str = "running"
    history_limit: int = DEFAULT_HISTORY_LIMIT
    view: dict[str, Any] | None = None
    thread: threading.Thread | None = None
    business: dict[str, str] = field(default_factory=dict)  # data_id and title; never sent to the browser
    # Ring expansion (a separate, separately confirmed stage).
    expansion_pages: list[ParsedResponse] = field(default_factory=list)
    expanded_ids: list[str] = field(default_factory=list)
    expansions: list[Collection] = field(default_factory=list)
    activity: str = "collecting"  # what a running state is doing: "collecting" or "expanding"


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _trim(store: dict[str, Any]) -> None:
    while len(store) > MAX_STORED:
        del store[next(iter(store))]


class LiveState:
    """Plans, search results, and investigations for one server process."""

    def __init__(
        self,
        search: Search = serpapi_client.search,
        is_cached: IsCached = serpapi_client.is_cached,
        key_configured: Any = serpapi_client.api_key_configured,
    ) -> None:
        self.token = secrets.token_urlsafe(32)
        self._search = search
        self._is_cached = is_cached
        self._key_configured = key_configured
        self._lock = threading.Lock()
        self._plans: dict[str, _Plan] = {}
        self._businesses: dict[str, dict[str, str]] = {}  # reference -> data_id, title
        self._investigations: dict[str, _Investigation] = {}
        self._requests_attempted = 0
        self._running = False

    # ---------- request accounting ----------

    def _guarded_search(self, params: dict[str, Any]) -> dict[str, Any]:
        """The client's search, refusing new requests beyond the process ceiling."""
        if not self._is_cached(params):
            with self._lock:
                if self._requests_attempted >= PROCESS_REQUEST_CEILING:
                    raise RequestCeilingReached("request ceiling reached")
                self._requests_attempted += 1
        return self._search(params)

    # ---------- plans ----------

    def _add_plan(self, kind: str, max_requests: int, payload: dict[str, Any]) -> str:
        plan_id = secrets.token_urlsafe(16)
        with self._lock:
            self._plans[plan_id] = _Plan(kind, max_requests, payload)
            _trim(self._plans)
        return plan_id

    def _take_plan(self, plan_id: Any, kind: str) -> _Plan:
        with self._lock:
            plan = self._plans.get(plan_id) if isinstance(plan_id, str) else None
            if plan is None or plan.kind != kind:
                raise LiveError(409, "This confirmation is not valid. Check the cost again.")
            del self._plans[plan_id]  # single use
        if time.monotonic() - plan.created > PLAN_LIFETIME_SECONDS:
            raise LiveError(409, "This confirmation has expired. Check the cost again.")
        return plan

    # ---------- session ----------

    def key_configured(self) -> bool:
        """Whether a SerpApi key is configured. The key itself never leaves the client module."""
        return bool(self._key_configured())

    def session_info(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "key_configured": bool(self._key_configured()),
            "default_histories": DEFAULT_HISTORIES,
            "max_histories": MAX_HISTORIES,
            "default_history_limit": DEFAULT_HISTORY_LIMIT,
            "min_history_limit": MIN_HISTORY_LIMIT,
            "max_history_limit": MAX_HISTORY_LIMIT,
            "default_review_pages": DEFAULT_REVIEW_PAGES,
            "max_review_pages": MAX_REVIEW_PAGES,
        }

    # ---------- search ----------

    def plan_search(self, query: Any) -> dict[str, Any]:
        query = _text(query)
        if query is None or not MIN_QUERY_LENGTH <= len(query) <= MAX_QUERY_LENGTH:
            raise LiveError(400, f"Enter a search of {MIN_QUERY_LENGTH} to {MAX_QUERY_LENGTH} characters.")
        if re.search(r"[\x00-\x1f\x7f]", query):
            raise LiveError(400, "The search contains characters that cannot be used.")
        saved = self._is_cached(search_params(query))
        max_requests = 0 if saved else 1
        statement = (
            "No SerpApi request: this search is already saved on this computer."
            if saved
            else f"At most 1 SerpApi request for this search. {BILLING_NOTE}"
        )
        plan_id = self._add_plan("search", max_requests, {"query": query})
        return {"plan": plan_id, "max_requests": max_requests, "statement": statement}

    def run_search(self, plan_id: Any) -> dict[str, Any]:
        plan = self._take_plan(plan_id, "search")
        params = search_params(plan.payload["query"])
        if plan.max_requests == 0 and not self._is_cached(params):
            raise LiveError(409, "The saved search is no longer available. Check the cost again.")
        try:
            response = self._guarded_search(params)
        except SerpApiError as error:
            raise LiveError(502, describe_error(error)) from None
        results = response.get("local_results") if isinstance(response, dict) else None
        businesses = []
        for entry in results if isinstance(results, list) else []:
            data_id = _text(entry.get("data_id")) if isinstance(entry, dict) else None
            title = _text(entry.get("title")) if data_id else None
            if title is None:
                continue
            reference = secrets.token_urlsafe(12)
            with self._lock:
                self._businesses[reference] = {"data_id": data_id, "title": title}
            reviews = entry.get("reviews")
            businesses.append(
                {
                    "ref": reference,
                    "title": title,
                    "category": _text(entry.get("type")),
                    "rating": _number(entry.get("rating")),
                    "reviews": reviews if isinstance(reviews, int) and not isinstance(reviews, bool) else None,
                    "address": _text(entry.get("address")),
                }
            )
            if len(businesses) == MAX_RESULTS:
                break
        with self._lock:
            while len(self._businesses) > MAX_STORED * MAX_RESULTS:
                del self._businesses[next(iter(self._businesses))]
        return {"businesses": businesses}

    # ---------- investigation ----------

    def _saved_review_pages(self, data_id: str, pages: int) -> tuple[list[ParsedResponse], str]:
        """Saved review pages from the first, following only saved tokens. No request is made.

        Returns the pages and what is known: ``"all"`` (every requested page is
        saved), ``"end"`` (the saved pages end before the requested count: no
        usable next-page token), or ``"open"`` (the next page is not saved).
        """
        saved: list[ParsedResponse] = []
        token: str | None = None
        used: set[str] = set()
        for number in range(1, pages + 1):
            params = review_page_params(data_id, token)
            if not self._is_cached(params):
                return saved, "open"
            try:
                page = parse_maps_reviews(self._search(params), f"review-page-{number}")
            except (SerpApiError, ParseError):
                return saved, "open"
            if page.page is None or page.page.business_id != data_id or page.page.request_token != token:
                return saved, "open"
            saved.append(page)
            next_token = page.page.next_token
            if next_token is None or next_token in used or not usable_page_token(next_token):
                return saved, "end"
            used.add(next_token)
            token = next_token
        return saved, "all"

    def plan_investigation(
        self, reference: Any, histories: Any, history_limit: Any = None, pages: Any = None
    ) -> dict[str, Any]:
        with self._lock:
            business = self._businesses.get(reference) if isinstance(reference, str) else None
        if business is None:
            raise LiveError(404, "That business is not in the current search results. Search again.")
        if isinstance(histories, bool) or not isinstance(histories, int) or not 0 <= histories <= MAX_HISTORIES:
            raise LiveError(400, f"Choose between 0 and {MAX_HISTORIES} reviewer histories.")
        limit = DEFAULT_HISTORY_LIMIT if history_limit is None else history_limit
        if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_HISTORY_LIMIT <= limit <= MAX_HISTORY_LIMIT:
            raise LiveError(
                400, f"Choose between {MIN_HISTORY_LIMIT} and {MAX_HISTORY_LIMIT} entries per reviewer history."
            )
        page_count = DEFAULT_REVIEW_PAGES if pages is None else pages
        if isinstance(page_count, bool) or not isinstance(page_count, int) or not 1 <= page_count <= MAX_REVIEW_PAGES:
            raise LiveError(400, f"Choose between 1 and {MAX_REVIEW_PAGES} review pages.")
        window = (
            f" Each history request asks for up to {limit} entries; the API may return fewer, "
            "and a history saved with a different entry limit is not reused."
            if histories
            else ""
        )

        def requests(count: int) -> str:
            return f"At most {count} SerpApi request{'s' if count != 1 else ''}"

        # Saved pages show exactly which pages and histories would be new. No request is made.
        saved_pages, known = self._saved_review_pages(business["data_id"], page_count)
        if known in ("all", "end"):
            chosen = choose_reviewers(merge_review_pages(saved_pages), histories, limit)
            new = sum(1 for contributor_id in chosen if not self._is_cached(history_params(contributor_id, limit)))
            saved = len(chosen) - new
            ended = (
                f" Only {len(saved_pages)} of the {page_count} requested review pages can be collected: "
                "the saved responses provide no usable next-page token."
                if len(saved_pages) < page_count
                else ""
            )
            if new == 0:
                statement = "No SerpApi request: everything needed is already saved on this computer." + ended
            else:
                pages_text = "The review page" if len(saved_pages) == 1 else f"The {len(saved_pages)} review pages"
                statement = (
                    f"{requests(new)}. {pages_text} and {saved} of {len(chosen)} reviewer histories are already "
                    "saved on this computer and will not be requested again." + ended + window
                )
            return self._investigation_plan(business, histories, limit, page_count, new, statement)

        new_pages = page_count - len(saved_pages)
        maximum = new_pages + histories
        if page_count == 1:
            statement = (
                f"{requests(maximum)}: 1 for the first page of reviews and up to {histories} for reviewer "
                "histories. Whether any history is already saved is unknown until the review page is read." + window
            )
        else:
            statement = (
                f"{requests(maximum)}: up to {new_pages} for review pages"
                + (
                    f" (the first of {page_count} is already saved)"
                    if len(saved_pages) == 1
                    else f" (the first {len(saved_pages)} of {page_count} are already saved)"
                    if saved_pages
                    else ""
                )
                + f" and up to {histories} for reviewer histories. A further review page is requested only if "
                "the previous response provides a next-page token, so fewer pages may be retrieved than asked "
                "for. Whether any history is already saved is unknown until the review pages are read." + window
            )
        return self._investigation_plan(business, histories, limit, page_count, maximum, statement)

    def _investigation_plan(
        self, business: dict[str, str], histories: int, limit: int, pages: int, max_requests: int, statement: str
    ) -> dict[str, Any]:
        with self._lock:
            remaining = PROCESS_REQUEST_CEILING - self._requests_attempted
        if max_requests > remaining:
            raise LiveError(
                409,
                f"This could need {max_requests} new requests, more than the {remaining} this server may still "
                "send before its safety limit. Choose fewer pages or histories, or restart the server.",
            )
        if max_requests:
            statement = f"{statement} {BILLING_NOTE}"
        plan_id = self._add_plan(
            "investigation",
            max_requests,
            {"business": business, "histories": histories, "history_limit": limit, "pages": pages},
        )
        return {
            "plan": plan_id,
            "business": business["title"],
            "histories": histories,
            "history_limit": limit,
            "pages": pages,
            "max_requests": max_requests,
            "statement": statement,
        }

    def start_investigation(self, plan_id: Any) -> dict[str, Any]:
        with self._lock:
            if self._running:
                raise LiveError(409, "Another investigation is still running. Wait for it to finish.")
        plan = self._take_plan(plan_id, "investigation")
        business, histories = plan.payload["business"], plan.payload["histories"]
        investigation = _Investigation(business["title"], plan.max_requests, histories)
        investigation.business = dict(business)
        investigation.collection.pages_requested = plan.payload["pages"]
        investigation.history_limit = plan.payload["history_limit"]
        investigation_id = secrets.token_urlsafe(16)
        with self._lock:
            if self._running:
                raise LiveError(409, "Another investigation is still running. Wait for it to finish.")
            self._running = True
            self._investigations[investigation_id] = investigation
            _trim(self._investigations)

        def run() -> None:
            def progress(collection: Collection) -> None:
                investigation.collection = collection

            try:
                # collect() never raises: any failure ends collection and keeps
                # what was parsed, with a fixed message in `stopped`.
                collection = collect(
                    business["data_id"], histories, plan.max_requests,
                    self._guarded_search, self._is_cached, progress,
                    plan.payload["history_limit"],
                    plan.payload["pages"],
                )
                investigation.collection = collection
                if not collection.parsed:
                    investigation.state = "failed"
                    return
                try:
                    investigation.view = self._investigation_view(investigation)
                except Exception:  # noqa: BLE001
                    # Collection worked but the result cannot be shown. No view is
                    # offered, and exception text is never surfaced.
                    investigation.view = None
                    investigation.state = "unviewable"
                    return
                investigation.state = "partial" if collection.stopped else "complete"
            except Exception:  # noqa: BLE001
                investigation.collection.stopped = "Collection stopped because of an unexpected error."
                investigation.state = "failed"
            finally:
                with self._lock:
                    self._running = False

        investigation.thread = threading.Thread(target=run, daemon=True)
        investigation.thread.start()
        return {"investigation": investigation_id}

    def investigation_status(self, investigation_id: str) -> dict[str, Any]:
        with self._lock:
            investigation = self._investigations.get(investigation_id)
        if investigation is None:
            raise LiveError(404, "That investigation is not available. Start a new one.")
        collection = investigation.collection
        status = {
            "state": investigation.state,
            "activity": investigation.activity,
            "business": investigation.title,
            "max_requests": investigation.max_requests,
            "requests_sent": collection.requests_sent,
            "saved_responses_used": collection.cache_hits,
            "histories_requested": investigation.histories_requested,
            "history_limit": investigation.history_limit,
            "histories_planned": collection.histories_planned,
            "histories_collected": collection.histories_collected,
            "responses_collected": len(collection.parsed),
            "pages_requested": collection.pages_requested,
            "pages_collected": collection.pages_collected,
            "more_pages": collection.more_pages,
            "page_statement": page_statement(collection),
            "stopped": collection.stopped,
        }
        if investigation.state != "running" and investigation.view is not None:
            status["view"] = investigation.view
        if investigation.state != "running":
            status["expansion"] = self._expansion_info(investigation)
        return status

    # ---------- ring expansion ----------

    def _investigation_view(self, investigation: _Investigation) -> dict[str, Any]:
        """The aggregate view of everything collected: the investigation plus any expansion pages."""
        business = investigation.business
        view = build_view(
            build_graph(investigation.collection.parsed + investigation.expansion_pages),
            INVESTIGATION,
            business_titles={business["data_id"]: business["title"]},
            expanded_businesses=frozenset(investigation.expanded_ids),
        )
        view["coverage"]["page_statement"] = page_statement(investigation.collection)
        return view

    def _candidates(self, investigation: _Investigation) -> list[str]:
        return expansion_candidates(build_graph(investigation.collection.parsed + investigation.expansion_pages))

    def _expansion_info(self, investigation: _Investigation) -> dict[str, Any]:
        """What the browser may know about expansion: availability, cost limits, and aggregate findings.

        Never which businesses qualify or were expanded: they are shared by a
        small set of reviewers, so the server chooses them and does not list them.
        """
        info: dict[str, Any] = {
            "available": False,
            "max_businesses": 0,
            "performed": len(investigation.expanded_ids),
            "last": None,
            "comparison": [],
            "findings": [],
            "limits": [],
        }
        if investigation.state not in ("complete", "partial") or not investigation.collection.parsed:
            info["statement"] = "Expansion is not available because this investigation did not produce a result."
            return info
        candidates = self._candidates(investigation)
        histories = investigation.collection.histories_collected
        if candidates:
            # How many qualify is not disclosed: the limit offered is the fixed
            # per-action maximum, whatever the number of candidates.
            info.update(available=True, max_businesses=MAX_EXPANSION_BUSINESSES)
            info["statement"] = (
                "Expansion is available. At least one pair of reviewers with retrieved histories shares "
                f"{MIN_SHARED_BUSINESSES} or more businesses, so the first review page of up to "
                f"{MAX_EXPANSION_BUSINESSES} businesses listed in both of their histories can be collected, one "
                "request each. The server chooses which by a fixed rule and does not list them or say how many "
                "qualify, because they are shared by a small set of reviewers. A shared business is not "
                "evidence that reviews were arranged."
            )
        elif histories < 2:
            info["statement"] = (
                "Expansion is not available: it needs at least two reviewers with a retrieved history, and "
                f"{'none was' if histories == 0 else 'only 1 was'} retrieved."
            )
        elif investigation.expanded_ids:
            info["statement"] = "No further business qualifies for expansion."
        else:
            info["statement"] = (
                "Expansion is not available. No business qualifies: it must be listed in the retrieved "
                f"histories of both reviewers in a pair that shares at least {MIN_SHARED_BUSINESSES} "
                "businesses, and have no review page collected yet. This is a valid outcome and is not a "
                "finding that no overlap exists."
            )
        if investigation.expansions:
            last = investigation.expansions[-1]
            info["last"] = {
                "state": "failed" if not last.parsed else "partial" if last.stopped else "complete",
                "businesses_requested": last.pages_requested,
                "businesses_collected": last.pages_collected,
                "requests_sent": last.requests_sent,
                "saved_responses_used": last.cache_hits,
                "stopped": last.stopped,
            }
        if investigation.expansion_pages:
            info.update(expansion_summary(investigation.collection.parsed, investigation.expansion_pages))
        return info

    def plan_expansion(self, investigation_id: Any, businesses: Any) -> dict[str, Any]:
        with self._lock:
            investigation = self._investigations.get(investigation_id) if isinstance(investigation_id, str) else None
            running = self._running
        if investigation is None:
            raise LiveError(404, "That investigation is not available. Start a new one.")
        if running or investigation.state == "running":
            raise LiveError(409, "Wait for the running collection to finish.")
        if investigation.state not in ("complete", "partial") or not investigation.collection.parsed:
            raise LiveError(409, "Expansion is not available because this investigation did not produce a result.")
        if isinstance(businesses, bool) or not isinstance(businesses, int) or not 1 <= businesses <= MAX_EXPANSION_BUSINESSES:
            raise LiveError(400, f"Choose 1 to {MAX_EXPANSION_BUSINESSES} businesses to expand.")
        candidates = self._candidates(investigation)
        if not candidates:
            raise LiveError(409, self._expansion_info(investigation)["statement"])
        chosen = candidates[:businesses]
        new = sum(1 for data_id in chosen if not self._is_cached(review_page_params(data_id)))
        with self._lock:
            remaining = PROCESS_REQUEST_CEILING - self._requests_attempted
        if new > remaining:
            raise LiveError(
                409,
                f"This could need {new} new requests, more than the {remaining} this server may still send "
                "before its safety limit. Restart the server to continue.",
            )
        # The cost is exact, because it is what the user confirms. Why it may be
        # lower than asked for (fewer qualify, or pages are saved) is not broken down.
        if new == 0:
            cost = "No SerpApi request: nothing new needs to be requested for this expansion."
        else:
            cost = f"At most {new} SerpApi request{'s' if new != 1 else ''}."
        statement = (
            f"{cost} Expansion collects the first review page of up to {businesses} shared "
            f"business{'' if businesses == 1 else 'es'} chosen by the server. Fewer are collected if fewer "
            "qualify, and a page already saved on this computer is read locally with no request. It requests "
            "no reviewer histories and no further pages, and it does not continue to other businesses afterwards."
        )
        if new:
            statement = f"{statement} {BILLING_NOTE}"
        plan_id = self._add_plan("expansion", new, {"investigation": investigation_id, "candidates": chosen})
        return {"plan": plan_id, "businesses": businesses, "max_requests": new, "statement": statement}

    def start_expansion(self, plan_id: Any, investigation_id: Any) -> dict[str, Any]:
        with self._lock:
            if self._running:
                raise LiveError(409, "Wait for the running collection to finish.")
        plan = self._take_plan(plan_id, "expansion")
        if plan.payload["investigation"] != investigation_id:
            raise LiveError(409, "This confirmation is not valid. Check the cost again.")
        with self._lock:
            investigation = self._investigations.get(investigation_id)
        candidates = plan.payload["candidates"]
        # The plan must still describe what would be collected.
        if investigation is None or investigation.state not in ("complete", "partial") or candidates != self._candidates(investigation)[: len(candidates)]:
            raise LiveError(409, "This confirmation is not valid. Check the cost again.")
        with self._lock:
            if self._running:
                raise LiveError(409, "Wait for the running collection to finish.")
            self._running = True
        resting_state = investigation.state
        investigation.activity = "expanding"
        investigation.state = "running"

        def run() -> None:
            added: list[ParsedResponse] = []
            try:
                result = expand(
                    candidates, plan.max_requests, self._guarded_search, self._is_cached,
                    first_number=len(investigation.expansion_pages) + 1,
                )
                added = result.parsed
                if added:
                    investigation.expansion_pages.extend(added)
                    investigation.expanded_ids.extend(page.page.business_id for page in added)
                    try:
                        investigation.view = self._investigation_view(investigation)
                    except Exception:  # noqa: BLE001
                        # Keep the investigation exactly as it was before this expansion.
                        del investigation.expansion_pages[-len(added):]
                        del investigation.expanded_ids[-len(added):]
                        result.parsed = []
                        result.pages_collected = 0
                        result.stopped = "The expansion result could not be displayed, so it was not added."
                investigation.expansions.append(result)
            except Exception:  # noqa: BLE001
                failed = Collection(pages_requested=len(candidates))
                failed.stopped = "Collection stopped because of an unexpected error."
                investigation.expansions.append(failed)
            finally:
                investigation.state = resting_state
                with self._lock:
                    self._running = False

        investigation.thread = threading.Thread(target=run, daemon=True)
        investigation.thread.start()
        return {"investigation": investigation_id}
