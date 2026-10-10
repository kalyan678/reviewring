# ReviewRing Architecture

This document records the approved high-level architecture from `PROJECT_SPEC.md` (Section 4) and the parts of the analysis layer that have been built and verified against captured responses (Graph Foundation v0.1, below). Signals, community detection, scoring, presentation, and storage formats beyond raw JSON are not yet defined.

## Pipeline

```
Google Maps Search
→ Target Business
→ Google Maps Reviews
→ Contributor IDs
→ Contributor Reviews
→ Reviewer-Business Bipartite Graph
→ Coordination Signals
→ Community / Dense-Subgraph Detection
→ Coordination Risk
→ Evidence Explanation
→ Ring Expansion
```

The primary graph is bipartite (`Reviewer ── REVIEWED ── Business`). A reviewer-reviewer projection may be derived from it for community analysis.

## Layer separation

| Layer | Responsibility | Constraints |
|---|---|---|
| Collection | Requests SerpApi Google Maps Search, Reviews, and Contributor Reviews. | Checks the cache before every request. Does no analysis. |
| Raw / cache storage | Stores sanitized raw responses: the complete SerpApi JSON response before any application-level transformation, with secrets (the API key) redacted before persistence. | Written before any parsing or transformation. Never trimmed, normalized, or overwritten. Kept local (`data/raw/`, `data/cache/`, git-ignored). |
| Analysis | Parses raw responses into typed records and builds the graph, signals, and community/dense-subgraph detection. | Schemas derived from captured Experiment 0 responses; unconfirmed fields are optional. Reads stored data only; makes no API calls. |
| Scoring | Computes the coordination risk score and its explanation from observable features. | Deterministic and explainable. No LLM involvement. Weights and cutoffs are provisional until set by experiments. |
| Presentation | Displays graphs, clusters, evidence, and ring expansion. | Consumes analysis and scoring outputs; contains no detection logic. Anonymizes reviewers. |

Data flows in one direction: collection → storage → analysis → scoring → presentation. Analysis must be reproducible from cached data alone.

## Graph Foundation v0.1 (analysis layer, implemented)

Builds the reviewer-business bipartite graph from saved raw responses. It makes no network request and does not import the SerpApi client. It computes structure and coverage only: no coordination signal, score, projection, or community detection.

| Module | Responsibility |
|---|---|
| `src/reviewring/models.py` | Typed records (frozen dataclasses). `None` means "not returned or not usable", never a default. |
| `src/reviewring/parsers.py` | Pure functions from one loaded response to records. One parser per engine. |
| `src/reviewring/graph.py` | Merges parsed responses into the graph; aggregate metrics; coverage; pseudonymous edge export. |
| `src/reviewring/privacy.py` | Keyed pseudonyms for identifiers that leave the process. |
| `experiments/exp002_build_graph.py` | Offline entry point: reads the six verified raw files by fixed name and prints aggregate metrics. |

Records are plain dataclasses filled by hand-written, type-checking parsers, not Pydantic models. A validation library reports the offending input value in its errors, and here those values are contributor IDs, names, and review text. The parsers' errors never quote response content.

### Identity

- **Reviewer node:** the `contributor_id` string.
- **Business node:** the `data_id` string. The same value was observed for one business across Google Maps Search, Google Maps Reviews, and Contributor Reviews.
- **Edge:** one (reviewer, business) pair, however many times it was observed.

The subject of a response comes from the request metadata SerpApi echoes in `search_parameters`, never from the entries:

| Engine | Business from | Reviewer from |
|---|---|---|
| `google_maps_reviews` | `search_parameters.data_id` | each review's `user.contributor_id` |
| `google_maps_contributor_reviews` | each entry's `place_info.data_id` | `search_parameters.contributor_id` |

Contributor history entries carry no reviewer identifier. A response whose subject is missing or malformed is rejected whole. An entry without a usable identifier is skipped and counted by reason.

### Fields used

Only fields observed in captured responses, all optional except the identifiers:

- Observation: `review_id`, `rating`, `date` (relative text), `iso_date` (Google Maps Reviews only).
- Reviewer: `user.reviews`, `user.photos`, `user.local_guide`; `contributor.local_guide`.
- Business: `place_info.type`; top-level `place_info.reviews` (reported review total).
- Coverage: `serpapi_pagination.next_page_token`, `search_parameters.next_page_token`.

