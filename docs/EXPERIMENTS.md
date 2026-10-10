# ReviewRing Experiments

Experiment log, using the format from `PROJECT_SPEC.md` Section 24. Only observed results are recorded.

**About this public version.** The experiments were run against one real business and a small number of its reviewers. This log reports structure and aggregate counts only. It does not name the business, its category, or its location, and it does not record request identifiers, saved-response filenames, reviewer positions, or per-reviewer details. Those would point at individual people. To re-run the scripts you supply your own values in an untracked `local_inputs.json` (see `local_inputs.example.json`). Saved responses stay in the git-ignored `data/raw/` and `data/cache/`.

**Raw data contract:** every uncached response is saved as a *sanitized raw response*: the complete SerpApi response before any application-level transformation, with the API key replaced by `[REDACTED]`. Schemas are validated against these files.

**SerpApi requests attempted in total:** 13, of which 12 succeeded (6 in the experiments below, 2 in the first live dashboard validation, 3 in the live reviewer-history validation, one of which was rejected, 1 in the entry-limit validation, and 1 in the live pagination validation).

---

## EXP-000 — API Smoke Test

**Date:** 2026-10-05 to 2026-10-10  
**Dataset:** 3 sanitized raw responses (local only)  
**Businesses:** 1 seed business, chosen by hand from 20 search results for experimental suitability only  
**Reviewers:** 8 on the first review page; 1 contributor history requested  
**SerpApi searches consumed:** 3  
**Scripts:** `experiments/exp000_maps_search.py`, `exp000_maps_reviews.py`, `exp000_contributor_reviews.py`

### Hypothesis

The three SerpApi Google Maps engines can be chained: a search returns a business identifier, the business's reviews return reviewer identifiers, and a reviewer's history returns business identifiers.

### Method

One request per engine, no pagination, through the shared cached client. Each script prints key names and counts only. The contributor for the third call was the first review on the page with a usable identifier, a rule that uses nothing about the reviewer.

### Results

- **Call #1, Google Maps Search:** 20 `local_results`. The inspected results carried `title`, `data_id`, `rating`, `reviews`, `type`, `address`, `gps_coordinates`, and a `reviews_link` whose parameters are `engine=google_maps_reviews`, `data_id`, `hl=en`.
- **Call #2, Google Maps Reviews:** a top-level `reviews` list of 8. Review keys: `position`, `link`, `rating`, `date`, `iso_date`, `iso_date_of_last_edit`, `source`, `review_id`, `user`, `snippet`, `extracted_snippet`, `likes`, and sometimes `response`. The nested `user` object carries `contributor_id`, `name`, `link`, `thumbnail`, `local_guide`, `reviews`, `photos`. A `serpapi_pagination` object offers a next-page token.
- **Call #3, Contributor Reviews:** top-level keys `search_metadata`, `search_parameters`, `contributor`, `reviews`. The history held 1 entry, with keys `place_info`, `date`, `snippet`, `translated_snippet`, `review_id`, `rating`, `likes`, `link`, `response`. `place_info` carries `title`, `address`, `gps_coordinates`, `type`, `thumbnail`, `data_id`.
- The `data_id` in that history entry equals the seed's `data_id`, so identifiers are comparable across the three engines in the observed case.

### Unexpected findings

- The first review page held 8 reviews.
- The one history returned only the seed business.
- The Contributor Reviews response used non-English keys inside `contributor.contributions` and a non-English relative `date`, although no language parameter was sent. Those keys are not stable and are not parsed.

### Limitations

Each link of the chain was observed once, on one business and one contributor.

### Decision

**PASS — API-chain feasibility only** (spec Section 12, Gate 1). This says nothing about reviewer overlap or coordination.

### Next experiment

EXP-001.

---

## EXP-001 — Reviewer Footprint

**Date:** 2026-10-10  
**Dataset:** 2 further sanitized raw responses (local only)  
**Businesses:** the seed; 7 observed in one contributor history, including the seed  
**Reviewers:** 18 across two review pages; 1 further contributor history  
**SerpApi searches consumed:** 2  
**Scripts:** `experiments/exp001_maps_reviews_page2.py`, `exp001_contributor_reviews.py`

### Hypothesis

A second review page yields more reviewers, and a reviewer who reports more reviews has a history containing businesses other than the seed.

### Method

