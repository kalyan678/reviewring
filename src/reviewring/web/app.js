// ReviewRing dashboard. Renders the view model from /api/view and nothing else.
// All text is inserted with textContent; no markup is built from data.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const MODES = ["real", "synthetic", "investigate"];
const ROW_HEIGHT = 44;

const $ = (id) => document.getElementById(id);
let current = null; // the view being shown
let pinned = null; // persistent: a selected node or a focused group { ids: Set, title, lines, focus?, group? }
let hover = null; // temporary: the node under the pointer or keyboard focus
let demoSteps = 0; // expansion steps applied to the synthetic demo; reset whenever the demo is opened
let hosted = false; // true when the server is the public demo, which serves fictional data only
let startMode = "synthetic"; // where the page opens when the address names no tab; set from the server's answer

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
  if (hosted) return "synthetic"; // the public demo has no other mode
  const mode = location.hash.slice(1);
  return MODES.includes(mode) ? mode : startMode;
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
  // A new view (another tab, or an expansion step) starts from the full network.
  pinned = null;
  hover = null;
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
    demoSteps = 0;
    const view = await request(`/api/view?mode=${mode}`);
    if (mode !== modeFromHash()) return; // a newer selection replaced this one
    if (!view.available) return showUnavailable(view);
    show(view);
  } catch (error) {
    if (mode !== modeFromHash()) return;
    showState(
      "The view could not be loaded",
      hosted ? "The demo server may still be starting. Wait a few seconds, then reload." : "Check that the ReviewRing server is still running, then reload."
    );
  }
}

const NETWORK_USE = {
  offline: "This page runs entirely on your computer and makes no outside request.",
  hosted: "This hosted page serves fictional data only. It contacts no other service, uses no SerpApi key, and stores nothing in your browser.",
  investigate:
    "This page runs on your computer and loads nothing from outside. In this mode the local ReviewRing server contacts SerpApi, and only after you confirm each step.",
};

