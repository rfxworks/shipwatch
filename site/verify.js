const $ = selector => document.querySelector(selector);
const create = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const link = (text, href) => {
  const node = create("a", "", text);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
};
const short = digest => digest.slice(0, 12) + "…" + digest.slice(-6);
const when = value => value ? new Date(value).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "not recorded";

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function bytes(url) {
  const response = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(20000)});
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.arrayBuffer();
}

function fact(list, term, ...content) {
  const row = create("div");
  const detail = create("dd");
  detail.append(...content);
  row.append(create("dt", "", term), detail);
  list.append(row);
}

function mark(node, state, text) {
  node.className = "verify-result " + state;
  node.textContent = text;
}

// Before a launch, name the planned day and the one wallet that will create
// the token, so the creator on the coin page can be compared with it later.
async function drawPlan(card) {
  try {
    const plan = JSON.parse(new TextDecoder().decode(await bytes("./token-plan.json")));
    if (card.dataset.state !== "none") return;
    const facts = create("dl", "facts");
    fact(facts, "Planned launch", document.createTextNode(plan.planned + " · " + plan.launchpad));
    fact(facts, "Creator wallet", link(plan.creator_wallet + " ↗", "https://solscan.io/account/" + plan.creator_wallet));
    card.append(facts, create("p", "", "The token will be created by this wallet and no other. On launch day, compare the creator shown on the coin page with this address before anything else."));
  } catch { /* the statement above stands on its own */ }
}

function drawToken(token, build) {
  const card = $("#token-status");
  card.replaceChildren();
  if (token.status !== "launched") {
    card.dataset.state = "none";
    card.append(create("p", "token-tag", "NO TOKEN LAUNCHED"),
      create("p", "token-headline", "Shipwatch has not launched a token."),
      create("p", "", "Any token using this name, logo or website today was not created by us. When one launches, its mint address will appear in this box."));
    drawPlan(card);
    return;
  }
  card.dataset.state = "live";
  card.append(create("p", "token-tag", "OFFICIAL TOKEN · " + String(token.launchpad || "").toUpperCase()));
  const mint = create("p", "token-mint", token.mint);
  const copy = create("button", "", "Copy mint");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(token.mint); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy"; }
  });
  const facts = create("dl", "facts");
  fact(facts, "Symbol", document.createTextNode("$" + token.symbol + " · " + token.name));
  fact(facts, "Created", document.createTextNode(when(token.launched_at)), document.createTextNode(" · "), link("transaction ↗", "https://solscan.io/tx/" + token.creation_signature));
  fact(facts, "Creator wallet", link(token.creator_wallet + " ↗", "https://solscan.io/account/" + token.creator_wallet));
  if (token.dev_allocation) fact(facts, "Team allocation", document.createTextNode(token.dev_allocation));
  if (token.sell_policy) fact(facts, "Sell policy", document.createTextNode(token.sell_policy));
  if (token.fee_recipient) fact(facts, "Creator fees go to", document.createTextNode(token.fee_recipient));
  fact(facts, "Metadata", link(token.metadata_uri + " ↗", token.metadata_uri));
  const actions = create("p", "token-links");
  actions.append(copy, link("Coin page ↗", token.coin_url || "https://pump.fun/coin/" + token.mint),
    link("Solscan ↗", "https://solscan.io/token/" + token.mint),
    link("TOKEN.md ↗", build.project.repository + "/blob/main/TOKEN.md"));
  card.append(mint, facts, actions,
    create("p", "", "This is the only Shipwatch token. Check the full address, not just the first and last characters."));
}

async function checkFiles(build) {
  const body = $("#files");
  const names = Object.keys(build.files);
  let matched = 0;
  let failed = 0;
  await Promise.all(names.map(async name => {
    const row = create("tr");
    const expected = build.files[name];
    const result = create("td", "pending", "checking…");
    const file = create("td");
    file.append(link(name, "./" + name));
    const digest = create("td", "hash", short(expected));
    digest.title = expected;
    row.append(file, digest, result);
    body.append(row);
    try {
      const actual = await sha256(await bytes("./" + name));
      if (actual === expected) {
        matched += 1;
        result.className = "ok";
        result.textContent = "✓ matches";
      } else {
        failed += 1;
        result.className = "bad";
        result.textContent = "✗ " + short(actual);
        result.title = actual;
      }
    } catch (error) {
      failed += 1;
      result.className = "bad";
      result.textContent = "✗ " + error.message;
    }
  }));
  [...body.children].sort((left, right) => left.firstChild.textContent.localeCompare(right.firstChild.textContent)).forEach(row => body.append(row));
  if (failed) mark($("#files-summary"), "bad", `${failed} of ${names.length} files do not match the build manifest. Treat this site as unverified and tell us.`);
  else mark($("#files-summary"), "ok", `All ${matched} files served to your browser match the build manifest.`);
}

