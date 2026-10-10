// ReviewRing dashboard. Renders the view model from /api/view and nothing else.
// All text is inserted with textContent; no markup is built from data.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const MODES = ["real", "synthetic", "investigate"];
const ROW_HEIGHT = 44;

const $ = (id) => document.getElementById(id);
let current = null; // the view being shown
let selection = null; // { ids: Set, title, lines, pinned }

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function replace(parent, ...children) {
  parent.replaceChildren(...children.filter(Boolean));
}

function plural(count, noun, pluralNoun = `${noun}s`) {
  return `${count} ${count === 1 ? noun : pluralNoun}`;
}

// ---------- mode handling ----------

function modeFromHash() {
  const mode = location.hash.slice(1);
  return MODES.includes(mode) ? mode : "real";
}

// The one place the page talks to the server. Paths are always same-origin.
async function request(path, options) {
  const response = await fetch(path, options);
  let payload = null;
  try { payload = await response.json(); } catch (error) { /* not JSON */ }
  if (!response.ok || payload === null) {
    throw new Error((payload && payload.error) || "The server could not complete the request.");
  }
  return payload;
}

function show(view) {
  current = view;
  selection = null;
  $("state").hidden = true;
  $("dashboard").hidden = false;
  render(view);
}

async function load(mode) {
  for (const name of MODES) $(`mode-${name}`).setAttribute("aria-pressed", String(name === mode));
  showBanner(mode);
  $("dashboard").hidden = true;
  $("investigate").hidden = mode !== "investigate";
  if (mode === "investigate") return openInvestigate();
  showState("Loading", "Building the view from local data.");
  try {
    const view = await request(`/api/view?mode=${mode}`);
    if (mode !== modeFromHash()) return; // a newer selection replaced this one
    if (!view.available) return showUnavailable(view);
    show(view);
  } catch (error) {
    if (mode !== modeFromHash()) return;
    showState("The view could not be loaded", "Check that the ReviewRing server is still running, then reload.");
  }
}

const NETWORK_USE = {
  offline: "This page runs entirely on your computer and makes no outside request.",
  investigate:
    "This page runs on your computer and loads nothing from outside. In this mode the local ReviewRing server contacts SerpApi, and only after you confirm each step.",
};

function showBanner(mode) {
  $("network-use").textContent = mode === "investigate" ? NETWORK_USE.investigate : NETWORK_USE.offline;
  const banner = $("banner");
  banner.className = mode === "synthetic" ? "banner synthetic" : "banner";
  if (mode === "investigate") {
    replace(
      banner,
      el("strong", {}, "Live investigation. "),
      document.createTextNode(
        "Collects real data through SerpApi and uses your credits, only after you confirm each step. Results are shown as totals and groups: no reviewer is named."
      )
    );
  } else if (mode === "synthetic") {
    replace(
      banner,
      el("strong", {}, "SYNTHETIC DEMO. "),
      document.createTextNode(
        "Every business, reviewer, and rating below is fictional and was invented to illustrate an overlap pattern. Nothing here describes a real business or person."
      )
    );
  } else {
    replace(
      banner,
      el("strong", {}, "Collected sample. "),
      document.createTextNode(
        "Combines every SerpApi Google Maps response saved on this computer, from all searches and investigations, which may be unrelated. Shown as totals and groups only: no reviewer or business is named."
      )
    );
  }
}

function showState(title, message, action) {
  const state = $("state");
  state.hidden = false;
  replace(state, el("h2", {}, title), el("p", { class: "muted" }, message), action);
}

function showUnavailable(view) {
  const button = el("button", { type: "button" }, "Open the synthetic demo");
  button.addEventListener("click", () => { location.hash = "synthetic"; });
  showState(
    "No collected sample on this computer",
    `${view.reason} Saved responses are kept locally and are not part of the repository. The synthetic demo needs no saved data.`,
    button
  );
}

// ---------- sections ----------

function render(view) {
  renderTiles(view);
  $("headline").textContent = view.explanation.headline;
  $("assessment").textContent = view.explanation.assessment.statement;
  replace($("points"), ...view.explanation.points.map((point) => el("li", {}, point)));
  renderNetwork(view);
  renderCoverage(view);
  renderIndicators(view);
  renderRatings(view);
}