function showBanner(mode) {
  $("network-use").textContent = hosted ? NETWORK_USE.hosted : mode === "investigate" ? NETWORK_USE.investigate : NETWORK_USE.offline;
  const banner = $("banner");
  banner.className = mode === "synthetic" ? "banner synthetic" : "banner";
  $("demo-about").hidden = mode !== "synthetic";
  if (mode === "investigate") {
    replace(
      banner,
      el("strong", {}, "Live investigation"),
      document.createTextNode(
        " Collects real data through SerpApi and uses your credits, only after you confirm each step. Results are shown as totals and groups: no reviewer is named."
      )
    );
  } else if (mode === "synthetic") {
    replace(
      banner,
      el("strong", {}, "Fictional data"),
      document.createTextNode(" Invented reviewers and businesses. No SerpApi key, request or credit is used."),
      hosted
        ? el("span", { class: "hosted-note" }, " This public demo serves only this fictional walkthrough. Live investigation is switched off here and runs only on your own computer.")
        : null
    );
  } else {
    replace(
      banner,
      el("strong", {}, "Collected data"),
      document.createTextNode(
        " Read-only. Everything saved on this computer, from all searches and investigations, which may be unrelated. It is not one investigation. Totals and groups only: no reviewer or business is named."
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
  const button = el("button", { type: "button" }, "Explore the demo");
  button.addEventListener("click", () => { location.hash = "synthetic"; });
  showState(
    "No collected data on this computer",
    `${view.reason} Responses are saved here when you run an investigation, and they are not part of the repository. The demo needs no saved data.`,
    button
  );
}

// ---------- sections ----------

function render(view) {
  renderTiles(view);
  $("headline").textContent = view.explanation.headline;
  $("assessment").textContent = view.explanation.assessment.statement;
  replace($("points"), ...view.explanation.points.map((point) => el("li", {}, point)));
  renderDemoExpansion(view);
  renderNetwork(view);
  renderCoverage(view);
  renderIndicators(view);
  renderRatings(view);
}

// The synthetic demo's own expansion stage: same rule and wording as a real
// expansion, computed locally on fictional data. Nothing is sent anywhere.
function renderDemoExpansion(view) {
  const expansion = view.synthetic ? view.expansion : null;
  $("demo-expansion").hidden = !expansion;
  $("legend-added").hidden = !(expansion && expansion.new_nodes.length);
  if (!expansion) return;
  const stages = ["Initial collection"];
  for (let step = 1; step <= expansion.steps; step += 1) stages.push(`Expansion ${step}`);
  if (expansion.available) stages.push(`Expansion ${expansion.steps + 1}`);
  replace(
    $("demo-steps"),
    ...stages.map((label, index) => {
      const item = el("li", {}, label);
      if (index < expansion.steps) item.className = "done";
      else if (index === expansion.steps) { item.className = "current"; item.setAttribute("aria-current", "step"); }
      return item;
    })
  );
  $("demo-stage").textContent = expansion.steps
    ? `${plural(expansion.performed, "shared business", "shared businesses")} expanded so far. Nodes added by the latest step are highlighted in the network.`
    : "Initial collection: three review pages and eight reviewer histories. Expanding collects the review page of a business two overlapping reviewers share, to see who else reviewed it.";
  if (!expansion.available) $("demo-stage").textContent += " No further business qualifies, so expansion stops here.";
  $("demo-statement").textContent = expansion.statement;
  replace($("demo-compare"), expansion.comparison.length ? comparisonTable(expansion.comparison) : null);
  $("demo-expand").hidden = !expansion.available;
  $("demo-expand").disabled = false;
  $("demo-reset").hidden = !expansion.steps;
  $("demo-error").hidden = true;
  $("demo-result").hidden = !expansion.findings.length;
  replace($("demo-findings"), ...expansion.findings.map((line) => el("li", {}, line)));
  replace($("demo-limits"), ...expansion.limits.map((line) => el("li", {}, line)));
}

async function loadDemoStep(steps) {
  $("demo-expand").disabled = true;
  $("demo-error").hidden = true;
  try {
    const view = await request(`/api/view?mode=synthetic&expansions=${steps}`);
    if (modeFromHash() !== "synthetic") return;
    demoSteps = view.expansion.steps;
    show(view);
    $("demo-expansion-title").focus();
  } catch (error) {
    $("demo-expand").disabled = false;
    $("demo-error").textContent = "The expansion could not be shown. Check that the ReviewRing server is still running, then try again.";
    $("demo-error").hidden = false;
  }
}

function renderTiles(view) {
  const m = view.metrics;
  const minimum = view.indicators.minimum_shared_businesses;
  const tiles = [
    [m.reviewers, "reviewers"],
    [m.businesses, "businesses"],
    [m.edges, "reviewer-business links"],
    [m.pairs_sharing_two, `pairs sharing ${minimum}+ businesses`, true],
  ];
  // After an expansion of the demo, each figure also shows how it changed.
  const compared = new Map(((view.synthetic && view.expansion && view.expansion.comparison) || []).map((row) => [row.key, row]));
  const keys = ["reviewers", null, "edges", "pairs"];
  replace(
    $("tiles"),
    ...tiles.map(([value, label, hero], index) => {
      const tile = el("div", { class: hero ? "tile hero" : "tile" });
      tile.append(el("span", { class: "value" }, String(value)));
      const row = compared.get(keys[index]);
      if (row) {
        const change = row.after - row.before;
        const delta = el("span", { class: change ? "delta" : "delta none" }, change ? signed(change) : "0");
        delta.setAttribute("title", "Change since the initial collection");
        delta.setAttribute("aria-label", `${signed(change)} since the initial collection`);
        tile.append(delta);
      }
      tile.append(el("div", { class: "label" }, label));
      return tile;
    })
  );
}

function signed(change) {
  return change > 0 ? `+${change}` : change < 0 ? `\u2212${-change}` : "No change";
}

// A closed-by-default row for detail that supports a figure but need not be read first.
function disclosure(summary, ...children) {
  const details = el("details", { class: "more" });
  details.append(el("summary", {}, summary), ...children.filter(Boolean));
  return details;
}

// Before and after an expansion, from the figures the server reports.
function comparisonTable(comparison) {
  const table = el("table", { class: "compare" });
  const head = el("tr");
  for (const [label, cls] of [["", ""], ["Before", "num"], ["After", "num"], ["Change", "num"]]) {
    head.append(el("th", cls ? { class: cls, scope: "col" } : { scope: "col" }, label));
  }
  table.append(head);
  for (const row of comparison) {
    const line = el("tr");
    const change = row.after - row.before;
    line.append(
      el("td", {}, row.label),
      el("td", { class: "num" }, String(row.before)),
      el("td", { class: "num after" }, String(row.after)),
      el("td", { class: change ? "num change" : "num change none" }, signed(change))
    );
    table.append(line);
  }
  return table;
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
    el("p", { class: "muted" }, "Filled bar: collected. Empty track: known to exist but not collected."),
    el("h3", {}, "Reviews collected per business"),
  ];
  for (const row of c.business_rows) {
    let note = `${plural(row.pages, "review page")} collected.`;
    if (row.not_collected) note += ` ${row.not_collected} reported reviews not collected.`;
    if (row.further_pages) note += " A token for a further page was provided and not used.";
    if (row.expanded) {
      note = "Collected by expansion: first review page only.";
      if (row.withheld) note += " Its reported total is withheld.";
      else if (row.not_collected) note += ` ${row.not_collected} reported reviews not collected.`;
    }
    parts.push(meter(row.label, row.captured, row.reported_total, note));
  }
  if (c.page_statement) parts.push(el("p", { class: "muted" }, c.page_statement));
  parts.push(
    el("h3", {}, "Reviewer histories collected"),
    meter(
      "Reviewers with their review history collected",
      c.reviewers_with_history,
      c.reviewers,
      "Without a history, only the business a reviewer was found on is known."
    ),
    disclosure("How complete the retrieved histories are", el("p", { class: "muted" }, c.history_windows.statement)),
    el(
      "p",
      { class: "caveat" },
      `${plural(c.history_only_businesses, "business", "businesses")} ${c.history_only_businesses === 1 ? "appears" : "appear"} only in a reviewer history, with no review page collected. Their other reviewers are unknown, not absent.`
    )
  );
  replace($("coverage"), ...parts);
}

function renderIndicators(view) {
  const ind = view.indicators;
  const rule = disclosure(
    "The rule behind this",
    el(
      "p",
      { class: "muted" },
      `Two reviewers are linked when both reviewed at least ${ind.minimum_shared_businesses} of the same businesses. Linked reviewers form a group. The number ${ind.minimum_shared_businesses} is a starting point for exploration, not a validated threshold.`
    )
  );
  const parts = [];
  const list = (items) => {
    const ul = el("ul", { class: "points" });
    for (const item of items) ul.append(el("li", {}, item));
    return ul;
  };
  if (!ind.group_count) parts.push(el("p", { class: "verdict" }, ind.evidence.assessed ? "No group found." : "Overlap not assessed."));
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
          if (!(pinned && pinned.group === group.label)) focusGroup(group);
          $("graph").scrollIntoView({ block: "nearest" });
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
      { class: "caveat" },
      "A group is a pattern to look into, not a conclusion. People share businesses for ordinary reasons, and this indicator cannot tell those apart from arranged reviews. It is not a probability and not evidence of fraud."
    ),
    rule
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
        el("td", { class: "num" }, b.withheld ? "withheld" : b.reported_rating === null ? "not reported" : String(b.reported_rating)),
        el("td", { class: "num" }, b.withheld ? "withheld" : b.reported_total === null ? "not reported" : String(b.reported_total)),
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
  parts.push(disclosure("Why there is no adjusted rating", el("p", { class: "muted" }, r.adjusted_rating.reason)));
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

function nodeSelection(node) {
  const byId = new Map(current.network.nodes.map((n) => [n.id, n]));
  const connections = current.network.links
    .filter((link) => link.source === node.id || link.target === node.id)
    .map((link) => {
      const other = byId.get(link.source === node.id ? link.target : link.source);
      return link.count > 1 ? `${other.label} (${link.count} links)` : other.label;
    });
  return { ids: neighbours(node.id), title: node.label, lines: node.detail, connections, focus: node.id };
}

// What the network emphasises has two layers:
//   pinned  - persistent: one selected node, or one focused overlap group. A new pin replaces the old one.
//   hover   - temporary: the node under the pointer or keyboard focus. It is shown while it lasts and
//             then gives way to the pin again; it never changes the pin.
// Only relationships that are drawn are emphasised: a node, the lines that touch it, and the nodes at
// their other ends. Reviewers are never joined to each other directly, because the data links them
// only through the businesses they both reviewed.
function pin(next) {
  pinned = next;
  hover = null;
  applySelection();
}

function preview(next) {
  hover = next;
  applySelection();
}

function clearSelection() {
  pinned = null;
  hover = null;
  applySelection();
}

function focusGroup(group) {
  if (pinned && pinned.group === group.label) return clearSelection();
  pin({
    group: group.label,
    ids: new Set(group.node_ids),
    title: group.label,
    lines: [
      `${plural(group.reviewers, "reviewer")}, ${plural(group.shared_businesses, "shared business", "shared businesses")}, ${plural(group.pairs, "linked pair")}.`,
      `Review history collected for ${group.histories_fetched} of ${group.reviewers} members.`,
      "The lines shown are members' reviews of businesses at least two of them reviewed.",
      "A group is a pattern to look into, not a conclusion.",
    ],
  });
}

function applySelection() {
  const graph = $("graph");
  const shown = hover || pinned;
  const ids = shown ? shown.ids : null;
  for (const node of graph.querySelectorAll(".node")) {
    const id = node.dataset.id;
    const isPinned = Boolean(pinned) && pinned.focus === id;
    node.classList.toggle("dim", Boolean(ids) && !ids.has(id));
    node.classList.toggle("selected", isPinned);
    node.classList.toggle("hovered", Boolean(hover) && hover.focus === id);
    node.setAttribute("aria-pressed", String(isPinned));
  }
  for (const link of graph.querySelectorAll(".link")) {
    const active = Boolean(ids) && ids.has(link.dataset.source) && ids.has(link.dataset.target) &&
      (!shown.focus || link.dataset.source === shown.focus || link.dataset.target === shown.focus);
    link.classList.toggle("active", active);
    link.classList.toggle("dim", Boolean(ids) && !active);
  }
  $("clear").hidden = !pinned;
  $("clear").textContent = pinned && pinned.group ? "Show full network" : "Clear selection";
  for (const button of $("group-focus").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(Boolean(pinned) && pinned.group === button.dataset.group));
  }
  const detail = $("detail");
  if (!shown) {
    replace(detail, el("h3", {}, "Selection"), el("p", { class: "title" }, "Nothing selected"), el("p", { class: "muted" }, "Point at a node to preview its connections. Click, tap or press Enter to keep it selected; Escape clears."));
    return;
  }
  // The state is named in words, so it does not depend on colour.
  const kind = shown === hover ? "Preview" : shown.group ? "Group focus" : "Selected";
  const lines = el("ul");
  for (const line of shown.lines) lines.append(el("li", {}, line));
  const parts = [el("h3", {}, kind), el("p", { class: "title" }, shown.title), lines];
  if (shown.connections && shown.connections.length) {
    const list = el("ul", { class: "connections" });
    for (const item of shown.connections) list.append(el("li", {}, item));
    parts.push(el("h3", {}, "Connected to"), list);
  }
  if (shown === hover && pinned && pinned.focus !== hover.focus) {
    parts.push(el("p", { class: "muted" }, `${pinned.title} stays ${pinned.group ? "in focus" : "selected"}.`));
  }
  replace(detail, ...parts);
}

function renderNetwork(view) {
  const network = view.network;
  $("network-note").textContent = network.aggregated
    ? "Reviewers with the same connections are merged into one circle, sized by how many they are. Businesses are labelled by letter. Individual reviewers are not shown."
    : "Each circle is one fictional reviewer and each square one fictional business. A line means that reviewer reviewed that business.";
  $("network-chip").hidden = !view.synthetic;
  $("network-suppressed").hidden = true;
  const hidden = network.suppressed;
  if (hidden && hidden.reviewers) {
    $("network-suppressed").hidden = false;
    $("network-suppressed").textContent = `${plural(hidden.reviewers, "reviewer")} with ${plural(hidden.links, "link")} ${hidden.reviewers === 1 ? "is" : "are"} not drawn: fewer than ${hidden.minimum_group_size} reviewers share that connection pattern, and drawing it would single them out. The totals above still include them.`;
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
    const added = view.expansion && view.expansion.new_nodes && view.expansion.new_nodes.includes(node.id);
    const group = svg("g", { class: `node ${node.kind}${added ? " added" : ""}`, tabindex: "0", role: "button", "aria-pressed": "false", "aria-label": `${node.label}. ${node.detail.join(" ")}` });
    group.dataset.id = node.id;
    group.append(svg("rect", { class: "hit", x: String(x - 22), y: String(y - ROW_HEIGHT / 2), width: "44", height: String(ROW_HEIGHT) }));
    if (added) group.append(svg("circle", { class: "halo", cx: String(x), cy: String(y), r: String(r + 11) }));
    // A second outline, drawn only for the selected node: the state is a shape, not only a colour.
    group.append(svg("circle", { class: "pin", cx: String(x), cy: String(y), r: String(r + (node.group ? 10 : 6) + (node.incomplete ? 4 : 0)) }));
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

    const enter = () => preview(nodeSelection(node));
    const leave = () => { if (hover && hover.focus === node.id) preview(null); };
    group.addEventListener("mouseenter", enter);
    group.addEventListener("mouseleave", leave);
    group.addEventListener("focus", enter);
    group.addEventListener("blur", leave);
    group.addEventListener("click", () => {
      // Selecting the selected node again releases it; anything else replaces the pin.
      if (pinned && pinned.focus === node.id) { pinned = null; applySelection(); } else pin(nodeSelection(node));
    });
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); group.dispatchEvent(new Event("click")); }
    });
    root.append(group);
  }
  replace(container, root);

  // Focus controls for the overlap groups the server chose to describe. A group
  // it withheld has no entry here, so it cannot be focused or revealed.
  const focusable = view.indicators.groups.filter((group) => group.node_ids);
  $("group-focus").hidden = !focusable.length;
  replace(
    $("group-focus"),
    focusable.length ? el("span", { class: "focus-label" }, "Focus on") : null,
    ...focusable.map((group) => {
      const button = el("button", { type: "button", class: "focus-chip", "aria-pressed": "false" }, `${group.label} \u00b7 ${plural(group.reviewers, "reviewer")}`);
      button.dataset.group = group.label;
      button.addEventListener("click", () => focusGroup(group));
      return button;
    })
  );

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
let investigationId = null; // opaque reference to it, for the separate expansion stage
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

