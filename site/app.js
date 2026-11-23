const API = "https://api.shipwatch.bwltd.kr";
const $ = selector => document.querySelector(selector);
let state = {targets: [], events: []};
let filter = "all";
let project = null;  // mint of the one project the feed is narrowed to, if any
let busy = false;
const labels = {baseline: "FIRST OBSERVATION", page: "PAGE EDIT", assets: "CODE / STYLE CHANGE", release: "LINKED RELEASE", repository: "REPOSITORY LINK", unavailable: "CHECK FAILED", recovered: "BACK ONLINE"};
const create = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const stamp = value => value ? new Date(value).toLocaleString(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "Waiting for first check";
const utc = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
};
const exact = value => value ? stamp(value) + (utc(value) ? " · " + utc(value) : "") : stamp(value);
const age = value => {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
  return Math.floor(seconds / 86400) + "d ago";
};

function link(text, href) {
  const node = create("a", "", text);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

async function copyDeepLink(button, hash) {
  const url = new URL(location.href);
  url.hash = hash;
  try {
    await navigator.clipboard.writeText(url.href);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Link in address bar";
    location.hash = hash;
  }
}

// A project link (#watch-<mint>) opens the feed narrowed to that project.
function openLinkedProject() {
  const id = decodeURIComponent(location.hash.slice(1));
  if (!id.startsWith("watch-") || !state.targets.some(target => target.mint === id.slice(6))) return;
  if (project === id.slice(6)) return;
  project = id.slice(6);
  draw();
}

function openLinkedEvidence() {
  const id = decodeURIComponent(location.hash.slice(1));
  if (!id.startsWith("event-")) return;
  const card = document.getElementById(id);
  if (!card) return;
  const details = card.querySelector("details");
  if (details) details.open = true;
  card.scrollIntoView({block: "center"});
}

function recurrenceCount(event, index) {
  if (event.kind !== "page") return Number(event.repeat_count) || 0;
  const signature = [...event.added, ...event.removed].filter(Boolean).sort().join("\n");
  const visibleFallback = signature && state.events.slice(index + 1).some(previous =>
    previous.kind === "page" && previous.mint === event.mint &&
    [...previous.added, ...previous.removed].filter(Boolean).sort().join("\n") === signature
  ) ? 1 : 0;
  return Math.max(Number(event.repeat_count) || 0, visibleFallback);
}

function draw() {
  const events = $("#events");
  events.replaceChildren();
  const groups = {all: Object.keys(labels), changes: ["page", "assets", "release", "repository"], availability: ["unavailable", "recovered"], baseline: ["baseline"]};
  const watched = project && state.targets.find(target => target.mint === project);
  if (project && state.targets.length && !watched) project = null;
  const filtered = state.events.filter((event, index) => (!project || event.mint === project) &&
    (filter === "novel" ? recurrenceCount(event, index) === 0 : groups[filter].includes(event.kind)));
  const narrowed = $("#project-filter");
  narrowed.hidden = !watched;
  narrowed.replaceChildren();
  if (watched) {
    const everything = create("button", "", "Show every project");
    everything.type = "button";
    everything.addEventListener("click", () => {
      project = null;
      if (location.hash.startsWith("#watch-")) history.replaceState(null, "", location.pathname + location.search);
      draw();
    });
    narrowed.append(create("span", "", `Only $${watched.symbol} · ${watched.name}`), everything);
  }
  $("#feed-status").hidden = filtered.length > 0;
  $("#feed-status").textContent = !state.targets.length ? "The watchlist is empty. Add a mint to record its first website observation."
    : watched ? "Nothing from this project in this view of the feed yet. Later checks will appear here."
    : "No observations in this filter yet. Later checks will appear here.";
  for (const event of filtered) {
    const sourceIndex = state.events.indexOf(event);
    const repeats = recurrenceCount(event, sourceIndex);
    const recurring = repeats > 0;
    const card = create("li", "event");
    card.id = "event-" + event.id;
    card.dataset.kind = event.kind;
    card.dataset.recurring = String(recurring);
    card.dataset.associationAmbiguous = String(Boolean(event.association_ambiguous));
    const top = create("div", "event-top");
    const kind = create("span", "event-kind", labels[event.kind]);
    if (recurring) kind.append(create("span", "event-repeat", `RECURRING · SEEN ${repeats} TIME${repeats === 1 ? "" : "S"} BEFORE`));
    if (event.association_ambiguous) kind.append(create("span", "event-association", "ATTRIBUTION PAUSED"));
    const observed = create("time", "", age(event.observed));
    observed.dateTime = event.observed;
    if (utc(event.observed)) observed.title = utc(event.observed);
    top.append(kind, observed);
    const heading = create("h3");
    heading.append(create("strong", "", "$" + event.symbol), document.createTextNode(event.name));
    const details = create("details");
    details.append(create("summary", "", "Inspect evidence"));
    const evidence = create("div", "evidence");
    evidence.append(create("p", "evidence-id", "Evidence #" + event.id));
    evidence.append(create("p", "", "Observed " + exact(event.observed)));
    if (recurring && event.kind === "page") evidence.append(create("p", "recurring-note", `The same visible before/after excerpts appeared in ${repeats} earlier stored observation${repeats === 1 ? "" : "s"}. This may be rotating content; inspect the website before treating it as a new catalyst.`));
    if (recurring && event.kind === "assets") evidence.append(create("p", "recurring-note", `This exact set of script and stylesheet bytes was already seen in ${repeats} earlier check${repeats === 1 ? "" : "s"} of this site. It is probably alternating between builds. Only the most recent recurrence is listed.`));
    if (event.association_ambiguous) evidence.append(create("p", "association-note", "Shipwatch observed multiple repository links for this watched website across stored checks, so this repository cannot be confidently attributed to the token project."));
    if (event.previous_observation) evidence.append(create("p", "", "Previous check " + exact(event.previous_observation)));
    for (const text of event.removed) evidence.append(create("p", "removed", "− " + text));
    for (const text of event.added) evidence.append(create("p", "added", "+ " + text));
    if (event.kind === "assets") evidence.append(create("p", "", "The fetched script or stylesheet bytes differ from the previous check. Inspect the product to establish what changed."));
    const fingerprint = event.kind === "assets" ? "asset set fingerprint " : "page fingerprint ";
    if (event.before_hash) evidence.append(create("p", "hash", "Previous " + fingerprint + event.before_hash));
    if (event.after_hash) evidence.append(create("p", "hash", "Current " + fingerprint + event.after_hash));
    const actions = create("div", "event-links");
    const shareEvidence = create("button", "", "Copy evidence link");
    shareEvidence.type = "button";
    shareEvidence.addEventListener("click", () => copyDeepLink(shareEvidence, "event-" + event.id));
    const shareProject = create("button", "", "Copy project link");
    shareProject.type = "button";
    shareProject.addEventListener("click", () => copyDeepLink(shareProject, "watch-" + event.mint));
    actions.append(link("Open website ↗", event.url), link("Mint ↗", "https://solscan.io/token/" + event.mint), shareEvidence, shareProject);
    if (event.detail_url) actions.append(link(event.kind === "release" ? "Open release ↗" : "Repository ↗", event.detail_url));
    details.append(evidence);
    card.append(top, heading, create("p", "event-summary", event.summary), details, actions);
    events.append(card);
  }
  const targets = $("#targets");
  targets.replaceChildren();
  $("#watch-count").textContent = state.targets.length + " / 40";
  $("#target-status").hidden = state.targets.length > 0;
  $("#target-status").textContent = "Add a mint to start watching.";
  const orderedTargets = [...state.targets].sort((left, right) =>
    new Date(right.latest_signal?.observed || 0) - new Date(left.latest_signal?.observed || 0)
  );
  for (const target of orderedTargets) {
    const row = create("li", "target");
    row.id = "watch-" + target.mint;
    const heading = create("div", "target-heading");
    const status = target.status === "online" ? "RESPONDED" : target.status === "pending" ? "PENDING" : "UNAVAILABLE";
    heading.append(create("h3", "", "$" + target.symbol + " · " + target.name), create("span", "target-status " + target.status, status));
    const website = link(new URL(target.url).hostname + " ↗", target.url);
    website.className = "target-site";
    row.append(heading, website, create("p", "target-check", "Checked " + (target.checked ? age(target.checked) : "pending")));
    if (target.latest_signal) {
      const signal = create("a", "target-signal", `Latest new · ${labels[target.latest_signal.kind]} · ${age(target.latest_signal.observed)}`);
      signal.href = "#event-" + target.latest_signal.id;
      signal.addEventListener("click", event => {
        event.preventDefault();
        filter = "all";
        if (project && project !== target.mint) project = null;
        for (const button of document.querySelectorAll("[data-filter]")) button.setAttribute("aria-pressed", String(button.dataset.filter === "all"));
        location.hash = "event-" + target.latest_signal.id;
        draw();
      });
      row.append(signal);
    }
    if (target.assets_seen) row.append(create("p", "target-check", `${target.assets_read}/${target.assets_seen} linked assets observed`));
    if (target.release_info) {
      const release = target.release_info;
      if (release.repo) {
        const repo = link(release.repo + " ↗", "https://github.com/" + release.repo);
        repo.className = "target-site";
        row.append(repo);
        row.append(create("p", "target-check", release.error ? "Release lookup incomplete" : release.tag ? "Release " + release.tag : "No public release listed"));
      } else if (release.ambiguous) {
        row.append(create("p", "target-check", `${release.count} GitHub repositories linked · release attribution paused`));
      } else if (release.error) {
        row.append(create("p", "target-check", "Release lookup incomplete"));
      }
    }
    if (target.error) row.append(create("p", "target-error", target.error));
    const only = create("button", "target-only", project === target.mint ? "Showing only this project" : "Only this project in the feed");
    only.type = "button";
    only.setAttribute("aria-pressed", String(project === target.mint));
    only.addEventListener("click", () => {
      project = project === target.mint ? null : target.mint;
      draw();
      if (project) $("#feed-heading").scrollIntoView({block: "start"});
    });
    row.append(only);
    targets.append(row);
  }
  openLinkedEvidence();
}

async function refresh() {
  if (busy) return;
  busy = true;
  $("#refresh").disabled = true;
  try {
    const response = await fetch(API + "/api/feed", {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error("The observer is temporarily unavailable.");
    const value = await response.json();
    if (!Array.isArray(value.events) || !Array.isArray(value.targets)) throw new Error("The observer returned an invalid feed.");
    const first = !state.targets.length;
    state = value;
    draw();
    if (first) openLinkedProject();
    $("#connection").textContent = "Feed checked " + new Date().toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"});
    $("#connection-dot").className = "online";
  } catch {
    $("#connection").textContent = "Connection unavailable";
    $("#connection-dot").className = "";
    if (!state.events.length) {
      $("#feed-status").hidden = false;
      $("#feed-status").textContent = "The live observer could not be reached. Try refreshing shortly.";
      $("#target-status").textContent = "Watchlist currently unavailable.";
    }
  } finally {
    busy = false;
    $("#refresh").disabled = false;
  }
}

$("#watch-form").addEventListener("submit", async event => {
  event.preventDefault();
  const field = $("#mint");
  const status = $("#form-status");
  const mint = field.value.trim();
  if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) {
    status.textContent = "Paste a full Solana mint address.";
    status.className = "error";
    return;
  }
  $("#watch-submit").disabled = true;
  status.className = "";
  status.textContent = "Looking up the linked website…";
  try {
    const response = await fetch(API + "/api/watch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mint}), signal: AbortSignal.timeout(20000)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "The watch could not be added.");
    status.textContent = value.created ? "Added. The first observation will appear shortly." : "This mint is already on watch.";
    await refresh();
    location.hash = "watch-" + mint;
  } catch (error) {
    status.className = "error";
    status.textContent = error.message || "The observer could not be reached.";
  } finally {
    $("#watch-submit").disabled = false;
  }
});
for (const button of document.querySelectorAll("[data-filter]")) {
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    for (const other of document.querySelectorAll("[data-filter]")) other.setAttribute("aria-pressed", String(other === button));
    draw();
  });
}
$("#refresh").addEventListener("click", refresh);
window.addEventListener("hashchange", () => { openLinkedProject(); openLinkedEvidence(); });
refresh();
setInterval(refresh, 30000);

// Token status. token.json is generated from token.toml in the repository.
// Until a launch is recorded there, token-plan.json states what is planned
// and names the one wallet that will create the token.
async function drawToken() {
  const panel = $("#token-panel");
  const json = async path => {
    const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  };
  try {
    const token = await json("./token.json");
    const lead = create("div", "token-lead");
    const detail = create("div", "token-detail");
    if (token.status === "launched") {
      lead.append(create("p", "token-tag", "OFFICIAL TOKEN · " + String(token.launchpad || "").toUpperCase()),
        create("p", "token-headline", "$" + token.symbol + " is live."));
      const copy = create("button", "", "Copy mint");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(token.mint); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy"; }
      });
      const actions = create("p", "token-links");
      actions.append(copy, link("Coin page ↗", token.coin_url || "https://pump.fun/coin/" + token.mint));
      const verify = create("a", "", "Check it is ours ↗");
      verify.href = "./verify.html#token";
      actions.append(verify);
      detail.append(create("p", "token-mint", token.mint), actions,
        create("p", "", "This is the only Shipwatch token. Check the full address, not just the first and last characters."));
      panel.replaceChildren(lead, detail);
      panel.dataset.state = "live";
      return;
    }
    const plan = await json("./token-plan.json");
    lead.append(create("p", "token-tag", "TOKEN · NOT LAUNCHED YET"),
      create("p", "token-headline", "Launching " + plan.planned.replace(/ \d{4}.*$/, "") + "."),
      create("p", "token-when", plan.planned + " · " + plan.launchpad));
    const wallet = create("p", "token-mint");
    wallet.append(link(plan.creator_wallet, "https://solscan.io/account/" + plan.creator_wallet));
    const how = create("p", "token-links");
    const verify = create("a", "", "How to check it is ours ↗");
    verify.href = "./verify.html#token";
    how.append(verify);
    detail.append(create("p", "", "One wallet will create it, and we are naming that wallet before the launch. On the day, compare the creator shown on the coin page with this address:"),
      wallet,
      create("p", "", "No address exists yet. A token that uses this name before the launch is recorded in our repository is not ours."),
      how);
    panel.replaceChildren(lead, detail);
    panel.dataset.state = "planned";
  } catch {
    panel.textContent = "Token status could not be loaded. Treat any address as unverified until the verify page shows it.";
    panel.dataset.state = "unknown";
  }
}
drawToken();

// Name the exact source commit this page was built from. The verify page
// explains how to check that claim without trusting this script.
fetch("./build.json", {cache: "no-store"}).then(response => response.ok ? response.json() : null).then(build => {
  if (!build || !build.source || !build.source.commit) return;
  $("#build-link").textContent = `Open source · v${build.version} · ${build.source.commit.slice(0, 7)} · verify ↗`;
}).catch(() => {});