async function checkObserver(build) {
  const facts = $("#observer-facts");
  const summary = $("#observer-summary");
  fact(facts, "Published source", link("observer.py.txt ↗", "./observer.py.txt"), document.createTextNode(" · "),
    link("backend/shipwatch.py in the repository ↗", `${build.project.repository}/blob/${build.source.commit || "main"}/backend/shipwatch.py`));
  const expected = build.observer_sha256;
  const expectedNode = create("span", "hash", expected);
  fact(facts, "Hash in this build", expectedNode);
  let published = null;
  try {
    published = await sha256(await bytes("./observer.py.txt"));
  } catch { /* reported below */ }
  let running = null;
  try {
    const response = await fetch(build.project.api + "/health", {cache: "no-store", signal: AbortSignal.timeout(15000)});
    running = (await response.json()).code_sha256 || null;
  } catch { /* reported below */ }
  fact(facts, "Server reports", create("span", "hash", running || "no response from the observer"));
  if (!running) mark(summary, "warn", "The observer did not answer, so its running code could not be compared right now.");
  else if (running === expected && published === expected) mark(summary, "ok", "The observer reports running exactly the published source for this build.");
  else if (published !== expected) mark(summary, "bad", "The published observer file does not match this build's manifest.");
  else mark(summary, "warn", "The observer reports a different source hash than this build. A new release may still be rolling out; check again in a few minutes.");
}

function drawCommands(build) {
  const site = build.project.site.replace(/\/$/, "");
  const repo = build.project.repository.replace("https://github.com/", "");
  const commit = build.source.commit || "main";
  const raw = `https://raw.githubusercontent.com/${repo}/${commit}`;
  $("#commands").textContent = [
    "# 1. Which commit does the site say it was built from?",
    `curl -s ${site}/build.json | grep -m1 '"commit"'`,
    "",
    "# 2. Is a served file identical to the repository at that commit?",
    `curl -s ${site}/app.js | sha256sum`,
    `curl -s ${raw}/site/app.js | sha256sum`,
    "",
    "# 3. Does the observer report the published source?",
    `curl -s ${build.project.api}/health`,
    `curl -s ${raw}/backend/shipwatch.py | sha256sum`,
    "",
    "# 4. Did GitHub Actions build these exact files from that commit?",
    `curl -sO ${site}/SHA256SUMS`,
    `gh attestation verify SHA256SUMS --repo ${repo}`,
  ].join("\n");
}

async function main() {
  let build;
  try {
    build = JSON.parse(new TextDecoder().decode(await bytes("./build.json")));
  } catch {
    $("#build-facts").replaceChildren();
    fact($("#build-facts"), "Build", document.createTextNode("This copy has no build manifest, so it cannot be verified. Use the official site."));
    mark($("#files-summary"), "bad", "No build manifest was found.");
    mark($("#observer-summary"), "bad", "No build manifest was found.");
    $("#token-status").textContent = "Token status is only shown on a verifiable build.";
    return;
  }
  const repository = build.project.repository;
  $("#repo-nav").href = repository;
  $("#verify-doc").href = repository + "/blob/main/VERIFY.md";
  const facts = $("#build-facts");
  facts.replaceChildren();
  fact(facts, "Version", document.createTextNode(build.version));
  if (build.source.commit) fact(facts, "Commit", link(build.source.commit + " ↗", `${repository}/commit/${build.source.commit}`));
  else fact(facts, "Commit", document.createTextNode("not recorded; this is a local preview"));
  fact(facts, "Committed", document.createTextNode(when(build.source.commit_time)));
  fact(facts, "Built", document.createTextNode(when(build.built_at)));
  if (build.source.run_url) fact(facts, "Build log", link("GitHub Actions run ↗", build.source.run_url));
  fact(facts, "Repository", link(repository.replace("https://", "") + " ↗", repository));
  if (build.source.dirty) fact(facts, "Warning", document.createTextNode("Built with uncommitted changes. Official builds never show this line."));
  drawCommands(build);
  try {
    drawToken(JSON.parse(new TextDecoder().decode(await bytes("./token.json"))), build);
  } catch {
    $("#token-status").textContent = "Token status could not be loaded. Treat any address as unverified until this box shows it.";
  }
  await Promise.all([checkFiles(build), checkObserver(build)]);
}

main();
