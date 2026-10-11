# ReviewRing

**Tagline:** Follow the reviewers. Find the coordination.

**Hackathon:** SerpApi India Hackathon 2026  
**Primary Track:** Commerce & Market Intelligence  
**Secondary positioning:** Knowledge & Public Interest  
**Development window:** October 5–10, 2026

---

> ## Original vision versus implemented release
>
> **This document is the original product and design specification, kept as written. It is not a description of the released application.** Several sections describe features that were proposed and never built. Where this document and the released code differ, `README.md` and `docs/ARCHITECTURE.md` describe what exists.
>
> | Area | Status in the release |
> |---|---|
> | Business search and review collection (5.1, 5.2) | Implemented. Run against the real service: one search, a first and a second review page. |
> | Reviewer histories (5.3) | Implemented. Run against the real service for a few reviewers. Each history is a window of recent reviews, not a full record. The 20 to 30 histories of Experiment 1 were never collected. |
> | Reviewer-business graph (5.4) | Implemented. Addresses, coordinates, reviewer names and review text are deliberately not read into the graph. |
> | Overlap indicators (5.5, signal 1) | Implemented as reviewer pairs sharing two or more businesses, and the groups those pairs form. No such overlap has been observed in real data; it is shown only on fictional data. |
> | One-hop expansion (5.6) | Implemented as a separate, confirmed step. Tested on simulated and fictional data only; never run against the real service. |
> | Privacy-preserving visualization (Section 9) | Implemented, and stricter than proposed: real reviewers are never drawn individually, sets of fewer than five are not drawn at all, and businesses reached by expansion are not named. |
> | Coordination risk scoring (Section 7) | **Not implemented.** No score, band, weight or cutoff exists anywhere in the release. |
> | Community or dense-subgraph detection (Section 4) | Not implemented. Groups are the connected sets formed by overlapping pairs. |
> | Temporal, geographic and other advanced signals (5.5 signals 2 to 7, Section 10) | Not implemented as signals. Categories and ratings are shown as context only. |
> | Coordination-adjusted rating and other stretch features (Sections 22, 23) | Not implemented. |
> | Streamlit, NetworkX, pandas, pydantic schemas (Section 17) | Not used. The application is a standard-library server with a static page. |
> | No-code onboarding and hosted real investigations | Not implemented and not part of this specification. Real investigations need a local install and the user's own SerpApi key. A hosted copy serves the fictional demo only. |
>
> **Decision gates (Section 16).** Gate 1, API viability, passed. Gate 2, graph viability, was never passed: real data showed no reviewer pair sharing two businesses. The application was built anyway, on the project owner's decision, and the release says so.
>
> **Tagline.** The released application uses "Follow the reviewers. Explore the overlap." The original tagline above is kept as history; the release does not claim to find coordination.

---

# Specification Status

LOCKED:

- project thesis
- SerpApi-centric workflow
- reviewer-business graph approach
- responsible-use principles
- MVP capability boundaries
- ring-expansion concept
- no LLM-based fraud verdict

NOT YET LOCKED:

- exact SerpApi schemas
- optional API fields
- graph thresholds
- scoring weights
- community-detection parameters
- UI implementation details

These are intentionally data-driven and will be finalized after the feasibility experiments (Experiments 0–3).

---

# 1. Project Goal

ReviewRing is a graph-based system for detecting unusual coordinated reviewing behavior across Google Maps businesses.

Most fake-review approaches focus primarily on review text.

ReviewRing starts from a different hypothesis:

> Coordinated reviewing behavior can leave structural patterns across reviewers, businesses, locations, ratings, and time that are difficult to observe by reading individual reviews.

ReviewRing therefore follows reviewers across the businesses they have reviewed and constructs a reviewer-business network.

The system does **not** declare that a reviewer, business, or review is fraudulent.

It identifies and explains **coordination signals** that may warrant further investigation.

---

# 2. Core Product Insight

A single review tells us relatively little.

A reviewer's footprint across many businesses can tell us considerably more.

If multiple reviewers repeatedly review the same unrelated businesses, particularly across different categories or locations, that creates a structural pattern worth investigating.

The core relationship is:

Business  
→ Reviews  
→ Reviewers  
→ Contributor histories  
→ Other businesses  
→ Shared reviewers  
→ Coordination graph

This recursive relationship is the foundation of ReviewRing.

---

# 3. Why SerpApi Is Essential