function renderTiles(view) {
  const m = view.metrics;
  const minimum = view.indicators.minimum_shared_businesses;
  const tiles = [
    [m.reviewers, "reviewers"],
    [m.businesses, "businesses"],
    [m.edges, "reviewer-business links"],
    [m.pairs_sharing_two, `reviewer pairs sharing ${minimum}+ businesses`, true],
  ];
  replace(
    $("tiles"),
    ...tiles.map(([value, label, hero]) => {
      const tile = el("div", { class: hero ? "tile hero" : "tile" });
      tile.append(el("div", { class: "value" }, String(value)), el("div", { class: "label" }, label));
      return tile;
    })
  );
}

function meter(label, value, total, note) {
  const wrap = el("div", { class: "meter" });
  const head = el("div", { class: "meter-label" });
  head.append(el("span", {}, label), el("span", {}, total === null ? `${value} collected` : `${value} of ${total}`));
  const bar = el("div", { class: "bar", role: "img", "aria-label": total === null ? `${value} collected, total unknown` : `${value} of ${total}` });
  const fill = el("div");
  fill.style.width = total ? `${Math.min(100, (100 * value) / total)}%` : "0%";
  bar.append(fill);
  wrap.append(head, bar);
  if (note) wrap.append(el("small", {}, note));
  return wrap;
}

function renderCoverage(view) {
  const c = view.coverage;
  const parts = [
    el("p", { class: "muted" }, "Solid bar: collected. Empty track: known to exist but not collected."),
    el("h3", {}, "Reviews collected per business"),
  ];
  for (const row of c.business_rows) {
    let note = `${plural(row.pages, "review page")} collected.`;
    if (row.not_collected) note += ` ${row.not_collected} reported reviews not collected.`;
    if (row.further_pages) note += " A further page was offered and not requested.";
    parts.push(meter(row.label, row.captured, row.reported_total, note));
  }
  parts.push(
    el("h3", {}, "Reviewer histories collected"),
    meter(
      "Reviewers with their review history collected",
      c.reviewers_with_history,
      c.reviewers,
      "Without a history, only the business a reviewer was found on is known."
    ),
    el("p", { class: "muted" }, c.history_windows.statement),
    el(
      "p",
      { class: "callout" },
      `${plural(c.history_only_businesses, "business", "businesses")} ${c.history_only_businesses === 1 ? "appears" : "appear"} only in a reviewer history, with no review page collected. Their other reviewers are unknown, not absent.`
    )
  );
  replace($("coverage"), ...parts);
}

