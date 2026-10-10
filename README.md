# ReviewRing

**Follow the reviewers. Find the coordination.**

ReviewRing uses SerpApi's Google Maps APIs to follow a business's reviewers across the other businesses they have reviewed. It builds a reviewer-business graph and shows structural overlap, such as several reviewers sharing the same unrelated businesses, together with how much of the picture was actually collected.

It identifies patterns that may warrant a closer look. It does not determine that any review, reviewer, or business is fraudulent, and its output is not proof of wrongdoing.

## Demo

There is no hosted online demo yet. ReviewRing runs on your own computer: follow the quick start below, then open the address it prints in your browser. That address only works on the computer where the server is running.

## Quick start (no API key needed)

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run main.py
```

Leave that command running. In a browser on the same computer, go to `http://127.0.0.1:8765` and choose **Synthetic demo**. It runs fully offline. Use `uv run main.py --port 9000` for another port.

`127.0.0.1` is your own machine, not a website. The address answers only while `main.py` is running, and only on that computer; opening it anywhere else shows "This site can't be reached".

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

The dashboard has three tabs.

| Tab | Data | Needs |
|---|---|---|
| **Synthetic demo** | Fictional businesses and reviewers that show a multi-reviewer overlap pattern | Nothing. Works on a fresh clone. |
| **Collected sample** | Every SerpApi response saved on your computer, shown as totals and groups | Saved responses. On a fresh clone it shows "No collected sample on this computer". |
| **Investigate** | A live, bounded investigation of a business you search for | Your own SerpApi key |

Each view contains:

- **Key figures** and a plain-language statement of what the data does and does not show, including when coordination cannot be assessed at all.
- **Network.** Reviewers on one side, businesses on the other. Point at, tap, or tab to a node to see its connections and what was not collected. The same connections are available as a table.
- **Coverage.** How many of each business's reported reviews were collected and how many reviewers have a collected history. Missing links are shown as unknown, not absent.
- **Overlap indicator.** Two reviewers are linked when both reviewed at least two of the same businesses; linked reviewers form a group. The "two" is an exploratory starting point from the project spec, not a validated threshold. There is no score and no probability.
- **Ratings.** Ratings of the collected reviews, shown beside the rating Google reports and never averaged or mixed with it. No adjusted rating is computed, and the page says why.

### The synthetic demo

Thirteen fictional reviewers and seven fictional businesses. Five reviewers share the same four unrelated businesses; two others share a cafe and a grocer. The indicator links both sets on purpose: the second is an everyday overlap, and it shows why a shared-business pattern is a reason to look closer and not proof of anything. The demo is labelled "SYNTHETIC DEMO" throughout and is evidence of nothing about any real business or person.

## Optional: live investigation with your own SerpApi key

1. Copy `.env.example` to `.env` and set `SERPAPI_API_KEY`. Never commit `.env`.
2. Restart the dashboard and open the **Investigate** tab.
3. **Search** for a business, **select** one from the list, choose how many reviewer histories to collect (0 to 8, default 3) and how many entries to request per history (10 to 200, default 50), and confirm.

Without a key the tab explains that one is required; nothing can be sent.

**Request budget.** Each new SerpApi request uses one credit.

| Action | Most it can send |
|---|---|
| A search | 1 request |
| An investigation | 1 for the first page of reviews + 1 per reviewer history you chose (at most 9) |
| One run of the server | 30 new requests in total, then it refuses until restarted |

- Before anything is sent the page states the maximum and waits for you to confirm. The server enforces that same maximum; the confirmation cannot be skipped.
- Responses already saved on your computer are reused and cost nothing.
- It reads only the first page of reviews. It never follows further pages, never requests reviews for businesses found in histories, and never retries.
- **A history is a window, not a full record.** Each history request asks for a reviewer's most recent reviews up to the entry limit, and the service may return fewer. The Coverage panel says how many retrieved histories look partial, of unknown completeness, or apparently complete, and how small a share of those reviewers' reported reviews was retrieved.
- **Which reviewers are chosen.** First those whose reported review count fits within the entry limit, in page order; then those with no reported count; last those reporting more than the limit, fewest first. A reviewer reporting a single review is skipped. This favours histories that can actually be compared and avoids always picking the most prolific accounts, who share popular places for ordinary reasons. Only the reported count and page position are used.
- A history saved with one entry limit is not reused for another limit; that request would be new.
- If a request fails, collection stops and what was collected is still shown. A failed or timed-out request may still have used a credit.
- With fewer than two histories the page says coordination cannot be assessed. Otherwise, when nothing overlaps, it says "No repeated overlap observed in the retrieved review windows": only what was retrieved was compared.
- If the result cannot be displayed, the page says so plainly and shows no summary; what was collected stays saved on your computer.

## What has been verified against the real service

All figures are from `docs/EXPERIMENTS.md`. Eleven SerpApi requests were attempted in total; ten succeeded.

- **The API chain works.** Search returns business identifiers, a business's reviews return reviewer identifiers, a reviewer's history returns business identifiers, and those identifiers match across the three engines.
- **The graph builds from real responses.** Six responses merged into 23 reviewers, 7 businesses, and 29 links in one connected component.
- **No coordination was found.** No reviewer pair in that sample shares two or more businesses. Histories were collected for only 2 of 23 reviewers, so the sample neither supports nor rules out coordinated reviewing.
- **The dashboard's live workflow ran end to end:** one business search and one first page of reviews, then, in a later run, two reviewer histories for that page. Both histories returned exactly 10 entries for reviewers whose profiles report over 500 reviews, with no next-page marker, and the two shared no business. That is why histories are treated as windows.
- **One live failure was observed:** a request rejected with an authentication error stopped collection at once, kept the page data, and showed a fixed message.

Not verified against the real service: the entry limit (`num` is documented by SerpApi but has not been sent in a live request by this project, so how many entries it actually returns is unknown), the revised reviewer selection, overlap detection on real data that contains overlap, and timeouts. These are tested against a simulated SerpApi only.

## Limitations

- The real sample is far too small to measure overlap. The only overlap pattern in this repository is the synthetic one.
- The overlap indicator is purely structural. It cannot tell arranged reviews from ordinary shared habits, and it sees only what was collected.
- A live investigation sees one page of one business's reviews and a handful of history windows. A business two reviewers share outside those windows is not seen.
- No coordination score, community detection, temporal analysis, ring expansion, or adjusted rating is implemented.
- Investigations are kept in memory and disappear when the server stops. Their saved responses remain and then appear in the collected sample, alongside any unrelated ones.
- Browser checks were run in one desktop browser engine.

## Privacy safeguards

- **No real data is in this repository.** There are no saved responses, no real reviewer or business identifiers, and no review text. Test fixtures and the demo are fictional.
- **Collected data stays on your computer.** Raw responses go to `data/raw/` and `data/cache/`, which are git-ignored. They contain reviewer names and review text; do not commit or share them.
- **The browser receives aggregates.** For real data it gets totals, distributions, and groups. Reviewers with the same connections are merged, and sets of fewer than five reviewers are not drawn. No reviewer is ever named. A business is named only when you selected it yourself from a search.
- **Identifiers stay in the server process.** The page holds random references in their place. Reviewer names, profile links, review text, addresses, and coordinates are never read into the graph.
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