// Which of the four steps the panel is at, from what it is currently showing.
function syncSteps() {
  const at = !$("live-summary").hidden ? 4 : !$("confirm").hidden ? 3 : !$("results-form").hidden ? 2 : 1;
  for (const item of $("steps").children) {
    const step = Number(item.dataset.step);
    item.className = step < at ? "done" : step === at ? "current" : "";
    if (step === at) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
}

function askConfirm(plan, run) {
  pending = run;
  $("confirm-cost").textContent = plan.max_requests === 0
    ? "No new request \u00b7 0 credits"
    : `At most ${plural(plan.max_requests, "new SerpApi request")} \u00b7 ${plural(plan.max_requests, "credit")}`;
  $("confirm-text").textContent = plan.business ? `${plan.business}: ${plan.statement}` : plan.statement;
  $("confirm-yes").textContent = plan.max_requests === 0 ? "Continue (no request sent)" : "Confirm and send";
  $("confirm").hidden = false;
  focusNext = $("confirm-yes");
  syncSteps();
}

function closeConfirm() {
  pending = null;
  $("confirm").hidden = true;
  syncSteps();
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
    $("pages").max = String(session.max_review_pages);
    $("pages").value = String(session.default_review_pages);
    $("pages-label").textContent = `Review pages to collect (1 to ${session.max_review_pages})`;
    $("history-limit").min = String(session.min_history_limit);
    $("history-limit").max = String(session.max_history_limit);
    $("history-limit").value = String(session.default_history_limit);
    $("history-limit-label").textContent = `Entries to request per history (${session.min_history_limit} to ${session.max_history_limit})`;
  }
  syncSteps();
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
  syncSteps();
}