ReviewRing depends on a chain of SerpApi Google Maps APIs.

**Field availability note:** The fields listed in this section are the information ReviewRing intends to use. None of them is assumed to be present in every response. Presence, naming, nesting, and format must be confirmed against raw responses captured in Experiment 0 (see Section 12). Fields not confirmed as consistently present are treated as optional.

## Stage 1 — Business Discovery

Google Maps Search discovers businesses for a category/location query.

Example:

`bakery in Exampleville`

Important fields include, where returned by the API:

- business title
- `data_id` (expected to be the business identifier used to request reviews; must be confirmed in Experiment 0)
- rating, where available
- review count, where available
- category/type, where available
- GPS coordinates, where available

## Stage 2 — Reviews

Google Maps Reviews retrieves reviews for a selected business.

Important fields include, where returned by the API:

- review ID, where available
- contributor/reviewer identifier (required for Stage 3; its presence and format must be confirmed in Experiment 0)
- rating
- review date, where available — may be relative text (e.g., "2 months ago") and/or an absolute date; precision is not assumed
- review text/snippet, where available (reviews may have no text)
- reviewer review count, where available
- reviewer photo count, where available
- Local Guide information, where available

Recent reviews should initially be prioritized.

## Stage 3 — Contributor Expansion

Google Maps Contributor Reviews uses the reviewer/contributor identifier to retrieve the reviewer's footprint across other Google Maps businesses.

Important information includes, where returned by the API:

- reviewed place (exact field names and nesting of place details to be confirmed from raw responses)
- place `data_id`, where available (required to link the same business across contributors; availability must be confirmed in Experiment 0)
- place title
- address, where available
- GPS coordinates, where available
- business category/type, where available
- rating
- review text/snippet, where available
- review-date information, where available — may be relative or coarse, not a precise timestamp

This creates:

`Reviewer → Businesses reviewed`

Combining multiple contributor histories creates:

`Reviewer ↔ Business ↔ Reviewer`

That graph is the primary dataset used by ReviewRing.

SerpApi must remain central to the final product. It should not appear as an incidental search API added to an otherwise independent application.

---

# 4. Core Architecture

The logical pipeline is:

Google Maps Search  
↓  
Target Business  
↓  
Google Maps Reviews  
↓  
Contributor IDs  
↓  
Contributor Reviews  
↓  
Reviewer–Business Bipartite Graph  
↓  
Coordination Signals  
↓  
Community / Dense-Subgraph Detection  
↓  
Coordination Risk  
↓  
Evidence Explanation  
↓  
Ring Expansion

The primary graph should remain bipartite:

`Reviewer ── REVIEWED ── Business`

A reviewer-reviewer projection may subsequently be created for community analysis.

Two reviewers become connected when they have reviewed one or more common businesses.

Repeated overlap across unrelated businesses should contribute more strongly than overlap at extremely popular/common places.

---

# 5. MVP — LOCKED SCOPE

The MVP consists of six capabilities.

## 5.1 Business Discovery

Allow the user to search for businesses by category/location.

Example:

`bakeries in Exampleville`

Return candidate businesses.

## 5.2 Review Collection

Select one business and retrieve recent reviews.

Persist the complete raw API response before parsing or transformation.

Extract relevant reviewer identifiers and review metadata where returned by the API.

## 5.3 Reviewer Expansion

For selected reviewers, retrieve contributor-review histories.

Cache contributor responses, preserving the complete raw JSON.

The number of reviewers selected for expansion is provisional and will be set from Experiment 0–3 results and available API credits.

Never repeatedly request the same contributor during development unless explicitly required.

## 5.4 Graph Construction

Construct a reviewer-business bipartite graph.

Reviewer node:

- anonymized reviewer ID
- review count where available
- photo count where available
- Local Guide status where available

Business node:

- data ID (where returned; required for cross-contributor linking)
- anonymized/display-safe name
- category where available
- location/address where available
- coordinates where available

Edge:

- reviewer reviewed business
- rating where available
- date information where available (stored as returned; may be relative or coarse, not assumed to be a precise timestamp)

Node and edge attributes beyond the identifiers are optional and must tolerate missing values.

## 5.5 Coordination Detection

Initial deterministic signals:

1. repeated co-reviewing
2. cross-category overlap
3. geographic dispersion where location data is available (contextual signal only; never proof of wrongdoing)
4. rating extremity
5. temporal/burst overlap where supported by available dates (coarse windows only; no precise timing inference)
6. account/reviewer thinness where reviewer counts are available
7. text similarity only if time permits