function renderIndicators(view) {
  const ind = view.indicators;
  const parts = [
    el(
      "p",
      { class: "muted" },
      `Rule: two reviewers are linked when both reviewed at least ${ind.minimum_shared_businesses} of the same businesses. Linked reviewers form a group. The number ${ind.minimum_shared_businesses} is a starting point for exploration, not a validated threshold.`
    ),
  ];
  const list = (items) => {
    const ul = el("ul", { class: "points" });
    for (const item of items) ul.append(el("li", {}, item));
    return ul;
  };
  if (!ind.group_count) parts.push(el("p", { class: "headline" }, "No group found."));
  parts.push(
    el("h3", {}, "What was observed"),
    list(ind.evidence.observed_statements),
    el("h3", {}, "What this does not show"),
    list(ind.evidence.limit_statements)
  );
  if (ind.groups.length) {
    const list = el("div", { class: "groups" });
    for (const group of ind.groups) {
      const card = el("div", { class: "group" });
      card.append(
        el("h3", {}, `${group.label}: ${plural(group.reviewers, "reviewer")}, ${plural(group.shared_businesses, "shared business", "shared businesses")}`),
        el("p", {}, `${plural(group.pairs, "linked pair")} across ${plural(group.distinct_categories, "business category", "business categories")}. Review history collected for ${group.histories_fetched} of ${group.reviewers} members.`)
      );
      if (group.members) {
        card.append(el("p", {}, `Reviewers: ${group.members.join(", ")}.`), el("p", {}, `Shared businesses: ${group.businesses.join(", ")}.`));
        const button = el("button", { type: "button", class: "group-button" }, `Show ${group.label} in the network`);
        button.addEventListener("click", () => {
          select({ ids: new Set(group.node_ids), title: group.label, lines: [`${plural(group.reviewers, "reviewer")} and the businesses at least two of them reviewed.`], pinned: true });
          $("graph").scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
        card.append(button);
      }
      list.append(card);
    }
    parts.push(list);
  }
  parts.push(
    el(
      "p",
      { class: "callout" },
      "A group is a pattern to look into, not a conclusion. People share businesses for ordinary reasons, and this indicator cannot tell those apart from arranged reviews. It is not a probability and not evidence of fraud."
    )
  );
  replace($("indicators"), ...parts);
}

function ratingRows(distribution, total) {
  const rows = el("div", { class: "rating-rows" });
  for (const item of distribution) {
    const row = el("div", { class: "rating-row" });
    const bar = el("div", { class: "bar" });
    const fill = el("div");
    fill.style.width = `${(100 * item.count) / total}%`;
    bar.append(fill);
    row.append(el("span", {}, `${item.rating} star`), bar, el("span", {}, String(item.count)));
    rows.append(row);
  }
  return rows;
}

function renderRatings(view) {
  const r = view.ratings;
  const parts = [
    el("h3", {}, `Ratings of the ${plural(r.reviews_with_rating, "collected review")}`),
    ratingRows(r.distribution, r.reviews_with_rating || 1),
  ];
  if (r.reviews_without_usable_rating) {
    parts.push(el("p", { class: "muted" }, `${plural(r.reviews_without_usable_rating, "review")} had no rating or conflicting ratings and ${r.reviews_without_usable_rating === 1 ? "is" : "are"} not counted.`));
  }
  if (r.businesses.length) {
    const table = el("table");
    const head = el("tr");
    for (const [label, cls] of [["Business", ""], ["Rating reported by Google", "num"], ["Reviews reported", "num"], ["Reviews collected", "num"], ["Ratings in collected reviews", ""]]) {
      head.append(el("th", cls ? { class: cls, scope: "col" } : { scope: "col" }, label));
    }
    table.append(head);
    for (const b of r.businesses) {
      const row = el("tr");
      row.append(
        el("td", {}, b.label),
        el("td", { class: "num" }, b.reported_rating === null ? "not reported" : String(b.reported_rating)),
        el("td", { class: "num" }, b.reported_total === null ? "not reported" : String(b.reported_total)),
        el("td", { class: "num" }, String(b.captured_reviews)),
        el("td", {}, b.captured_distribution.map((d) => `${d.count} x ${d.rating} star`).join(", ") || "none")
      );
      table.append(row);
    }
    const scroll = el("div", { class: "scroll" });
    scroll.append(table);
    parts.push(
      el("h3", {}, "Google's rating and the collected reviews are different things"),
      scroll,
      el("p", { class: "muted" }, "Google's rating covers every review of the business. The collected reviews are a small, unrepresentative slice, so no average is computed from them.")
    );
  }
  parts.push(el("h3", {}, "Coordination-adjusted rating"), el("p", { class: "callout" }, r.adjusted_rating.reason));
  replace($("ratings"), ...parts);
}

// ---------- network ----------

function radius(node) {
  return Math.max(9, Math.min(24, 6 + 3.5 * Math.sqrt(node.count)));
}

function truncate(label, maxChars) {
  return label.length > maxChars ? `${label.slice(0, Math.max(3, maxChars - 1))}…` : label;
}

function neighbours(id) {
  const ids = new Set([id]);
  for (const link of current.network.links) {
    if (link.source === id) ids.add(link.target);
    if (link.target === id) ids.add(link.source);
  }
  return ids;
}

function nodeSelection(node, pinned) {
  const byId = new Map(current.network.nodes.map((n) => [n.id, n]));
  const connections = current.network.links
    .filter((link) => link.source === node.id || link.target === node.id)
    .map((link) => {
      const other = byId.get(link.source === node.id ? link.target : link.source);
      return link.count > 1 ? `${other.label} (${link.count} links)` : other.label;
    });
  return { ids: neighbours(node.id), title: node.label, lines: node.detail, connections, focus: node.id, pinned };
}

function select(next) {
  selection = next;
  applySelection();
}

function applySelection() {
  const graph = $("graph");
  const ids = selection ? selection.ids : null;
  for (const node of graph.querySelectorAll(".node")) {
    node.classList.toggle("dim", Boolean(ids) && !ids.has(node.dataset.id));
    node.classList.toggle("selected", Boolean(selection) && selection.focus === node.dataset.id);
  }
  for (const link of graph.querySelectorAll(".link")) {
    const active = Boolean(ids) && ids.has(link.dataset.source) && ids.has(link.dataset.target) &&
      (!selection.focus || link.dataset.source === selection.focus || link.dataset.target === selection.focus);
    link.classList.toggle("active", active);
    link.classList.toggle("dim", Boolean(ids) && !active);
  }
  $("clear").hidden = !(selection && selection.pinned);
  const detail = $("detail");
  if (!selection) {
    replace(detail, el("h3", {}, "Nothing selected"), el("p", { class: "muted" }, "Point at, tap, or tab to a node to see what it is connected to and what was not collected."));
    return;
  }
  const lines = el("ul");
  for (const line of selection.lines) lines.append(el("li", {}, line));
  const parts = [el("h3", {}, selection.title), lines];
  if (selection.connections && selection.connections.length) {
    const list = el("ul", { class: "connections" });
    for (const item of selection.connections) list.append(el("li", {}, item));
    parts.push(el("h3", {}, "Connected to"), list);
  }
  replace(detail, ...parts);
}

function renderNetwork(view) {
  const network = view.network;
  $("network-note").textContent = network.aggregated
    ? "Reviewers with the same connections are merged into one circle, sized by how many they are. Businesses are labelled by letter. Individual reviewers are not shown."
    : "Each circle is one fictional reviewer and each square one fictional business. A line means that reviewer reviewed that business.";
  const hidden = network.suppressed;
  if (hidden && hidden.reviewers) {
    $("network-note").textContent += ` ${plural(hidden.reviewers, "reviewer")} with ${plural(hidden.links, "link")} ${hidden.reviewers === 1 ? "is" : "are"} not drawn: fewer than ${hidden.minimum_group_size} reviewers share that connection pattern, and drawing it would single them out. The totals above still include them.`;
  }

  const container = $("graph");
  const width = Math.max(320, Math.min(1000, container.clientWidth || 800));
  const reviewers = network.nodes.filter((node) => node.kind === "reviewer");
  const businesses = network.nodes.filter((node) => node.kind === "business");
  const height = Math.max(reviewers.length, businesses.length, 3) * ROW_HEIGHT + 24;
  // On narrow screens the reviewer column moves right so reviewer labels,
  // which differ only at the end, are never cut short.
  const narrow = width < 480;
  const columns = { reviewer: width * (narrow ? 0.47 : 0.36), business: width * (narrow ? 0.66 : 0.64) };
  // Room for a label beside each column, at roughly 6.5px per character.
  const maxChars = {
    reviewer: Math.floor((columns.reviewer - 40) / 6.5),
    business: Math.floor((width - columns.business - 40) / 6.5),
  };

  const position = new Map();
  for (const group of [reviewers, businesses]) {
    group.forEach((node, index) => {
      position.set(node.id, { x: columns[node.kind], y: ((index + 0.5) * height) / group.length });
    });
  }

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": "Network of reviewers and businesses" });
  for (const link of network.links) {
    const a = position.get(link.source);
    const b = position.get(link.target);
    const mid = (a.x + b.x) / 2;
    const path = svg("path", {
      class: "link",
      d: `M${a.x},${a.y} C${mid},${a.y} ${mid},${b.y} ${b.x},${b.y}`,
      "stroke-width": String(Math.min(10, 1.5 + Math.log2(link.count) * 1.5)),
    });
    path.dataset.source = link.source;
    path.dataset.target = link.target;
    root.append(path);
  }

  for (const node of network.nodes) {
    const { x, y } = position.get(node.id);
    const r = radius(node);
    const group = svg("g", { class: `node ${node.kind}`, tabindex: "0", role: "button", "aria-label": `${node.label}. ${node.detail.join(" ")}` });
    group.dataset.id = node.id;
    group.append(svg("rect", { class: "hit", x: String(x - 22), y: String(y - ROW_HEIGHT / 2), width: "44", height: String(ROW_HEIGHT) }));
    if (node.group) group.append(svg("circle", { class: "ring", cx: String(x), cy: String(y), r: String(r + 5) }));
    if (node.incomplete) group.append(svg("circle", { class: "outline", cx: String(x), cy: String(y), r: String(r + (node.group ? 9 : 4)) }));
    group.append(
      node.kind === "reviewer"
        ? svg("circle", { class: "shape", cx: String(x), cy: String(y), r: String(r) })
        : svg("rect", { class: "shape", x: String(x - r), y: String(y - r), width: String(2 * r), height: String(2 * r), rx: "4" })
    );
    const left = node.kind === "reviewer";
    const offset = r + (node.group ? 14 : 9) + (node.incomplete ? 4 : 0);
    const label = (node.group ? `G${node.group} ` : "") + truncate(node.label, maxChars[node.kind]);
    group.append(svg("text", { x: String(left ? x - offset : x + offset), y: String(y + 4), "text-anchor": left ? "end" : "start" }, label));

    const preview = () => { if (!(selection && selection.pinned)) select(nodeSelection(node, false)); };
    const clear = () => { if (selection && !selection.pinned) select(null); };
    group.addEventListener("mouseenter", preview);
    group.addEventListener("mouseleave", clear);
    group.addEventListener("focus", preview);
    group.addEventListener("blur", clear);
    group.addEventListener("click", () => {
      const pinnedHere = selection && selection.pinned && selection.focus === node.id;
      select(pinnedHere ? null : nodeSelection(node, true));
    });
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); group.dispatchEvent(new Event("click")); }
      if (event.key === "Escape") select(null);
    });
    root.append(group);
  }
  replace(container, root);

  const table = $("links-table");
  const head = el("tr");
  head.append(el("th", { scope: "col" }, "Reviewer"), el("th", { scope: "col" }, "Business"), el("th", { scope: "col", class: "num" }, "Links"));
  const byId = new Map(network.nodes.map((node) => [node.id, node.label]));
  replace(
    table,
    head,
    ...network.links.map((link) => {
      const row = el("tr");
      row.append(el("td", {}, byId.get(link.source)), el("td", {}, byId.get(link.target)), el("td", { class: "num" }, String(link.count)));
      return row;
    })
  );
  applySelection();
}

