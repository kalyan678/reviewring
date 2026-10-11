# ReviewRing

**Follow the reviewers. Find the coordination.**

ReviewRing uses SerpApi's Google Maps APIs to follow a business's reviewers across the other businesses they have reviewed. It builds a reviewer-business graph and shows structural overlap, such as several reviewers sharing the same unrelated businesses, together with how much of the picture was actually collected.

It identifies patterns that may warrant a closer look. It does not determine that any review, reviewer, or business is fraudulent, and its output is not proof of wrongdoing.

## Demo

ReviewRing is built to run on your own computer: follow the quick start below, then open the address it prints in your browser. That address only works on the computer where the server is running.

The same code can also be hosted as a public, synthetic-only demo; see [Hosted demo](#hosted-demo-synthetic-only). A hosted demo shows fictional data only and cannot contact SerpApi.

## Quick start (no API key needed)

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run main.py
```

Leave that command running. In a browser on the same computer, go to `http://127.0.0.1:8765` With no SerpApi key configured it opens on **Explore Demo**, which runs fully offline, needs no SerpApi key, and uses no credits. Press **Expand network** to walk through the whole investigation, including expansion and the before-and-after comparison. Use `uv run main.py --port 9000` for another port.

`127.0.0.1` is your own machine, not a website. The address answers only while `main.py` is running, and only on that computer; opening it anywhere else shows "This site can't be reached".

After updating the code, stop the server and start it again, then reload the page. A running server keeps serving the page files it started with; if a page and a server from different versions do meet, the page says so and changes nothing.

Run the tests (all offline, no key, no network):

```
uv run python -m unittest discover -s tests
```

### Without uv (pip)

The dashboard and its tests need only two packages besides the standard library. With Python 3.11 or newer:

Windows:

```
python -m venv .venv
.venv\Scripts\activate
pip install httpx python-dotenv
python main.py
```

macOS or Linux:

```
python3 -m venv .venv
source .venv/bin/activate
pip install httpx python-dotenv
python main.py
```

Then, in the same environment, `python -m unittest discover -s tests` runs the tests.

## What you will see

The dashboard has three tabs. They are independent workspaces, not steps. With a SerpApi key configured the page opens on Investigate; without one it opens on Explore Demo.

| Tab | Data | Needs |
|---|---|---|
| **Explore Demo** | Fictional businesses and reviewers that show a multi-reviewer overlap pattern, with interactive expansion | Nothing. Works on a fresh clone. |
| **Collected Data** | Read-only. Every SerpApi response saved on your computer, shown as totals and groups. It may combine unrelated investigations and is not a single case | Saved responses. On a fresh clone it shows "No collected sample on this computer". |
| **Investigate** | A live, bounded investigation of a business you search for | Your own SerpApi key |

Each view contains:

- **Key figures** and a plain-language statement of what the data does and does not show, including when coordination cannot be assessed at all.
- **Network.** Reviewers on one side, businesses on the other, in fixed positions. Point at or tab to a node to preview its direct connections; click, tap or press Enter to keep it selected; Escape or **Clear selection** returns to the full network. Where the page describes an overlap group, **Explore overlap groups** has a button per group that shows that group's reviewers, the businesses at least two of them share, and the lines between them. Only drawn relationships are emphasised: reviewers are never joined to each other directly. The same connections are available as a table.
- **Coverage.** How many of each business's reported reviews were collected and how many reviewers have a collected history. Missing links are shown as unknown, not absent.
- **Overlap indicator.** Two reviewers are linked when both reviewed at least two of the same businesses; linked reviewers form a group. The "two" is an exploratory starting point from the project spec, not a validated threshold. There is no score and no probability. The panel states what was observed (how many pairs overlap, roughly how many businesses and categories are involved) and what that does not show. For real data, when a group has fewer than five reviewers, it gives no group card, member list, links, or list of shared businesses. This limits what is disclosed; it does not make a small group anonymous, since the number of overlapping pairs indicates how small it is and its members could be worked out from the same public review pages.
- **Ratings.** Ratings of the collected reviews, shown beside the rating Google reports and never averaged or mixed with it. No adjusted rating is computed, and the page says why.

### The synthetic demo

This is the **Explore Demo** tab. Everything in it is invented, and a "Demo · Fictional data" indicator stays at the top of the window while you scroll. It is an interactive walk through the same steps as a real investigation:

1. **Initial collection.** Thirteen fictional reviewers and seven fictional businesses, from three review pages and eight reviewer histories. Five reviewers share the same four unrelated businesses; two others share a cafe and a grocer. The indicator links both sets on purpose: the second is an everyday overlap, and it shows why a shared-business pattern is a reason to look closer and not proof of anything.
2. **Expansion is offered.** A panel beside the network shows the steps; "Why expansion is or is not available" names the businesses that qualify under the same rule a real investigation uses.
3. **Press Expand.** The first step adds the fictional first review page of two shared businesses. Four reviewers not seen before appear, two of them on both pages, so the larger group grows from five reviewers to seven and overlapping pairs go from 11 to 20. A notice says what the step added, the reviewers, review pages and lines that are new in that step are highlighted (they fade in once, unless your system asks for reduced motion), and a before-and-after table appears beside the graph.
4. **Press Expand again.** The second step adds the grocer's page. It adds one reviewer and no overlapping pair: the everyday pair is still just a pair. After that nothing qualifies and the page says why.
5. **Compare.** "What expansion added" states the before-and-after figures, and "What this does not show" states that the counts rose because more was collected, not because anyone behaved differently.

The steps are computed on your computer by the real graph, overlap and eligibility code, on fictional pages. No request is sent, so no key or credit is involved, and the result is identical every time. **Start again** returns to the initial collection.

Fictional evidence is not a finding. The demo shows what the pattern and the workflow look like; it says nothing about any real business or reviewer. The only real results are listed under [What has been verified against the real service](#what-has-been-verified-against-the-real-service), and they contain no overlap.

## Optional: live investigation with your own SerpApi key

1. Copy `.env.example` to `.env` and set `SERPAPI_API_KEY`. Never commit `.env`.
2. Restart the dashboard and open the **Investigate** tab.
3. **Search** for a business, **select** one from the list, and choose three things: how many review pages to collect (1 to 4, default 1), how many reviewer histories (0 to 8, default 3), and how many entries to request per history (10 to 200, default 50). Then confirm.

4. **Optionally, expand.** When the result is shown, a second stage may be offered beneath it: collecting the first review page of up to two businesses that the retrieved histories share. It is a separate action with its own cost check and confirmation, described under [Second stage: expansion](#second-stage-expansion).

Without a key the tab explains that one is required; nothing can be sent.

**Request budget.** Each new SerpApi request uses one credit.

| Action | Most it can send |
|---|---|
| A search | 1 request |
| An investigation | 1 per review page you chose + 1 per reviewer history you chose (at most 4 + 8 = 12) |
| An expansion (optional, each time you confirm one) | 1 per business you chose to expand (at most 2) |
| One run of the server | 30 new requests in total, then it refuses until restarted |

- Before anything is sent the page states the maximum and waits for you to confirm. The server enforces that same maximum; the confirmation cannot be skipped.
- Responses already saved on your computer are reused and cost nothing.
- **Review pages.** It collects only as many review pages as you chose, for the selected business only. A further page is requested only with the next-page token the previous response returned; if none is returned, pagination ends there, so fewer pages may be collected than you asked for and fewer requests sent. A token that was provided but not used is reported as exactly that: an offer of a further page, not a retrieved page. Each page is saved and reused on its own. It never requests reviews for businesses found in histories and never retries.
- **Reading the coverage.** The Coverage panel says how many pages were requested and collected, whether the service provided a token for a further page, and how many reviews were collected against the total the business reports. A response without a next-page token does not establish that all reviews were retrieved.
- **Examples.** With 3 histories: 1 page is at most 4 requests, 2 pages at most 5, 3 pages at most 6, 4 pages at most 7. In this project's runs a first page returned 8 reviews and a second page 10, so 4 pages is roughly 30 to 40 reviewers if the business has that many.
- **A history is a window, not a full record.** Each history request asks for a reviewer's most recent reviews up to the entry limit, and the service may return fewer. The Coverage panel says how many retrieved histories look partial, of unknown completeness, or apparently complete, and how small a share of those reviewers' reported reviews was retrieved.
- **Which reviewers are chosen.** First those whose reported review count fits within the entry limit, in page order; then those with no reported count; last those reporting more than the limit, fewest first. A reviewer reporting a single review is skipped. This favours histories that can actually be compared and avoids always picking the most prolific accounts, who share popular places for ordinary reasons. Only the reported count and page position are used.
- A history saved with one entry limit is not reused for another limit; that request would be new.
- If a request fails, collection stops and what was collected is still shown. A failed or timed-out request may still have used a credit.
- With fewer than two histories the page says coordination cannot be assessed. Otherwise, when nothing overlaps, it says "No repeated overlap observed in the retrieved review windows": only what was retrieved was compared.
- If the result cannot be displayed, the page says so plainly and shows no summary; what was collected stays saved on your computer.

### Second stage: expansion

An investigation never requests any business other than the one you selected. Expansion is a second, optional step that you start yourself after the result is shown.

- **What it does.** It collects the first review page of one or two businesses that appear in the retrieved reviewer histories, and merges those pages into the same result. This shows whether more of the reviewers already in the investigation, or reviewers not seen before, reviewed those businesses too.
- **Which businesses qualify.** The same rule as the overlap indicator: a pair of reviewers sharing at least two businesses. A business qualifies when both reviewers of such a pair list it in their retrieved histories and it has no review page collected yet. The investigated business counts as one of the two only because both reviewers were actually observed on it. A business listed in only one retrieved history does not qualify, however popular it is. Qualifying is a reason to look, not evidence that anything was arranged.
- **No qualifying business is a normal outcome.** The page then says why: fewer than two histories were retrieved, or no two retrieved histories list the same further business. This is not a finding that no overlap exists.
- **You choose how many, not which.** These businesses are shared by a small set of reviewers, and naming them would describe those reviewers. The server picks them by a fixed rule (the business supported by the most reviewer pairs first). The page is told only whether expansion is available, never how many businesses qualify: the form always allows 1 or 2, and if fewer qualify than you ask for, fewer are collected. Expanded businesses appear as "Expanded business 1", "Expanded business 2", with name, category, reported rating and review total withheld; only the number of reviews actually collected is shown.
- **Cost.** One request per business, at most two per confirmation. The page states the exact maximum and waits for you to confirm, exactly as for an investigation. A page already saved on your computer costs nothing. Examples: an investigation with 1 page and 3 histories is at most 4 requests; expanding one business afterwards is at most 1 more, two businesses at most 2 more. Expanding again later needs a new confirmation.
- **It goes one step and stops.** Expansion requests no reviewer histories and no further review pages. Reviewers first seen on an expanded page have no retrieved history, so they can never make another business qualify.
- **If a request fails,** nothing is retried. A page collected before the failure is kept and merged; if none was collected, the investigation is left exactly as it was. Either way the original result stays on screen.
- **Reading the result.** "What expansion added" is listed apart from the original findings: reviews collected, reviewers before and after, and reviewer pairs sharing two or more businesses before and after. Higher counts after expansion mean more was collected, nothing else. Two reviewers appearing together on another business's page is an observed co-review. It is not evidence that anyone arranged it: people who live near each other or share an interest review the same places.

## Hosted demo (synthetic only)

The repository can serve a public demo so the walkthrough can be tried without installing anything. It is a restricted mode of the same server, switched on only by `REVIEWRING_HOSTED_DEMO=1`.

**What it contains.** The interactive synthetic demo: the fictional network, the overlap evidence, both expansion steps, the before-and-after comparison, and reset. It is labelled as fictional throughout.

**What it switches off.** Everything that touches real data. The hosted server has a fixed list of routes: the page, its stylesheet and script, a one-line configuration flag, and the synthetic view. Search, investigation, reviewer-history, real expansion, the collected sample and the session token do not exist there, and no request method other than GET and HEAD is accepted. It does not load the live-investigation code or the SerpApi client, reads no key and no saved data, keeps no state between requests, and sets no cookie. It does not perform live SerpApi collection; that runs only on your own computer with your own key.

**Try the hosted mode locally.**

```
REVIEWRING_HOSTED_DEMO=1 PORT=10000 python main.py
```

On Windows PowerShell: `$env:REVIEWRING_HOSTED_DEMO = "1"; $env:PORT = "10000"; python main.py`. Then open `http://127.0.0.1:10000`. In this mode the server listens on all network interfaces, so other devices on your network can reach it. It needs no installed packages, only Python 3.11 or newer.

Any other value of `REVIEWRING_HOSTED_DEMO` stops the program instead of starting either mode. Without the variable the server is the local dashboard on `127.0.0.1:8765`, as before.

**Deploy on Render.** `render.yaml` describes the service. To set it up by hand, create a Web Service from this repository with:

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `python --version` (nothing needs installing) |
| Start command | `python main.py` |
| Environment variable | `REVIEWRING_HOSTED_DEMO` = `1` |
| Environment variable | `PYTHON_VERSION` = `3.11.11` |
| Health check path | `/api/config` |

Render provides `PORT` itself. Do not add `SERPAPI_API_KEY`: the hosted demo never reads it.

**Check a deployed URL.**

- `https://<your-service>/api/config` returns `{"hosted_demo": true}`.
- The page opens directly on Explore Demo, with no Investigate or Collected Data tab.
- `https://<your-service>/api/view?mode=real` returns an error, not data.
- `https://<your-service>/api/session` returns "Not found".

**Free-tier limits.** A free Render service is stopped after a period without visitors and takes up to about a minute to start again, so the first page load can be slow. It has limited monthly hours and no uptime guarantee. Nothing is stored, so a restart loses nothing.

## What has been verified against the real service

All figures are from `docs/EXPERIMENTS.md`. Thirteen SerpApi requests were attempted in total; twelve succeeded.

- **The API chain works.** Search returns business identifiers, a business's reviews return reviewer identifiers, a reviewer's history returns business identifiers, and those identifiers match across the three engines.
- **The graph builds from real responses.** Six responses merged into 23 reviewers, 7 businesses, and 29 links in one connected component.
- **No coordination was found.** No reviewer pair in that sample shares two or more businesses. Histories were collected for only 2 of 23 reviewers, so the sample neither supports nor rules out coordinated reviewing.
- **The dashboard's live workflow ran end to end:** one business search and one first page of reviews, then, in a later run, two reviewer histories for that page. Both histories returned exactly 10 entries for reviewers whose profiles report over 500 reviews, with no next-page marker, and the two shared no business. That is why histories are treated as windows.
- **One live failure was observed:** a request rejected with an authentication error stopped collection at once, kept the page data, and showed a fixed message.

- **The entry limit works:** one history requested with 50 entries returned 50, beginning with the same 10 as before.
- **Review pagination works through the dashboard:** one second review page was collected with the token the first page returned. It held 10 reviews and the response provided a token for a further page, which was not used.

Not verified against the real service: collecting more than two review pages in one investigation, the revised reviewer selection, overlap detection on real data that contains overlap, expansion to shared businesses (no qualifying business has been observed in real data), and timeouts. These are tested against a simulated SerpApi only. Expansion in particular has only ever run on simulated and fictional data.

## Limitations

- The real sample is far too small to measure overlap. The only overlap pattern in this repository is the synthetic one.
- The overlap indicator is purely structural. It cannot tell arranged reviews from ordinary shared habits, and it sees only what was collected.
- A live investigation sees at most four pages of one business's reviews and a handful of history windows. A business two reviewers share outside those windows is not seen.
- If a request for a later review page fails, collection stops there and no histories are requested; the pages already collected are shown.
- Expansion goes one step only: the first review page of at most two shared businesses per confirmation. It does not follow those pages further, and its selection depends entirely on the few history windows retrieved. Because every reviewer in an investigation was observed on the investigated business, any second business that two retrieved histories share qualifies; that is ordinary for neighbours and regulars.
- No coordination score, community detection, temporal analysis, or adjusted rating is implemented.
- Investigations are kept in memory and disappear when the server stops. Their saved responses remain and then appear in the collected sample, alongside any unrelated ones.
- Browser checks were run in one desktop browser engine.
- The hosted demo mode was verified on a local machine only. Its behaviour on a hosting provider (build, start-up, sleeping) has not been observed.

## Privacy safeguards

- **No real data is in this repository.** There are no saved responses, no real reviewer or business identifiers, and no review text. Test fixtures and the demo are fictional.
- **Collected data stays on your computer.** Raw responses go to `data/raw/` and `data/cache/`, which are git-ignored. They contain reviewer names and review text; do not commit or share them.
- **The browser receives aggregates.** For real data it gets totals, distributions, and groups. Reviewers with the same connections are merged, and sets of fewer than five reviewers are not drawn. No reviewer is ever named. A business is named only when you selected it yourself from a search.
- **Identifiers stay in the server process.** The page holds random references in their place. Reviewer names, profile links, review text, addresses, and coordinates are never read into the graph.
- **Expansion does not name what it expands.** The page is told whether expansion is available, never which businesses qualify or how many. This limits disclosure about the small set of reviewers who share them; it does not make those reviewers anonymous. Availability itself says that some pair of reviewers shares a second business. The cost you confirm is exact, so a lower cost than you asked for can mean fewer qualify or a page was already saved. The result shows how many businesses were expanded and how many reviews each page held, and the saved responses on your computer hold the full detail.
- **The server is local.** It listens on the loopback interface only, loads nothing from outside, uses no cookies or browser storage, and rejects cross-origin requests. Its only outbound traffic is the SerpApi requests you confirm.
- **Errors are generic.** SerpApi's own error text is never shown, because it can repeat request details.
- **Experiment inputs are private.** The business and reviewers used in the feasibility experiments are not identified here. Re-running those scripts needs your own values in an untracked `local_inputs.json` (see `local_inputs.example.json`, which is fictional).
- Per-node exports, if you write any, should use the keyed pseudonyms in `src/reviewring/privacy.py` (`REVIEWRING_PSEUDONYM_KEY`). The dashboard does not need that key.

## Project layout

| Path | Purpose |
|---|---|
| `main.py` | Starts the dashboard. |
| `src/reviewring/app.py`, `web/` | Local server and the static page. |
| `src/reviewring/views.py` | Builds what the page may see. The privacy boundary. |
| `src/reviewring/live.py` | Plans, confirmed budgets, and in-memory state for live investigations. |
| `src/reviewring/investigation.py` | The bounded collection itself: one review page, then the chosen histories. |
| `src/reviewring/indicators.py` | Structural overlap indicators. |
| `src/reviewring/synthetic.py` | Fictional demo fixtures. |
| `src/reviewring/loader.py` | Reads saved responses from `data/raw/`. |
| `src/reviewring/models.py`, `parsers.py`, `graph.py`, `privacy.py` | Graph foundation. |
| `src/reviewring/serpapi_client.py` | Cached SerpApi client. The only code that contacts SerpApi. |
| `src/reviewring/local_inputs.py`, `local_inputs.example.json` | Private local inputs for the experiment scripts. |
| `experiments/` | The scripts behind the feasibility experiments. They spend credits and need `local_inputs.json`. |
| `docs/ARCHITECTURE.md`, `docs/EXPERIMENTS.md`, `PROJECT_SPEC.md` | Design, experiment log, specification. |

## License

MIT. See [LICENSE](LICENSE).