function progressText(status) {
  if (status.activity === "expanding") return "Expanding: collecting the first review page of the selected shared businesses.";
  return `Collecting: ${status.pages_collected} of ${plural(status.pages_requested, "review page")} so far, ` +
    `${plural(status.requests_sent, "new request")} sent of at most ${status.max_requests}, ` +
    `${plural(status.saved_responses_used, "saved response")} reused, ` +
    `${status.histories_collected} of ${status.histories_planned || status.histories_requested} reviewer histories collected.`;
}

function renderExpansion(expansion) {
  $("expansion").hidden = !expansion;
  if (!expansion) return;
  $("expansion-statement").textContent = expansion.statement;
  $("expansion-form").hidden = !expansion.available;
  if (expansion.available) {
    $("expansion-count").max = String(expansion.max_businesses);
    $("expansion-count").value = "1";
    $("expansion-count-label").textContent = `Shared businesses to expand (1 to ${expansion.max_businesses})`;
  }
  $("expansion-result").hidden = !expansion.findings.length;
  replace($("expansion-compare"), expansion.comparison.length ? comparisonTable(expansion.comparison) : null);
  replace($("expansion-findings"), ...expansion.findings.map((line) => el("li", {}, line)));
  replace($("expansion-limits"), ...expansion.limits.map((line) => el("li", {}, line)));
}