All thresholds used by these signals are provisional (see Section 7, Provisional Parameters).

No LLM should determine whether a review is fraudulent, or determine coordination.

## 5.6 Ring Expansion

Given a suspicious coordination cluster:

reviewers  
→ their reviewed businesses  
→ businesses shared by multiple cluster members  
→ expanded network

This is the primary demonstration feature.

---

# 6. Non-Goals

Do NOT build these during MVP development:

- supervised fake-review classifier
- graph neural network
- custom transformer
- Neo4j
- vector database
- Kafka
- microservices
- nationwide crawler
- browser extension
- mobile application
- complicated authentication
- elaborate LLM agent architecture

Do not introduce infrastructure unless it solves an observed requirement.

---

# 7. Coordination Risk

> **PROPOSED AND NOT IMPLEMENTED.** Nothing in this section exists in the released application. There is no coordination risk score, no "High Coordination Signal" or other band, and no weights or cutoffs. The figure `81 / 100` below was an illustration in a proposal; it was never computed from data and has not been validated.

The system should never display:

`87% fake`

or make equivalent fraud accusations.

Instead use:

**Coordination Risk Score**

Example:

`81 / 100 — High Coordination Signal`

(Illustrative only. The score value and the "High" band label are examples; risk-score cutoffs are not locked.)

The score must be derived from observable features.

Potential components:

- co-review density
- repeated business overlap
- cross-category overlap
- geographic dispersion
- rating extremity
- temporal burst participation
- reviewer/account characteristics
- optional text similarity

Exact weights are NOT locked.

Weights and thresholds must be determined after experimentation with real data.

Every score must be explainable.

## Provisional Parameters

The following are provisional and NOT locked. They must be determined from Experiment 0–3 results:

- graph overlap thresholds (e.g., the exploratory `shared_businesses >= 2` rule in Section 14)
- community-detection thresholds/parameters
- coordination-score weights
- risk-score cutoffs (band boundaries such as "High Coordination Signal")
- number of reviewers sampled (e.g., the approximate 20–30 in Section 13)
- text-similarity thresholds

Any number appearing elsewhere in this document for these parameters is a starting point for experimentation, not a specification.

---

# 8. Responsible Interpretation

ReviewRing detects unusual reviewing patterns.

It does not prove:

- a review was purchased
- a reviewer was paid
- a business intentionally manipulated reviews
- a reviewer physically visited or did not visit a location
- fraud occurred

Preferred terminology:

- coordination signal
- unusual reviewer overlap
- suspicious structural pattern
- coordination risk
- reviewer network
- anomalous behavior
- evidence requiring investigation

Avoid:

- scammer
- fake reviewer
- fraudulent business
- review farm confirmed
- bought reviews

unless independently established by authoritative evidence.

---

# 9. Privacy / Demo Safety

Public demonstration should minimize unnecessary identification.

Where appropriate:

- hash/anonymize reviewer names
- represent reviewers as `R001`, `R002`, etc.
- avoid displaying profile photographs
- consider anonymizing business names in screenshots/demo
- avoid accusing identifiable businesses
- explain methodology and limitations

The product demonstrates coordination analysis, not adjudication.

---

# 10. Important Methodological Constraint

Do NOT implement "impossible travel" as a fraud signal. It is explicitly out of scope for the MVP.

Contributor-review dates are not assumed to be precise timestamps. Where returned, they may be relative (e.g., "3 months ago"), coarse, inconsistent across responses, or missing, and are insufficient for precise travel-time inference. Reviews may also be written well after a physical visit.

Date information may only support coarse temporal grouping (e.g., burst overlap within broad windows), and only where Experiment 0 shows the returned dates are usable for that purpose.

Instead use:

**Geographic Dispersion**

Example:

A reviewer has highly concentrated extreme ratings across many geographically distant businesses/categories.

Geographic dispersion contributes context but should never independently imply wrongdoing.

---

# 11. Experimental Question

Before building the product, answer:

> Does real SerpApi Google Maps contributor data contain sufficient reviewer-business overlap to produce meaningful coordination graphs?

This is the project's primary feasibility question.

Application development should not begin until this has been tested.

---

# 12. Experiment 0 — API Viability

Target budget:

approximately 3–5 SerpApi searches.

Test:

Google Maps Search  
→ one business

Google Maps Reviews  
→ one or more contributor IDs

Contributor Reviews  
→ reviewed-business footprint

Save every complete sanitized raw JSON response BEFORE any parsing or application-level transformation. Preserve the response structure and content as received, except that secrets such as API keys must be redacted before persistence.

Raw responses are the evidence used to validate our schemas. They must not be trimmed, normalized, or overwritten by parsed output.

From the raw responses, record for each endpoint:

- which fields are actually present
- which fields are absent or inconsistently present
- field names, nesting, and value formats (in particular date formats and identifier formats)

Derive and validate Pydantic schemas against these captured responses (see Section 25). Fields not confirmed as guaranteed must be `Optional`.

Success criterion:

We can reliably construct:

`Business → Reviewer → Other Businesses`

using identifiers actually present in the captured responses.

---

# 13. Experiment 1 — Reviewer Footprints

Retrieve approximately 20–30 contributor histories from one selected target business, subject to available API credits. This sample size is provisional and may be adjusted based on Experiment 0 results.

For each contributor calculate:

- number of observed reviews
- number of unique businesses
- approximate geographic diversity where location data is available
- category diversity where available
- shared businesses with other reviewers

Success criterion:

At least some reviewer overlap exists beyond the original target business.

---

# 14. Experiment 2 — Graph Viability

Construct a bipartite graph:

Reviewer ↔ Business

Measure:

- nodes
- edges
- connected components
- reviewer degree
- business degree
- repeated reviewer overlap

Create a reviewer projection.

Initial exploratory reviewer connection rule:

`shared_businesses >= 2`

This threshold is provisional. Overlap and community-detection thresholds should be changed based on observed data.

Success criterion:

Non-trivial reviewer communities or repeated overlaps exist.

---

# 15. Experiment 3 — Ring Expansion

For interesting reviewer clusters:

identify all businesses reviewed by multiple cluster members.

Calculate:

- businesses shared by >=2 members
- businesses shared by >=3 members
- cross-category overlap where category data is available
- geographic distribution where location data is available
- rating patterns

The >=2 / >=3 cut points are exploratory, not locked.

Success criterion:

A visually understandable reviewer-business network exists that can demonstrate the concept.

This is the critical hackathon decision gate.

---

# 16. Decision Gates

## Gate 1 — API viability

Can we obtain:

Business  
→ Reviews  
→ Contributor  
→ Other businesses?

If NO:

stop and diagnose.

If YES:

continue.

## Gate 2 — Graph viability

Do contributor histories create meaningful reviewer overlap?

If NO:

reconsider sampling/search strategy before building UI.

If YES:

continue.

## Gate 3 — Demo viability

Can we produce at least one compelling, responsibly anonymized coordination graph?

If YES:

freeze experimentation and build the product.

---

# 17. Initial Technology Stack

Python 3.11

Dependency/environment management:

`uv`

Initial experimentation:

- Jupyter
- httpx
- pandas
- pydantic
- python-dotenv

After graph viability:

- NetworkX
- scikit-learn
- appropriate community-detection package if necessary

Application:

- Streamlit initially

Visualization:

Evaluate Plotly, PyVis, or another lightweight graph visualization approach after the graph experiment.

Persistence:

SQLite only if persistent structured storage becomes necessary.

Raw API responses should initially be stored as JSON.

---

# 18. Caching Strategy

SerpApi credits are limited.

Every API response should be cached.

Conceptual cache key:

`engine + normalized request parameters`

Before issuing a SerpApi request:

1. construct request key
2. check local cache
3. return cached response if present
4. otherwise call SerpApi
5. save the complete raw JSON response before any parsing or transformation
6. return response

Contributor responses should be especially aggressively cached because the same reviewers may appear across multiple businesses.

---

# 19. LLM Policy

An LLM is NOT required for core detection.

The graph and deterministic feature pipeline produce coordination signals.

An LLM may later be used to:

- explain detected patterns in plain English
- summarize evidence
- classify ambiguous categories
- help development

An LLM must NOT produce the coordination score or decide whether a reviewer/business is fraudulent.

Claude Pro and ChatGPT are development tools, not runtime dependencies unless explicitly decided later.

---

# 20. Development Principles

Optimize for hackathon completion.

Prefer:

- simple code
- deterministic behavior
- cached data
- explainable calculations
- typed schemas
- small modules
- reproducible experiments
- polished demo