`user.reviews` is a self-reported total. It is not the number of history entries Contributor Reviews returns: the two differed in the one case observed. `contributor.contributions` is not parsed, because its key names changed with the response language.

Reviewer names, profile links, avatars, review text, business titles, addresses, and coordinates are not read into the model.

### Merge policy

Nothing is overwritten or resolved; every observation is kept on its edge with its source file, engine, and position.

- Observations of a pair with the same `review_id` are one review seen more than once (for example on a review page and in a contributor history).
- An observation without a `review_id` is attributed to its (reviewer, business) pair.
- Several distinct `review_id` values on one pair stay on one edge and are flagged `multiple_review_ids`. Repeat reviews never add edges.
- For what should be one review, differing `rating` or `iso_date` values are flagged as conflicts. A missing value never conflicts with a known one. When a pair has several review IDs, values are compared only within each review.
- Differing relative `date` text is flagged separately as `date_text`. It varies with fetch time and response language, so it is weak evidence of a real disagreement.
- Reviewer and business attributes are kept as one record per source, not merged into a single value.
- A source file may be merged only once.

### Coverage

The graph is a sample, and the coverage records say how partial it is.

- Per business: number of Google Maps Reviews pages captured, reviewers captured from those pages, reported review total, and pagination tokens offered but not followed. A token counts as followed when another captured request was made with it.
- A business with no captured page is **history-only**: it is known from a contributor history, and its other reviewers are unobserved.
- Per reviewer: whether a contributor history was fetched, and whether that history offered a further page. A reviewer without a fetched history has only the edges found on review pages.

Degrees, components, and shared-business counts must be read against coverage. Reviewers collected from one business's review page share that business by construction.

### Privacy boundary

- Raw `contributor_id` and `data_id` values stay inside the process as join keys. They are never printed, logged, put in error messages, or written to documentation.
- Anything exported or logged per node uses `privacy.pseudonym`: HMAC-SHA256 under a local secret, with the identifier kind (`reviewer` or `business`) mixed into the message so the two kinds cannot be matched against each other. Output form: `R_` or `B_` plus 16 hex characters.
- The secret is read from `REVIEWRING_PSEUDONYM_KEY` (environment or `.env`, at least 32 characters). It is never generated, defaulted, or written by the code; a missing or short key is an error. Changing the key changes every pseudonym.
- The aggregate report uses no pseudonyms and needs no key.
- Pseudonyms are not anonymity. Anyone holding the key can test a guessed identifier, and an edge list under pseudonyms still describes who reviewed what. Pseudonymous edge lists are not published alongside anything that identifies a business, and documentation does not record which categories of place a particular reviewer has reviewed.
- Presentation-layer labels (`R001`, `R002`, ...) are a separate, later concern.

## Application v0.1 (presentation layer, implemented)

A local dashboard over the graph foundation. It adds no second graph model and no parsing or merge logic of its own.

| Module | Responsibility |
|---|---|
| `src/reviewring/loader.py` | Reads `data/raw/*.json`, recognising responses by the engine in their own `search_parameters`. Uses no filenames: raw filenames embed an unkeyed hash of the request parameters. A request saved twice is used once. |
| `src/reviewring/indicators.py` | Structural overlap indicators: reviewer pairs sharing at least two businesses, and the groups those pairs connect. The minimum of two is the provisional rule from spec Section 14. No score or probability. |
| `src/reviewring/synthetic.py` | Fictional demo fixtures, rendered as response-shaped data and passed through the real parsers and merge. Identifiers carry a `synthetic-` prefix. |
| `src/reviewring/views.py` | The privacy boundary: turns a graph into the only data the page receives. |
| `src/reviewring/app.py`, `src/reviewring/web/` | Standard-library HTTP server bound to the loopback interface, one static page, one JSON endpoint (`/api/view?mode=real|synthetic`). |

**Framework.** The spec (Section 17) names Streamlit as the initial application framework. v0.1 uses the Python standard library and a static page instead: no UI framework was installed, and this adds no dependency, loads nothing from outside, sends no telemetry, and allows a strict content-security policy. The view models are framework-independent, so moving to Streamlit later would replace only `app.py` and `web/`.

**Real mode sends aggregates only.** Reviewers with the same connections are merged into one node carrying a count; businesses with a collected review page get a letter label; businesses known only from histories are one node. No identifier, name, category, review ID, token, filename, or per-reviewer link is sent.