// ---------- live investigation ----------

let session = null; // { token, key_configured, default_histories, max_histories }; memory only
let pending = null; // the action waiting for confirmation
let investigationView = null; // last finished investigation, kept while the page is open
let busy = false;
let focusNext = null; // element to focus once controls are enabled again

function post(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-ReviewRing-Token": session.token },
    body: JSON.stringify(body),
  });
}

function liveError(message) {
  $("live-error").hidden = !message;
  $("live-error").textContent = message || "";
}

function setBusy(value) {
  busy = value;
  for (const control of $("investigate").querySelectorAll("button, input")) control.disabled = value;
}

function askConfirm(plan, run) {
  pending = run;
  $("confirm-text").textContent = plan.business ? `${plan.business}: ${plan.statement}` : plan.statement;
  $("confirm-yes").textContent = plan.max_requests === 0 ? "Continue (no request sent)" : "Confirm and send";
  $("confirm").hidden = false;
  focusNext = $("confirm-yes");
}

function closeConfirm() {
  pending = null;
  $("confirm").hidden = true;
}

async function guarded(action) {
  liveError("");
  setBusy(true);
  try {
    await action();
  } catch (error) {
    liveError(error.message);
  } finally {
    setBusy(false);
    // Disabled controls cannot take focus, so focus moves only now.
    if (focusNext) focusNext.focus();
    focusNext = null;
  }
}