function finishInvestigation(status) {
  $("progress").hidden = true;
  const lines = [
    `${status.business}: ${plural(status.requests_sent, "new SerpApi request")} sent (limit ${status.max_requests}), ${plural(status.saved_responses_used, "saved response")} reused, ${plural(status.histories_collected, "reviewer history", "reviewer histories")} collected.`,
  ];
  if (status.histories_planned < status.histories_requested && status.state !== "failed") {
    lines.push(`Only ${plural(status.histories_planned, "reviewer")} on the first page reported more than one review, so fewer histories were requested than asked for.`);
  }
  if (status.state !== "failed") lines.push(status.page_statement);
  if (status.state === "partial") lines.push(`Stopped early: ${status.stopped} What was collected before that is shown below.`);
  if (status.state === "failed") lines.push(`Nothing could be collected: ${status.stopped}`);
  if (status.state === "unviewable") {
    lines.push(`${plural(status.responses_collected, "response")} collected and saved on this computer, but the result could not be displayed, so no summary is shown.${status.stopped ? ` Collection also stopped early: ${status.stopped}` : ""}`);
  }
  if (status.histories_collected) lines.push(`Up to ${status.history_limit} entries were requested per history; see Coverage for how complete the retrieved histories appear.`);
  const last = status.expansion && status.expansion.last;
  if (last) {
    let line = `Expansion: ${plural(last.requests_sent, "new SerpApi request")} sent, ${plural(last.saved_responses_used, "saved response")} reused, ${last.businesses_collected} of ${plural(last.businesses_requested, "business", "businesses")} collected.`;
    if (last.state === "partial") line += ` Stopped early: ${last.stopped} The pages collected before that were added.`;
    if (last.state === "failed") line += ` Nothing was added: ${last.stopped} The investigation above is unchanged.`;
    lines.push(line);
  }
  replace($("live-summary"), ...lines.map((line) => el("p", {}, line)));
  $("live-summary").hidden = false;
  syncSteps();
  renderExpansion(status.expansion);
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
    const pages = Number.parseInt($("pages").value, 10);
    const plan = await post("/api/investigations/plan", { business: chosen.value, histories, history_limit, pages });
    askConfirm(plan, async () => {
      $("live-summary").hidden = true;
      $("dashboard").hidden = true;
      $("expansion").hidden = true;
      syncSteps();
      const started = await post("/api/investigations", { plan: plan.plan });
      investigationId = started.investigation;
      $("progress").textContent = "Collecting: starting.";
      await follow(started.investigation);
    });
  });
});