**Small reviewer sets are not drawn.** A reviewer node is emitted only when at least `MIN_AGGREGATE_GROUP_SIZE` (5) reviewers share its connection pattern. Smaller sets, including a single reviewer, are left out of the network together with their links, and the view reports how many reviewers and links were left out. Aggregate metrics still include them. This is a cautious display choice, not a formal anonymity guarantee: the size of a hidden set, and sometimes which businesses it touches, can be inferred by subtracting the drawn counts from the totals. No identifier is exposed either way.

**The collected sample is everything saved, not one investigation.** It merges all saved responses, including those written by live investigations. When the result falls into separate sets with no reviewer in common, the view says how many and that they are not one investigation.

**Synthetic mode sends individual nodes**, because every node is fictional, and is labelled as synthetic in the data and on the page.

**Ratings.** The view reports the ratings of collected reviews and, separately, the rating and review total Google reports for a business (`place_info.rating`, `place_info.reviews`). It computes no average of collected reviews and no adjusted rating.

**Server.** Three fixed static files and a small JSON API; request paths are never mapped to the filesystem. Exception text is never returned to the page, and requests are not logged.

## Live Investigation v0.1 (collection from the dashboard, implemented)

Status: implemented. Live validation so far: one run against the real SerpApi service on 2026-10-10, covering one business search and one first page of reviews (two requests, both successful), with zero reviewer histories. Reviewer-history collection, failure handling, and overlap detection in a live investigation have been tested only against a simulated SerpApi.

One bounded investigation: Google Maps Search, then the first page of Google Maps Reviews for a chosen business, then Contributor Reviews for a chosen number of that page's reviewers. No further review pages, no requests for businesses found in histories, no recursion.

| Module | Responsibility |
|---|---|
| `src/reviewring/investigation.py` | Stateless collection. Builds the three verified request shapes, chooses which reviewers to expand, counts new requests against a budget, and returns parsed responses. Makes no HTTP call itself: the caller passes in the client's `search` and `is_cached`. |
| `src/reviewring/live.py` | In-memory state for one server process: plans, search results, investigations, the session token, and the per-process request ceiling. |
| `src/reviewring/serpapi_client.py` | Unchanged behaviour; two read-only helpers added (`is_cached`, `api_key_configured`). Still the only code that performs a SerpApi request or writes raw responses. |

Responses are parsed by the existing parsers and merged by `build_graph`, then shown through the same aggregate view as saved real data. There is no second graph model.

### Endpoints

| Method and path | Purpose | Can send a SerpApi request |
|---|---|---|
| `GET /api/session` | Session token, limits, whether a key is configured | No |
| `POST /api/search/plan` | Validate a query; state the maximum cost | No |
| `POST /api/search` | Run a planned search; return the selection list | At most 1 |
| `POST /api/investigations/plan` | Validate a selection; state the maximum cost | No |
| `POST /api/investigations` | Start a planned investigation in the background | At most 1 + histories |
| `GET /api/investigations/{id}` | Progress, then the aggregate result | No |

### Request budget

- A search: 1 new request. An investigation: 1 + the chosen number of histories, 0 to 8 (default 3), so at most 9.
- A plan is single-use, expires after 10 minutes, and records the maximum number of new requests. Execution accepts only a plan the server issued, and enforces the plan's number, so the confirmation shown in the browser cannot be skipped or enlarged.
- Saved responses are reused and not counted. When the review page is already saved, the plan reads it locally and states exactly how many histories are new; otherwise it states the maximum and says the rest is unknown.
- One server process attempts at most 30 new requests in total, as a safety net.
- Collection stops at the first failure of any kind and keeps what it has; `collect` never raises. One investigation runs at a time.
- Final states: *complete*; *partial* (collection stopped early, a view of what was collected is shown); *failed* (nothing was collected); *unviewable* (responses were collected and saved, but the view could not be built, so none is offered). Messages are fixed text; exception text never reaches the browser.

Reviewer selection is a fixed coverage rule, not a ranking of suspicion (`choose_reviewers`). In order: reviewers whose reported review count is between 2 and the entry limit, in page order; then those with no reported count, in page order; then those reporting more than the limit, fewest first. A reviewer reporting one review is skipped because that history would only repeat the business. The aim is histories that can be compared in full. Prolific reviewers come last because their histories are returned as small windows and because they share popular places for ordinary reasons. Only the reported count and the page position are used.

### Contributor history windows

A retrieved history is a window onto a reviewer's reviews, and the code never treats it as complete by default.