- **Call #4:** the next review page, using the pagination token from Call #2. The script validates the saved pagination data (host, path, exact parameter names, same business) before sending anything.
- **Call #5:** one Contributor Reviews request for one second-page reviewer, selected by a fixed rule based on reported review count and nothing else. The selection is held in private local configuration and re-validated at run time.

### Results

- The second page held 10 reviews; all 18 reviews across both pages have a valid, distinct contributor identifier.
- `user.local_guide` was absent from every second-page reviewer, so the field is optional.
- A further pagination token was offered and not followed.
- The contributor history held 7 entries with 7 distinct, valid `place_info.data_id` values: the seed and 6 other businesses. No pagination was offered.
- History entries carry no reviewer identifier. A history alone cannot show who else reviewed a business.

### Unexpected findings

- Page sizes differ (8, then 10).
- The review count reported on the review page was one lower than the length of the returned history, so `user.reviews` is not an authoritative count.
- A pagination token issued five days earlier was still accepted.

### Limitations

Two contributor histories in total, against the 20 to 30 in spec Section 13. With one multi-business history there is nothing to compare it with. Only a small part of the seed's several hundred reported reviews was collected.

### Decision

**PASS — retrieval only.** The spec Section 13 criterion, reviewer overlap beyond the original business, is **not met and remains untested**. Coordination evidence is unproven.

### Next experiment

EXP-002.

---

## EXP-002 — Graph Viability

**Date:** 2026-10-10  
**Dataset:** 1 further sanitized raw response; then all 6 merged offline  
**Businesses:** 1 expanded from the contributor history; 7 in the graph  
**Reviewers:** 6 on the expanded business; 23 distinct in the graph  
**SerpApi searches consumed:** 1  
**Scripts:** `experiments/exp002_business_reviews.py`, `exp002_build_graph.py`

### Hypothesis

A business found in a contributor history can be queried for its own reviewers, and the captured responses merge into one reviewer-business graph without losing provenance or duplicating edges.

### Method

- **Call #6:** the first review page of one non-seed business from the Call #5 history, chosen by a fixed rule (first valid entry whose category string differs from the seed's).
- **Graph build:** the six responses are parsed and merged offline (`docs/ARCHITECTURE.md`). Only aggregate counts are printed.

### Results

Call #6 returned all 6 reviews the business reports. Exactly one of its reviewers is among the 18 seed reviewers: the contributor whose history led to this business. That overlap is expected by construction and is not a finding.

| Measure | Value |
|---|---:|
| Reviewers | 23 |
| Businesses | 7 |
| Unique reviewer-business edges | 29 |
| Review observations merged into those edges | 32 |
| Edges observed more than once | 3 |
| Connected components | 1 |
| Reviewer pairs sharing at least one business | 168 |
| Reviewer pairs sharing at least two businesses | 0 |

- The 168 pairs sharing one business are produced by the sampling: reviewers collected from one business's review page all share that business.
- The three edges observed twice are reviews seen on both a review page and a contributor history. Their `review_id` and rating agree in each case.
- One edge has differing relative `date` text between its two observations, fetched on different days and in different languages. Relative date text is therefore not treated as a reliable comparison field.
- Coverage: 2 businesses have a collected review page and 5 are known only from one history. Histories were collected for 2 of 23 reviewers.

### Unexpected findings

Every collected rating in this sample is 5, so rating patterns cannot be examined in it.

### Limitations

- With histories for 2 of 23 reviewers, the graph is two review pages joined by one reviewer. It cannot show overlap that exists outside those two histories.
- The exploratory rule `shared_businesses >= 2` (spec Section 14) yields no reviewer-reviewer link in this sample.

### Decision

**PASS — expansion and graph construction only.** The spec Section 14 criterion, non-trivial reviewer communities or repeated overlaps, is **not met**. Coordination evidence is unproven, and spec Gate 2 (graph viability) remains open.

### Next experiment

More contributor histories are needed before overlap can be measured. The dashboard's live investigation collects them within a confirmed request budget.

---

## Live Dashboard Validation

**Date:** 2026-10-10  
**SerpApi searches consumed:** 2  
**Scope:** one run of the dashboard's Investigate workflow against the real service, with zero reviewer histories.

### Method

The dashboard was driven in a browser through its own confirmation prompts. An independent counter at the HTTP layer capped the run at two requests. A first pass repeated an already saved search and review page to confirm that saved responses cost nothing; a second pass used a new, unrelated search.