async function openInvestigate() {
  $("state").hidden = true;
  if (!session) {
    try {
      session = await request("/api/session");
    } catch (error) {
      return liveError("The server could not be reached. Check that it is still running, then reload.");
    }
    $("key-missing").hidden = session.key_configured;
    $("histories").max = String(session.max_histories);
    $("histories").value = String(session.default_histories);
    $("histories-label").textContent = `Reviewer histories to collect (0 to ${session.max_histories})`;
    $("history-limit").min = String(session.min_history_limit);
    $("history-limit").max = String(session.max_history_limit);
    $("history-limit").value = String(session.default_history_limit);
    $("history-limit-label").textContent = `Entries to request per history (${session.min_history_limit} to ${session.max_history_limit})`;
  }
  if (investigationView && modeFromHash() === "investigate") show(investigationView);
}

function showResults(businesses) {
  const items = businesses.map((business, index) => {
    const label = el("label", { class: "result" });
    const radio = el("input", { type: "radio", name: "business", value: business.ref });
    if (index === 0) radio.required = true;
    const text = el("span");
    const facts = [
      business.category,
      business.rating === null ? null : `rated ${business.rating}`,
      business.reviews === null ? null : plural(business.reviews, "review"),
      business.address,
    ].filter(Boolean);
    text.append(el("strong", {}, business.title), el("small", {}, facts.join(" · ") || "No further details returned"));
    label.append(radio, text);
    return label;
  });
  replace($("results"), ...(items.length ? items : [el("p", { class: "muted" }, "No businesses were returned for this search.")]));
  $("results-form").hidden = false;
  $("results-form").querySelector('button[type="submit"]').hidden = !items.length;
  if (items.length) focusNext = items[0].querySelector("input");
}