// Expansion is a separate action with its own cost check and confirmation.
$("expansion-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy || !session || !investigationId) return;
  closeConfirm();
  const businesses = Number.parseInt($("expansion-count").value, 10);
  const investigation = investigationId;
  guarded(async () => {
    const plan = await post("/api/expansions/plan", { investigation, businesses });
    askConfirm(plan, async () => {
      await post("/api/expansions", { plan: plan.plan, investigation });
      $("progress").textContent = "Expanding: starting.";
      await follow(investigation);
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
$("clear").addEventListener("click", clearSelection);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (pinned || hover)) clearSelection();
});
$("demo-expand").addEventListener("click", () => loadDemoStep(demoSteps + 1));
$("demo-reset").addEventListener("click", () => loadDemoStep(0));
window.addEventListener("hashchange", () => load(modeFromHash()));

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (current && !$("dashboard").hidden) renderNetwork(current); }, 150);
});

// Ask the server which kind it is before showing anything. If that fails the
// page behaves as the local dashboard, and a hosted server refuses those requests.
async function start() {
  let config = {};
  try {
    config = await request("/api/config");
  } catch (error) {
    config = {};
  }
  hosted = config.hosted_demo === true;
  // The server says whether a key is configured; the page never sees the key.
  // With one, the working tool comes first; without one, the demo does.
  startMode = !hosted && config.key_configured === true ? "investigate" : "synthetic";
  if (hosted) {
    $("mode-real").hidden = true;
    $("mode-investigate").hidden = true;
  }
  load(modeFromHash());
}

start();
