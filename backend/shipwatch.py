"""Shipwatch's public API and bounded website observer."""

import concurrent.futures
from contextlib import contextmanager
import datetime as dt
import difflib
import gzip
import hashlib
from html.parser import HTMLParser
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

STATE = Path(os.environ.get("SHIPWATCH_STATE", "/var/lib/shipwatch"))
INTERVAL = 300
MAX_TARGETS = 40
MAX_BYTES = 600_000
MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
SCAN_LOCK = threading.Lock()
CODE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def excerpt_signature(added, removed):
    return "\n".join(sorted(str(value) for value in [*added, *removed] if value))


def repeat_count(con, mint, added, removed):
    signature = excerpt_signature(added, removed)
    if not signature:
        return 0
    count = 0
    for row in con.execute("SELECT added,removed FROM events WHERE mint=? AND kind='page'", (mint,)):
        if excerpt_signature(json.loads(row["added"]), json.loads(row["removed"])) == signature:
            count += 1
    return count


@contextmanager
def database():
    con = sqlite3.connect(STATE / "shipwatch.sqlite3", timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        with con:
            yield con
    finally:
        con.close()


def initialize():
    STATE.mkdir(parents=True, exist_ok=True)
    with database() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
            mint TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
            url TEXT NOT NULL, link_source TEXT NOT NULL, added TEXT NOT NULL,
            checked TEXT, next_check REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', failures INTEGER NOT NULL DEFAULT 0,
            fingerprint TEXT, text TEXT, title TEXT, assets TEXT, error TEXT,
            complete_assets TEXT, release_info TEXT);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, mint TEXT NOT NULL, observed TEXT NOT NULL,
            previous_observation TEXT, kind TEXT NOT NULL, summary TEXT NOT NULL,
            added TEXT NOT NULL DEFAULT '[]', removed TEXT NOT NULL DEFAULT '[]',
            before_hash TEXT, after_hash TEXT, detail_url TEXT,
            repeat_count INTEGER NOT NULL DEFAULT 0,
            association_ambiguous INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS submissions (bucket TEXT NOT NULL, stamp REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS github_cache (repo TEXT PRIMARY KEY, checked REAL NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS github_requests (stamp REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS events_time ON events(id DESC);
        """)
        columns = {row[1] for row in con.execute("PRAGMA table_info(targets)")}
        if "complete_assets" not in columns:
            con.execute("ALTER TABLE targets ADD COLUMN complete_assets TEXT")
            con.execute("UPDATE targets SET complete_assets=assets")
        if "release_info" not in columns:
            con.execute("ALTER TABLE targets ADD COLUMN release_info TEXT")
        event_columns = {row[1] for row in con.execute("PRAGMA table_info(events)")}
        if "detail_url" not in event_columns:
            con.execute("ALTER TABLE events ADD COLUMN detail_url TEXT")
        if "repeat_count" not in event_columns:
            con.execute("ALTER TABLE events ADD COLUMN repeat_count INTEGER NOT NULL DEFAULT 0")
            seen = {}
            for row in list(con.execute("SELECT id,mint,added,removed FROM events WHERE kind='page' ORDER BY id")):
                signature = excerpt_signature(json.loads(row["added"]), json.loads(row["removed"]))
                key = (row["mint"], signature)
                prior = seen.get(key, 0) if signature else 0
                con.execute("UPDATE events SET repeat_count=? WHERE id=?", (prior, row["id"]))
                if signature:
                    seen[key] = prior + 1
        if "association_ambiguous" not in event_columns:
            con.execute("ALTER TABLE events ADD COLUMN association_ambiguous INTEGER NOT NULL DEFAULT 0")
        con.execute("""UPDATE events SET association_ambiguous=1
            WHERE kind='repository' AND mint IN (
                SELECT mint FROM events WHERE kind='repository' AND detail_url IS NOT NULL
                GROUP BY mint HAVING COUNT(DISTINCT detail_url)>1)""")


def public_address(host):
    addresses = {entry[4][0] for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("The website must resolve only to public internet addresses.")
    return sorted(addresses, key=lambda address: ":" in address)[0]


def fetch(url, limit=MAX_BYTES, redirects=3):
    for _ in range(redirects + 1):
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Unsupported website address.")
        expected_port = 443 if parsed.scheme == "https" else 80
        if parsed.port not in (None, expected_port):
            raise ValueError("Only standard website ports are supported.")
        address = public_address(parsed.hostname)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, expected_port, timeout=8)
        # Keep the HTTP hostname and TLS SNI while pinning the validated destination.
        connection._create_connection = lambda endpoint, timeout, source_address=None: socket.create_connection(
            (address, expected_port), timeout, source_address
        )
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection.request("GET", path, headers={"User-Agent": "Shipwatch/1.0 website observer", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Website redirect has no destination.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Website returned HTTP {response.status}.")
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Response exceeds the observation size limit.")
            if response.getheader("Content-Encoding") == "gzip":
                import io
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                    raw = zipped.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError("Expanded response exceeds the observation size limit.")
            return raw, response.getheader("Content-Type", ""), url
        finally:
            connection.close()
    raise ValueError("Website redirected too many times.")


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.title_mode = False
        self.title = []
        self.parts = []
        self.assets = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "title":
            self.title_mode = True
        if tag in {"p", "h1", "h2", "h3", "li", "section", "div", "br"}:
            self.parts.append("\n")
        if tag == "script" and attributes.get("src"):
            self.assets.append(("script", attributes["src"]))
        if tag == "link" and attributes.get("rel") == "stylesheet" and attributes.get("href"):
            self.assets.append(("stylesheet", attributes["href"]))
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.title_mode = False

    def handle_data(self, data):
        if self.title_mode:
            self.title.append(data)
        elif not self.hidden:
            self.parts.append(data)


def github_release(repo):
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        cached = con.execute("SELECT * FROM github_cache WHERE repo=?", (repo,)).fetchone()
        if cached:
            cached_result = json.loads(cached["result"])
            lifetime = 30 if cached_result.get("pending") else 3600
            if cached["checked"] > time.time() - lifetime:
                return cached_result
        con.execute("DELETE FROM github_requests WHERE stamp < ?", (time.time() - 3600,))
        if con.execute("SELECT count(*) FROM github_requests").fetchone()[0] >= 40:
            return {"repo": repo, "error": "Public release lookup budget reached; a later check will retry."}
        con.execute("INSERT INTO github_requests(stamp) VALUES (?)", (time.time(),))
        con.execute("INSERT OR REPLACE INTO github_cache(repo,checked,result) VALUES (?,?,?)",
                    (repo, time.time(), json.dumps({"repo": repo, "pending": True, "error": "Release lookup in progress."})))
    result = {"repo": repo, "checked": now()}
    try:
        raw, _, _ = fetch("https://api.github.com/repos/" + repo + "/releases?per_page=1")
        releases = json.loads(raw)
        if not isinstance(releases, list):
            raise ValueError("Unexpected release response.")
        if releases:
            latest = releases[0]
            result.update({"id": latest["id"], "tag": str(latest["tag_name"])[:100],
                           "url": "https://github.com/" + repo + "/releases/tag/" + quote(str(latest["tag_name"]), safe=""),
                           "published": latest.get("published_at"), "prerelease": bool(latest.get("prerelease"))})
    except (ValueError, OSError, KeyError, http.client.HTTPException) as exc:
        result["error"] = str(exc)[:160]
    with database() as con:
        con.execute("INSERT OR REPLACE INTO github_cache(repo,checked,result) VALUES (?,?,?)", (repo, time.time(), json.dumps(result)))
    return result


def stabilize_text(text):
    """Remove clock-driven relative ages without hiding surrounding content."""
    return re.sub(r"\d+(?:\.\d+)?(?:mo|[smhdwy])(?=old|push)", "<age>", text, flags=re.IGNORECASE)


def linked_repositories(links, base_url):
    repos = []
    reserved = {"sponsors", "features", "settings", "login", "join", "topics", "collections", "marketplace", "explore", "orgs", "users"}
    for href in links:
        parsed = urlsplit(urljoin(base_url, href))
        parts = parsed.path.strip("/").split("/")
        if parsed.hostname not in {"github.com", "www.github.com"} or len(parts) < 2 or parts[0].lower() in reserved:
            continue
        repo = "/".join(parts[:2]).removesuffix(".git")
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo) and repo not in repos:
            repos.append(repo)
    return repos


def observe(url):
    raw, content_type, final_url = fetch(url)
    if "html" not in content_type:
        raise ValueError("The linked website did not return an HTML page.")
    page = Page()
    page.feed(raw.decode("utf-8", errors="replace"))
    title = " ".join("".join(page.title).split())[:180]
    if title.lower() in {"just a moment...", "attention required! | cloudflare", "checking your browser"}:
        raise ValueError("The website requires a browser verification.")
    lines = [" ".join(line.split()) for line in "".join(page.parts).splitlines()]
    text = stabilize_text("\n".join(line for line in lines if line and not re.fullmatch(r"[$€£]?[-+\d,.%\s]+", line)))[:60000]
    assets = []
    candidates = []
    for kind, path in page.assets:
        asset_url = urljoin(final_url, path)
        if urlsplit(asset_url).netloc == urlsplit(final_url).netloc and asset_url not in candidates:
            candidates.append(asset_url)
            assets.append({"kind": kind, "url": asset_url})
        if len(assets) >= 3:
            break
    for asset in assets:
        try:
            body, _, _ = fetch(asset["url"])
            asset["sha256"] = hashlib.sha256(body).hexdigest()
        except (OSError, ValueError, http.client.HTTPException) as exc:
            asset["error"] = str(exc)[:120]
    repos = linked_repositories(page.links, final_url)
    release = github_release(repos[0]) if len(repos) == 1 else None
    if len(repos) > 1:
        release = {"ambiguous": True, "count": len(repos),
                   "error": "Multiple GitHub repository links found; release association is ambiguous."}
    return {"title": title, "text": text, "assets": assets, "release": release,
            "fingerprint": hashlib.sha256((title + "\n" + text).encode()).hexdigest()}


def linked_project(mint):
    raw, _, _ = fetch(f"https://api.dexscreener.com/token-pairs/v1/solana/{quote(mint)}")
    pairs = json.loads(raw)
    if not isinstance(pairs, list):
        raise ValueError("Token lookup did not return a supported response.")
    pairs = [pair for pair in pairs if pair.get("baseToken", {}).get("address") == mint and pair.get("info", {}).get("websites")]
    if not pairs:
        raise ValueError("No project website is linked to this Solana mint in DEX Screener.")
    pairs.sort(key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0), reverse=True)
    pair = pairs[0]
    websites = pair["info"]["websites"]
    url = websites[0].get("url", "")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("The token's linked website is unsupported.")
    token = pair["baseToken"]
    return {"mint": mint, "symbol": str(token.get("symbol", ""))[:30], "name": str(token.get("name", ""))[:100],
            "url": url, "link_source": "https://dexscreener.com/solana/" + pair["pairAddress"]}


def add_target(mint, bucket):
    if not MINT.fullmatch(mint):
        raise ValueError("Paste a full Solana mint address.")
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute("SELECT mint FROM targets WHERE mint=?", (mint,)).fetchone()
        if previous:
            return {"mint": mint, "created": False}
        cutoff = time.time() - 3600
        con.execute("DELETE FROM submissions WHERE stamp < ?", (cutoff,))
        if con.execute("SELECT count(*) FROM submissions WHERE bucket=?", (bucket,)).fetchone()[0] >= 6:
            raise ValueError("This connection has reached its hourly watch limit.")
        if con.execute("SELECT count(*) FROM targets").fetchone()[0] >= MAX_TARGETS:
            raise ValueError("The pilot watchlist is full.")
        con.execute("INSERT INTO submissions(bucket,stamp) VALUES (?,?)", (bucket, time.time()))
    project = linked_project(mint)
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT mint FROM targets WHERE mint=?", (mint,)).fetchone():
            return {"mint": mint, "created": False}
        if con.execute("SELECT count(*) FROM targets").fetchone()[0] >= MAX_TARGETS:
            raise ValueError("The pilot watchlist is full.")
        inserted = con.execute("INSERT OR IGNORE INTO targets(mint,symbol,name,url,link_source,added) VALUES (:mint,:symbol,:name,:url,:link_source,:added)", project | {"added": now()}).rowcount
    return {"mint": mint, "created": bool(inserted)}


def excerpts(before, after):
    old = before.splitlines()
    new = after.splitlines()
    changes = list(difflib.ndiff(old, new))
    return (
        [line[2:][:100] for line in changes if line.startswith("+ ")][:2],
        [line[2:][:100] for line in changes if line.startswith("- ")][:2],
    )


def scan_target(target):
    observed = now()
    try:
        sample = observe(target["url"])
    except (OSError, ValueError, http.client.HTTPException) as exc:
        failures = target["failures"] + 1
        with database() as con:
            con.execute("UPDATE targets SET checked=?,next_check=?,status=?,failures=?,error=? WHERE mint=?",
                        (observed, time.time() + INTERVAL, "unavailable", failures, str(exc)[:180], target["mint"]))
            if failures == 2:
                con.execute("INSERT INTO events(mint,observed,previous_observation,kind,summary) VALUES (?,?,?,?,?)",
                            (target["mint"], observed, target["checked"], "unavailable", "The observer could not load the website on two consecutive checks."))
        return
    events = []
    if not target["fingerprint"]:
        events.append(("baseline", "First website observation recorded.", [], []))
    else:
        if target["failures"] >= 2:
            events.append(("recovered", "Website responded successfully again.", [], []))
        previous_text = stabilize_text(target["text"] or "")
        previous_fingerprint = hashlib.sha256(((target["title"] or "") + "\n" + previous_text).encode()).hexdigest()
        if previous_fingerprint != sample["fingerprint"]:
            added, removed = excerpts((target["title"] or "") + "\n" + previous_text, sample["title"] + "\n" + sample["text"])
            events.append(("page", "Page title or visible text changed.", added, removed))
        old_assets = json.loads(target.get("complete_assets") or target["assets"] or "[]")
        old_hashes = sorted(asset["sha256"] for asset in old_assets if "sha256" in asset)
        new_hashes = sorted(asset["sha256"] for asset in sample["assets"] if "sha256" in asset)
        complete = all("sha256" in asset for asset in old_assets + sample["assets"])
        if complete and old_hashes != new_hashes:
            events.append(("assets", "Linked script or stylesheet bytes changed.", [], []))
    observed = now()
    previous_release = json.loads(target.get("release_info") or "null")
    release = sample.get("release")
    ambiguous_release = bool(release and release.get("ambiguous"))
    if release and release.get("error") and not ambiguous_release and previous_release and previous_release.get("repo") == release.get("repo"):
        release = previous_release | {"error": release["error"]}
    release_events = []
    if target["fingerprint"] and not ambiguous_release:
        old_repo = previous_release.get("repo") if previous_release else None
        new_repo = release.get("repo") if release else None
        if old_repo != new_repo:
            release_events.append(("repository", "The observed GitHub repository link changed.",
                                   [new_repo] if new_repo else [], [old_repo] if old_repo else [],
                                   "https://github.com/" + new_repo if new_repo else None))
        elif release and not release.get("error") and previous_release.get("id") != release.get("id"):
            release_events.append(("release", "The linked repository's public release changed.",
                                   [release.get("tag") or "No public release listed"], [previous_release["tag"]] if previous_release.get("tag") else [],
                                   release.get("url")))
    with database() as con:
        for kind, summary, added, removed in events:
            before_hash = previous_fingerprint if target["fingerprint"] else target["fingerprint"]
            after_hash = sample["fingerprint"]
            repeats = repeat_count(con, target["mint"], added, removed) if kind == "page" else 0
            if kind == "assets":
                before_hash = hashlib.sha256(json.dumps(old_hashes).encode()).hexdigest()
                after_hash = hashlib.sha256(json.dumps(new_hashes).encode()).hexdigest()
            con.execute("INSERT INTO events(mint,observed,previous_observation,kind,summary,added,removed,before_hash,after_hash,repeat_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (target["mint"], observed, target["checked"], kind, summary, json.dumps(added), json.dumps(removed),
                         before_hash, after_hash, repeats))
        for kind, summary, added, removed, detail_url in release_events:
            con.execute("INSERT INTO events(mint,observed,previous_observation,kind,summary,added,removed,detail_url) VALUES (?,?,?,?,?,?,?,?)",
                        (target["mint"], observed, target["checked"], kind, summary, json.dumps(added), json.dumps(removed), detail_url))
        distinct_repositories = con.execute("""SELECT COUNT(DISTINCT detail_url) FROM events
            WHERE mint=? AND kind='repository' AND detail_url IS NOT NULL""", (target["mint"],)).fetchone()[0]
        if distinct_repositories > 1:
            con.execute("UPDATE events SET association_ambiguous=1 WHERE mint=? AND kind='repository'", (target["mint"],))
        complete = all("sha256" in asset for asset in sample["assets"])
        con.execute("UPDATE targets SET checked=?,next_check=?,status='online',failures=0,error=NULL,fingerprint=?,text=?,title=?,assets=?,complete_assets=CASE WHEN ? THEN ? ELSE complete_assets END,release_info=? WHERE mint=?",
                    (observed, time.time() + INTERVAL, sample["fingerprint"], sample["text"], sample["title"], json.dumps(sample["assets"]), int(complete), json.dumps(sample["assets"]), json.dumps(release), target["mint"]))
        con.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)")


def scan():
    if not SCAN_LOCK.acquire(blocking=False):
        return
    try:
        with database() as con:
            due = [dict(row) for row in con.execute("SELECT * FROM targets WHERE next_check <= ? ORDER BY next_check LIMIT 6", (time.time(),))]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(scan_target, due))
    finally:
        SCAN_LOCK.release()


def feed():
    with database() as con:
        targets = [dict(row) for row in con.execute("SELECT mint,symbol,name,url,link_source,added,checked,status,title,error,assets,release_info FROM targets ORDER BY added DESC")]
        events = [dict(row) for row in con.execute("SELECT e.*,t.symbol,t.name,t.url FROM events e JOIN targets t ON t.mint=e.mint ORDER BY e.observed DESC,e.id DESC LIMIT 80")]
        for target in targets:
            latest = con.execute("""SELECT id,observed,kind,summary FROM events
                WHERE mint=? AND kind IN ('page','assets','release')
                AND (kind!='page' OR repeat_count=0)
                ORDER BY observed DESC,id DESC LIMIT 1""", (target["mint"],)).fetchone()
            target["latest_signal"] = dict(latest) if latest else None
    for event in events:
        event["added"] = json.loads(event["added"])
        event["removed"] = json.loads(event["removed"])
        event["association_ambiguous"] = bool(event["association_ambiguous"])
    for target in targets:
        assets = json.loads(target.pop("assets") or "[]")
        target["assets_seen"] = len(assets)
        target["assets_read"] = sum("sha256" in asset for asset in assets)
        target["release_info"] = json.loads(target["release_info"] or "null")
    return {"observed_at": now(), "interval_seconds": INTERVAL, "targets": targets, "events": events}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.reply(200, {})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.reply(200, {"project": "shipwatch", "status": "ok", "code_sha256": CODE_SHA})
        elif path == "/api/feed":
            self.reply(200, feed())
        else:
            self.reply(404, {"error": "Unknown endpoint."})

    def do_POST(self):
        if urlsplit(self.path).path != "/api/watch":
            self.reply(404, {"error": "Unknown endpoint."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1000:
                raise ValueError("Invalid request size.")
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict) or not isinstance(request.get("mint"), str):
                raise ValueError("Paste a full Solana mint address.")
            address = self.headers.get("X-Real-IP") or self.client_address[0]
            bucket = hashlib.sha256((self.server.bucket_secret + address + dt.date.today().isoformat()).encode()).hexdigest()
            result = add_target(request["mint"].strip(), bucket)
            self.reply(200, result)
        except (ValueError, OSError, http.client.HTTPException) as exc:
            self.reply(400, {"error": str(exc)[:200]})


def run():
    initialize()
    def observer():
        while True:
            try:
                scan()
            except Exception:
                import logging
                logging.exception("Website observer failed")
            time.sleep(15)
    threading.Thread(target=observer, daemon=True).start()
    class BoundedServer(ThreadingHTTPServer):
        slots = threading.BoundedSemaphore(12)

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    server = BoundedServer(("127.0.0.1", 8789), Handler)
    server.daemon_threads = True
    server.bucket_secret = secrets.token_hex(32)
    server.serve_forever()


if __name__ == "__main__":
    run()