Avoid:

- premature abstraction
- unnecessary frameworks
- speculative infrastructure
- architecture rewrites
- scope expansion

Every new feature must answer:

> Does this materially improve the judging criteria or three-minute demo?

If not, defer it.

---

# 21. Proposed Demo Story

Opening:

> Fake reviews don't always look fake. But coordinated reviewers can leave a network behind.

Search/select a business.

Show review collection.

Reveal reviewer network.

Highlight an unusual cluster.

Explain the structural signals.

Show:

- repeated reviewer overlap
- shared businesses
- categories
- geography (as context, not as proof)
- rating patterns

Then trigger:

**Expand Ring**

Reveal additional businesses connected through the reviewer cluster.

Final message:

> ReviewRing doesn't decide whether a review sounds fake. It follows reviewer behavior across the live web and shows the coordination evidence.

---

# 22. Potential Wow Moment

If supported by the observed data, display:

**Coordination-adjusted rating**

Example:

`4.8★ → 3.7★` (illustrative values)

This means:

rating after excluding reviews associated with the selected coordination cluster.

It must NOT be labeled:

- true rating
- real rating
- corrected rating

It is a scenario analysis based on removing the identified cluster. It does not imply that the excluded reviews are fake.

Only implement this if the graph analysis supports it.

---

# 23. Stretch Features

Only after MVP completion:

1. Coordination-adjusted rating
2. Ring Signature / Ring DNA
3. Ring-to-ring similarity
4. multilingual semantic review similarity
5. MinHash near-duplicate detection
6. LLM evidence summaries
7. richer graph visualization
8. comparison between multiple target businesses

These must never block submission.

---

# 24. Experiment Documentation

Maintain:

`docs/EXPERIMENTS.md`

For every experiment record:

## EXP-XXX — Name

**Date:**  
**Dataset:**  
**Businesses:**  
**Reviewers:**  
**SerpApi searches consumed:**

### Hypothesis

### Method

### Results

### Unexpected findings

### Limitations

### Decision

PASS / FAIL / MODIFY

### Next experiment

This prevents development decisions from becoming disconnected from observed evidence.

---

# 25. Engineering Rules for AI Assistants

Do not change architecture without explicit approval.

Do not invent SerpApi response fields.

Use actual API responses to define schemas.

Do not design Pydantic models from documentation assumptions alone. Capture actual SerpApi JSON responses during Experiment 0 and derive/validate schemas against those responses. Fields that are not guaranteed must be Optional.

Do not treat any SerpApi field as guaranteed unless captured responses support it.

Preserve complete raw API responses before parsing or transformation.

Never expose API keys.

Cache API requests.

Keep data collection separate from graph analysis.

Keep graph analysis separate from presentation.

Use deterministic scoring.

Document assumptions.

Do not add dependencies unless necessary.

Do not implement features outside the locked MVP unless specifically requested.

If requirements are unclear, ask rather than making architectural assumptions.

---

# 26. Five-Day Execution Strategy

Available development time is approximately two focused hours per day.

Therefore optimize for a working submission rather than maximum feature count.

## Day 1

Environment + SerpApi experiment.

Goal:

prove:

`Business → Reviewer → Businesses`

Then test approximately 20–30 contributor footprints (provisional sample size).

## Day 2

Build graph and analyze overlap.

Goal:

determine whether ReviewRing's core hypothesis works on real data.

## Day 3

Implement coordination signals and ring detection.

Goal:

produce explainable clusters.

## Day 4

Build Streamlit interface and graph visualization.

Goal:

complete end-to-end demo.

## Day 5

Polish:

- README
- responsible-use language
- demo dataset
- screenshots
- submission
- video/demo narrative
- testing
- deployment

No major feature development on Day 5.

---

# 27. Definition of Success

A successful MVP allows us to:

1. discover/select a business;
2. retrieve its reviewers through SerpApi;
3. expand reviewer histories;
4. construct a reviewer-business graph;
5. identify unusual repeated reviewer overlap;
6. explain why a cluster was highlighted;
7. expand the cluster to other businesses;
8. visualize the result clearly;
9. reproduce the analysis from cached data;
10. demonstrate that SerpApi is indispensable to the workflow.

The objective is not to build the world's most sophisticated review-fraud model.

The objective is to build the clearest, most technically interesting, defensible demonstration of **reviewer-network intelligence powered by SerpApi** within the hackathon window.