- **Documented contract** (SerpApi, checked 2026-10-10): `contributor_id` and `engine` are required; `num` is optional, "10 (default)", "should not be larger than 200". The documentation lists no `next_page_token` parameter and shows no pagination object, although a release note mentions a `serpapi_pagination` block.
- **Observed behaviour:** with no `num`, two histories returned exactly 10 entries each for profiles reporting over 500 reviews, with no pagination object. An earlier history returned 7 entries for a reviewer whose review page reported 6. `num` itself has not been sent in a live request by this project.
- **Entry limit:** `history_params(contributor_id, limit)` sends `num` only when the limit differs from the API default of 10, so default requests are identical to the verified ones and their saved responses are still reused. Any other limit is part of the request and of its cache key; a history saved at one limit is never reused or overwritten at another. Limits: 10 to 200, default 50. A larger window collects and stores more personal data per reviewer, which is why the default is not the maximum. `num` is a ceiling: nothing assumes that many entries come back.
- **Coverage per history** (`graph.history_coverage`): entries returned, distinct businesses, the limit in force, whether the limit was reached, whether a further page was offered, and a reported total when available (the profile's own count in the history response, else an unambiguous count from a review page).
- **Status**, deliberately not a claim of certainty:
  - *partial*: a further page is offered, or the reported total exceeds the entries returned by more than one;
  - *unknown*: the window was filled and no total is available;
  - *apparently complete*: nothing observed says more exists.
  A missing next-page marker is never taken as completeness. Reported totals are approximate, so a difference of one is ignored.
- **What the page shows:** counts of retrieved histories by status, the entry limit, entries retrieved, and the share of the reported reviews they cover. With fewer than five histories the share is given only as a coarse band and the reported total is withheld, since reported counts are reviewer-level data.

### Security and privacy

- Loopback only. Every request must carry this server's own `Host` header, which blocks DNS rebinding.
- Requests that can spend credits or read an investigation must be same-origin, send JSON, and carry a per-process random token. The token is served only to same-origin readers, no CORS header is ever sent, and the page keeps the token in memory. No cookie or browser storage is used.
- Business `data_id` values and contributor IDs stay in the server. The browser holds random references for the businesses in a search list, and a random reference for an investigation.
- The search list shows what a Google Maps listing shows: name, category, rating, review count, address. Investigation results are aggregates under the same rules as real mode, including the minimum drawn group size. The business the user selected is labelled with its listing name; businesses found in histories keep anonymous labels, and no reviewer is ever named.
- SerpApi error text is never forwarded, because it can repeat request parameters such as a contributor ID. The browser receives one of a fixed set of messages.
- Raw responses are written only by the client, sanitized, to the git-ignored `data/raw/` and `data/cache/`.

### Interpretation

The view states whether overlap could be assessed at all, and the headline follows the same rule in every mode. With fewer than two retrieved histories the headline is that coordination cannot be assessed; an absence of overlap is not reported as a finding. With two or more and nothing overlapping, the headline is "No repeated overlap observed in the retrieved review windows", and the assessment says how many reviewers could not be compared and how many retrieved histories are partial or of unknown completeness. The page never states that no overlap exists. With overlap it reports counts and calls it a pattern to look into. It never reports a score, a probability, or a conclusion about any reviewer or business.

## Experiment scripts and private local inputs

The scripts in `experiments/` reproduce the feasibility experiments in `docs/EXPERIMENTS.md`. They spend SerpApi credits and are not used by the dashboard.

What they were run on is not in the repository. The seed business, the search query, the saved-response filenames, and the position of the reviewer whose history was requested are read from `local_inputs.json`, which is git-ignored (`src/reviewring/local_inputs.py`; `local_inputs.example.json` holds fictional values). A saved-response filename embeds an unkeyed hash of its request, including any contributor ID, and a pinned position points at one person, so neither may appear in tracked files. Values are validated before use, a saved-response name must be a plain filename inside `data/raw/`, and a missing or invalid file stops a script before any request.

### Limitations

- Shared-business pairs are counted by enumerating reviewer pairs per business, which is quadratic in a business's reviewer count. It is adequate for the captured sample and will need revisiting before large review pools are loaded.
- Connected components use a small built-in union-find; no graph library is a dependency yet.
- Whether Contributor Reviews paginates, and with which request parameter, has not been observed. A history offering a further page is counted but cannot yet be marked as followed.