### Results

- Saved pass: both plans stated that no request would be sent, and none was.
- New search: planned at 1 request, 1 sent, HTTP 200, 20 results, all with identifier, title, category, rating, review count, and address.
- New review page: planned at 1 request, 1 sent, HTTP 200, 8 reviews, 8 valid and distinct reviewer identifiers, nothing skipped, a further page offered and not followed.
- The dashboard rendered both investigations and stated that coordination cannot be assessed, because no histories were collected.
- Nothing the browser received contained a reviewer identifier, name, profile link, review text, pagination token, business identifier, or the API key.

### Limitations

This run collected no reviewer histories.

---

## Live Reviewer-History Validation

**Date:** 2026-10-10
**SerpApi searches attempted:** 3 (2 successful, 1 rejected)
**Scope:** the dashboard's Investigate workflow with two reviewer histories, for a business whose search and first review page were already saved.

### Method

The sanitized dashboard code was driven in a browser through its own confirmation prompts, with the saved search and review page reused. An independent HTTP-layer limit allowed two Contributor Reviews requests, only for the two reviewers the dashboard selected. The reviewer-selection rule in force was the earlier one (most reported reviews first); no `num` parameter was sent.

### Results

- **First attempt:** the test harness supplied a malformed key. One request was sent and rejected with HTTP 401. The dashboard stopped at once, made no second request, kept the review-page data, reported a partial result, and showed a fixed message. Nothing was saved.
- **Second attempt:** the plan stated at most 2 requests; 2 were sent, both HTTP 200. Each response matched its requested contributor. Each history listed 10 entries, all with a valid business identifier, rating, review identifier, and date text; nothing was skipped.
- **Graph:** 8 reviewers, 21 businesses (the investigated one and 20 from the histories), 28 links, 1 connected component.
- **Truncation:** both reviewers' profiles report more than 500 reviews, yet 10 entries were returned each, and neither response contained a pagination object. The investigated business itself appears in neither history, although both reviewers are on its review page.
- **Overlap:** the two histories share no business. No reviewer pair shares two or more businesses.
- **Privacy:** nothing the browser received contained a reviewer identifier, name, profile link, review text, token, business identifier, saved-response filename, or the API key.

### Unexpected findings

- A history as returned by default is a 10-entry window, and the response gives no sign that it is incomplete.
- A history can omit a link already known from a review page.
- Selecting the reviewers who report the most reviews selects exactly those whose histories are most truncated.

### Limitations

One pair of reviewers, 10 entries each out of several hundred. This cannot rule out overlap between those two reviewers, let alone others. Whether `num` enlarges the window has not been tested.

### Decision

**PASS — live history collection works.** It is not evidence about coordination. It led to the changes recorded in `docs/ARCHITECTURE.md` under "Contributor history windows": histories are reported as windows, the entry limit is configurable, and reviewer selection prefers histories that fit the window.

---

## Entry-Limit Validation

**Date:** 2026-10-10
**SerpApi searches attempted:** 1 (successful)
**Scope:** one Contributor Reviews request with an entry limit of 50, for a reviewer whose default history was already saved.

### Results

- 50 entries were requested and 50 were returned.
- The first 10 entries were the same as in the default response for that reviewer.

### Limitations

One reviewer, one limit. Larger limits, and whether a full window ever means a complete history, were not tested.

### Decision

**PASS — the entry limit enlarges the history window.** 50 is the dashboard's default.

---

## Live Pagination Validation

**Date:** 2026-10-10
**SerpApi searches attempted:** 1 (successful)
**Scope:** the dashboard's Investigate workflow with two review pages and no histories, for a business whose search and first review page were already saved.

### Results

- The plan stated at most 1 new request; 1 was sent, using the next-page token the saved first page had returned.
- The second page held 10 reviews.
- The response provided a token for a further page, which was not used.

### Limitations

One business, one further page. Third and fourth pages, and a response without a token, were not observed live.

### Decision

**PASS — bounded review pagination works through the dashboard.**

---

## EXP-003 — Ring Expansion

Not run against the real service. It requires a reviewer pair meeting the overlap indicator in real data, and none has been observed. Expansion is implemented and has run only on a simulated SerpApi in tests and on the fictional pages of the synthetic demo.
