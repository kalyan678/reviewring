# CLAUDE.md

Development instructions for AI assistants working on ReviewRing.

## Source of truth

`PROJECT_SPEC.md` is the source of truth. Read it before making changes. If this file and the spec ever disagree, the spec wins.

Current phase: **Application v0.1 with Live Investigation v0.1, built on Graph Foundation v0.1.** The application was started on the project owner's explicit instruction before all decision gates passed.

- Gate 1 (API viability): passed.
- **Gate 2 (graph viability) remains open.** The real sample shows no reviewer pair sharing two or more businesses. No real coordination evidence exists; never state or imply otherwise in code, UI text, or documentation.
- The only overlap pattern in the product is the synthetic demo (`src/reviewring/synthetic.py`), which must stay labelled as synthetic everywhere it appears.

## Application architecture (as implemented)

- **UI:** a standard-library HTTP server (`src/reviewring/app.py`) bound to the loopback interface, serving one static page (`src/reviewring/web/`) and a small JSON API. This replaces the spec's "Streamlit initially" (Section 17) by decision of the project owner. Do not migrate to Streamlit or add a UI framework without approval.
- **No external resources:** the page loads no CDN script, font, or stylesheet. Keep the content-security policy strict. The server's only outbound requests are SerpApi calls made by a confirmed live investigation.
- **Privacy boundary:** `src/reviewring/views.py` produces everything the page receives. Real data leaves it only as aggregates; reviewer sets smaller than `MIN_AGGREGATE_GROUP_SIZE` are not drawn. Do not send real reviewer–business edges, identifiers, names, categories, or raw filenames to the frontend.
- **Data modes never mix:** real mode reads `data/raw/` through `loader.py`; synthetic mode reads nothing from disk; a live investigation's view is built only from that investigation's own responses.
- **Experiment inputs are private:** the seed business, the saved-response filenames, and the reviewer selected for a contributor history are read from the untracked `local_inputs.json` (`src/reviewring/local_inputs.py`). Never write them, or anything that points at one reviewer, into code, tests, or documentation. Keep `local_inputs.example.json` fictional.
- **Raw filenames are sensitive:** they embed an unkeyed hash of the request parameters, including contributor IDs. Do not add them to new code, output, or documentation. `loader.py` finds responses by engine instead.
- The dashboard does not score coordination or compute adjusted ratings. Do not describe it as doing so.

## Live investigation rules

- **Status:** live search, first-page review collection, and collection of two reviewer histories were each validated once against the real service. The `num` entry limit was validated once (50 requested, 50 returned). Multi-page review collection through the dashboard, the revised reviewer selection, and overlap detection on real overlapping data are tested against a simulated SerpApi only. Do not describe them as verified live, and do not claim any real coordination finding.
- **Never make a real SerpApi request while developing or testing** unless the project owner approves that specific run and its request limit. Tests and manual checks use a fake `search` / `is_cached` passed to `LiveState`; never start a browser check against a server built with the default `LiveState`.
- **Every credit-spending action needs a server-issued plan** (`src/reviewring/live.py`): plan first, execute second, with the plan's maximum enforced during execution. Do not add an endpoint that sends a request without one.
- **A contributor history is a window, never assumed complete.** Do not treat a missing next-page marker, or a full `num` window, as the whole history. Keep the wording "retrieved review windows"; never say that no overlap exists. Reported review totals are approximate and are reviewer-level data: show them only in aggregate for five or more reviewers.
- **Overlap evidence for real data is aggregate and shape-only** (`views._overlap_evidence`). For fewer than five reviewers: no group card, size, member list, per-reviewer link, list of shared businesses, or category names; counts of businesses are banded. Do not add a drill-down that gets around this, and keep the test that identical overlap shapes give identical output.
- **Reviewer selection is a coverage rule** (`choose_reviewers`), not a suspicion ranking. Do not rank reviewers by anything about their reviews.
- **Collection is bounded** (`src/reviewring/investigation.py`): one search, at most `MAX_REVIEW_PAGES` review pages for the selected business, at most `MAX_HISTORIES` histories of at most `MAX_HISTORY_LIMIT` entries. The investigation itself requests no business found in a history; only the separate expansion stage does. No recursion, no retries. Do not raise the limits or the per-process ceiling without approval.
- **Pagination follows returned tokens only.** Never construct a next-page token, never reuse one, and never treat a missing next-page token as evidence that all reviews were collected. History pagination is not implemented.
- **Keep the request checks:** Host header on every request; same-origin, JSON, and the session token on anything that spends credits or reads an investigation. No CORS headers, cookies, or browser storage.
- **Never forward SerpApi error text or exception text** to the browser or logs; use the fixed messages in `describe_error`.
- Business `data_id` values and contributor IDs stay server-side; the browser gets random references.
- **Expansion is a separate, confirmed, one-hop stage** (`indicators.expansion_candidates`, `investigation.expand`, `live.plan_expansion`). Status: tested against a simulated SerpApi only. Keep these properties: it never runs inside `collect`; at most `MAX_EXPANSION_BUSINESSES` first review pages per confirmed plan; no histories and no page tokens during expansion; a candidate must be listed in the retrieved histories of both reviewers of a pair that meets the overlap rule (`MIN_SHARED_BUSINESSES`, counted from observations only). Do not count a business for a reviewer without an observation, do not accept a business listed in only one history, and do not let expansion create its own candidates.
- **The browser never learns which businesses qualify or were expanded.** The server selects them; the page gets availability, the fixed per-action maximum, and aggregate before-and-after findings, never the number of candidates. Expanded businesses keep neutral labels with rating and review total withheld. Describe a shared business page as observed co-review, never as evidence of arrangement.

## Architecture and scope

- Do not redesign the architecture without explicit approval. The approved pipeline is in `docs/ARCHITECTURE.md`.
- Do not expand the MVP scope (spec Section 5) without approval. Stretch features (Section 23) come only after the MVP is complete.
- Do not add infrastructure or dependencies without a stated justification tied to an observed requirement.
- If a requirement would materially affect the design, ask before assuming.

## SerpApi data rules

- Do not invent SerpApi response fields.
- The actual JSON captured in Experiment 0 determines the schemas. Do not design Pydantic models from documentation alone.
- Optional or unconfirmed API fields must remain `Optional`.
- Preserve the complete raw API response before any parsing or transformation. Never trim, normalize, or overwrite raw responses.
- Cache SerpApi requests (key: engine + normalized request parameters). Check the cache before every call; credits are limited.
- Never expose API keys. Load `SERPAPI_API_KEY` from `.env`; never commit `.env`.

## Code structure

- Keep data collection, analysis, and presentation separate.
- Coordination scoring must be deterministic and explainable from observable features.
- Thresholds, weights, sample sizes, and score cutoffs are provisional until set by experiment results (spec Section 7, Provisional Parameters).
- Prefer minimal, production-quality code: simple, typed, small modules, no premature abstraction.

## Responsible use

- LLMs do not determine coordination or fraud, and do not produce the coordination score.
- Never label reviews, reviewers, or businesses as fake or fraudulent. Use the spec's terminology (coordination signal, coordination risk, unusual reviewer overlap).
- Geographic dispersion is context, never proof. "Impossible travel" is out of scope.
- Anonymize reviewers in anything shown publicly (`R001`, `R002`, ...).

## Experiments

Record every experiment in `docs/EXPERIMENTS.md` using the template there. Record only observed results.