function progressText(status) {
  return `Collecting: ${plural(status.requests_sent, "new request")} sent of at most ${status.max_requests}, ` +
    `${plural(status.saved_responses_used, "saved response")} reused, ` +
    `${status.histories_collected} of ${status.histories_planned || status.histories_requested} reviewer histories collected.`;
}

function finishInvestigation(status) {
  $("progress").hidden = true;
  const lines = [
    `${status.business}: ${plural(status.requests_sent, "new SerpApi request")} sent (limit ${status.max_requests}), ${plural(status.saved_responses_used, "saved response")} reused, ${plural(status.histories_collected, "reviewer history", "reviewer histories")} collected.`,
  ];
  if (status.histories_planned < status.histories_requested && status.state !== "failed") {
    lines.push(`Only ${plural(status.histories_planned, "reviewer")} on the first page reported more than one review, so fewer histories were requested than asked for.`);
  }
  if (status.state === "partial") lines.push(`Stopped early: ${status.stopped} What was collected before that is shown below.`);
  if (status.state === "failed") lines.push(`Nothing could be collected: ${status.stopped}`);
  if (status.state === "unviewable") {
    lines.push(`${plural(status.responses_collected, "response")} collected and saved on this computer, but the result could not be displayed, so no summary is shown.${status.stopped ? ` Collection also stopped early: ${status.stopped}` : ""}`);
  }
  if (status.histories_collected) lines.push(`Up to ${status.history_limit} entries were requested per history; see Coverage for how complete the retrieved histories appear.`);
  replace($("live-summary"), ...lines.map((line) => el("p", {}, line)));
  $("live-summary").hidden = false;
  if (status.view) {
    investigationView = status.view;
    if (modeFromHash() === "investigate") show(status.view);
  }
}

async function follow(id) {
  $("progress").hidden = false;
  for (;;) {
    const status = await request(`/api/investigations/${id}`, { headers: { "X-ReviewRing-Token": session.token } });
    if (status.state !== "running") return finishInvestigation(status);
    $("progress").textContent = progressText(status);
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy || !session) return;
  closeConfirm();
  const query = $("query").value;
  guarded(async () => {
    const plan = await post("/api/search/plan", { query });
    askConfirm(plan, async () => showResults((await post("/api/search", { plan: plan.plan })).businesses));
  });
});

$("results-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy || !session) return;
  closeConfirm();
  const chosen = $("results-form").querySelector('input[name="business"]:checked');
  const histories = Number.parseInt($("histories").value, 10);
  if (!chosen) return liveError("Choose a business first.");
  guarded(async () => {
    const history_limit = Number.parseInt($("history-limit").value, 10);
    const plan = await post("/api/investigations/plan", { business: chosen.value, histories, history_limit });
    askConfirm(plan, async () => {
      $("live-summary").hidden = true;
      $("dashboard").hidden = true;
      const started = await post("/api/investigations", { plan: plan.plan });
      $("progress").textContent = "Collecting: starting.";
      await follow(started.investigation);
    });
  });
});

$("confirm-yes").addEventListener("click", () => {
  const run = pending;
  closeConfirm();
  if (run) guarded(run).then(() => { $("progress").hidden = true; });
});
$("confirm-no").addEventListener("click", closeConfirm);

// ---------- wiring ----------

for (const mode of MODES) {
  $(`mode-${mode}`).addEventListener("click", () => { location.hash = mode; });
}
$("clear").addEventListener("click", () => select(null));
window.addEventListener("hashchange", () => load(modeFromHash()));

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (current && !$("dashboard").hidden) renderNetwork(current); }, 150);
});

load(modeFromHash());
