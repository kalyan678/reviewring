"""Typed records for the reviewer-business graph.

Every field is one observed in the captured SerpApi responses (see
docs/EXPERIMENTS.md). ``None`` always means "not returned or not usable", never
a default value.

``reviewer_id`` and ``business_id`` hold the raw SerpApi ``contributor_id`` and
``data_id``. They are internal join keys: never print, log, or export them.
Use ``reviewring.privacy.pseudonym`` for anything that leaves the process.
Reviewer names, profile links, avatars, review text, business titles,
addresses, and coordinates are deliberately not part of the model.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

MAPS_REVIEWS_ENGINE = "google_maps_reviews"
CONTRIBUTOR_REVIEWS_ENGINE = "google_maps_contributor_reviews"

# Contributor Reviews `num` parameter, as DOCUMENTED by SerpApi (not observed
# limits): "10 (default) returns 10 results ... should not be larger than 200".
# Observed: with no `num`, two histories returned exactly 10 entries each.
CONTRIBUTOR_REVIEWS_DEFAULT_NUM = 10
CONTRIBUTOR_REVIEWS_MAX_NUM = 200


@dataclass(frozen=True)
class Source:
    """Where an observation came from. Raw filenames are hash-based and hold no identifiers."""

    file: str
    engine: str
    position: int  # 1-based position in the response's `reviews` list


@dataclass(frozen=True)
class ReviewObservation:
    """One sighting of a reviewer's review of a business in one response."""

    reviewer_id: str
    business_id: str
    source: Source
    review_id: str | None = None
    rating: int | float | None = None
    # `date` as returned: relative text that depends on fetch time and language.
    date_text: str | None = None
    # `iso_date`: observed on Google Maps Reviews only.
    iso_date: str | None = None


@dataclass(frozen=True)
class ReviewerAttributes:
    """Reviewer metadata as reported by one response."""

    reviewer_id: str
    source_file: str
    # `user.reviews`: a self-reported total. It is NOT the number of history
    # entries Contributor Reviews returns (observed: reported 6, returned 7).
    reported_review_count: int | None = None
    photo_count: int | None = None
    local_guide: bool | None = None


@dataclass(frozen=True)
class BusinessAttributes:
    """Business metadata as reported by one response."""

    business_id: str
    source_file: str
    category: str | None = None
    reported_review_total: int | None = None
    # Top-level `place_info.rating`: Google's rating for the whole business,
    # not an average of the captured reviews.
    reported_rating: int | float | None = None


@dataclass(frozen=True)
class ReviewsPage:
    """Coverage record: one Google Maps Reviews page captured for a business."""

    business_id: str
    source_file: str
    reviews_listed: int
    request_token: str | None = None  # token this request was made with
    next_token: str | None = None  # token offered for a further page


@dataclass(frozen=True)
class ContributorHistory:
    """Coverage record: one Contributor Reviews response captured for a reviewer."""

    reviewer_id: str
    source_file: str
    entries_listed: int
    next_token: str | None = None
    # `search_parameters.num` as echoed by SerpApi; None when the request sent none.
    requested_limit: int | None = None
    # `contributor.contributions.reviews`: the profile's own review total. It is
    # self-reported, may be approximate, and its key name varies with language.
    reported_total: int | None = None


@dataclass
class ParsedResponse:
    """Everything extracted from one raw response, plus what was skipped and why."""

    source_file: str
    engine: str
    observations: list[ReviewObservation] = field(default_factory=list)
    reviewer_attributes: list[ReviewerAttributes] = field(default_factory=list)
    business_attributes: list[BusinessAttributes] = field(default_factory=list)
    page: ReviewsPage | None = None
    history: ContributorHistory | None = None
    skipped: Counter[str] = field(default_factory=Counter)
