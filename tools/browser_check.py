"""Drive the built site in a real browser and fail on anything a visitor would notice.

    python tools/browser_check.py            checks dist/ served locally
    python tools/browser_check.py --url URL  checks a deployed copy instead

Covers both pages at four widths: no horizontal overflow, no script errors,
the live feed loads, filters and evidence work, invalid mints are refused,
and the verify page confirms every file and the observer hash.
"""
from __future__ import annotations

import argparse
import functools
import http.server
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (1440, 768, 390, 320)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def run(base: str) -> list[str]:
    from playwright.sync_api import sync_playwright
    problems: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        page.goto(base + "/")
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(150)
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"home page overflows sideways at {width}px")
        page.set_viewport_size({"width": 1440, "height": 900})
        check(page.locator(".target").count() >= 1, "watchlist shows no projects")
        # The hero radar draws one dot per watched project, and picking a dot narrows the feed to it.
        check(page.locator("#radar").get_attribute("data-blips") == str(page.locator(".target").count()), "the radar does not show every watched project")
        box = page.locator("#radar").bounding_box()
        check(bool(box) and box["width"] >= 200 and abs(box["width"] - box["height"]) < 2, "the radar is not a visible square")
        check("on the radar" in page.locator("#radar-readout").inner_text(), "the radar readout does not summarise the watchlist")
        check(page.locator(".event").count() >= 1, "feed shows no observations")
        # The page opens short: a few observations and projects, with the rest one press away.
        check(1 <= page.locator(".event:visible").count() <= 6, "the feed does not open with at most six observations")
        if page.locator(".event").count() > 6:
            page.locator("#more-events").click()
            check(page.locator(".event:visible").count() > 6, "asking for more observations shows no more")
        check(page.locator("[data-stat]").evaluate_all("nodes => nodes.length === 4 && nodes.every(node => /^[\\d,]+$/.test(node.textContent))"), "the numbers band is not filled in")
        check(page.locator(".target-name").evaluate_all("nodes => nodes.every(node => node.title === node.textContent)"), "a project's full name is not kept where it is cut")
        check(page.locator(".event").count() == page.locator(".event time[datetime]").count(), "an observation has no exact time")
        shares = page.locator(".event .event-links a.share-x")
        check(shares.count() == page.locator(".event").count(), "an observation has no Share on X link")
        check(shares.evaluate_all("nodes => nodes.every(node => node.href.startsWith('https://x.com/intent/post?') && node.target === '_blank' && node.rel === 'noopener noreferrer')"), "a Share on X link does not open X in a new tab")
        # Each observation can be copied as one plain line of text.
        check(page.locator(".event .event-links button.copy-citation").count() == page.locator(".event").count(), "an observation has no Copy as text button")
        page.locator(".event .copy-citation").first.click()
        page.wait_for_timeout(300)
        check(page.locator(".event .copy-citation").first.inner_text() in ("Copied", "Copy not allowed here"), "Copy as text does not say whether it copied")
        page.locator(".event summary").first.click()
        check(page.locator(".event details[open] .evidence").first.is_visible(), "evidence does not expand")
        counted = "document.querySelectorAll('.event').length === Number(document.querySelector('[data-filter=%s] .filter-count').textContent)"
        check(page.locator("[data-filter] .filter-count").evaluate_all("nodes => nodes.length === 5 && nodes.every(node => /^\\d+$/.test(node.textContent))"), "a feed filter shows no count")
        check(page.evaluate(counted % "all"), "the All checks count does not match the feed")
        page.get_by_role("button", name="Availability", exact=True).click()
        check(page.evaluate(counted % "availability"), "the Availability count does not match the feed")
        kinds = page.locator(".event").evaluate_all("nodes => nodes.map(node => node.dataset.kind)")
        check(all(kind in ("unavailable", "recovered") for kind in kinds), "availability filter shows other events")
        page.get_by_role("button", name="All checks", exact=True).click()
        check(page.locator(".target").count() == page.locator(".target > .target-signal, .target > .target-unchanged").count(), "a watched project shows neither its latest change nor that none was seen")
        check(page.locator(".target").evaluate_all("rows => rows.every(row => !row.querySelector('.target-status.unavailable') || (row.querySelector(':scope > .target-reason') || {}).textContent)"), "a project that could not be checked does not say why in plain words")
        check(page.locator(".target").count() == page.locator(".target > .target-links a[href^='https://solscan.io/token/']").count(), "a watched project has no Solscan link")
        check(page.locator(".target-links a").evaluate_all("nodes => nodes.length > 0 && nodes.every(node => (node.href.startsWith('https://solscan.io/token/') || node.href.startsWith('https://dexscreener.com/')) && node.target === '_blank' && node.rel === 'noopener noreferrer')"), "a project link does not open DEX Screener or Solscan in a new tab")
        # Finding a project on the watchlist.
        search = page.get_by_role("searchbox", name="Find a project on the watchlist")
        total = page.locator(".target:visible").count()
        if page.locator(".target").count() > total:
            page.locator("#more-targets").click()
            check(page.locator(".target:visible").count() == page.locator(".target").count(), "asking for every project does not list them all")
            page.locator("#more-targets").click()
        search.fill(page.locator(".target").first.evaluate("node => node.dataset.search.split('\\n')[0]"))
        check(page.locator(".target").first.is_visible(), "searching a project's symbol hides that project")
        check(page.locator("#search-empty").is_hidden(), "searching a listed symbol says no project matches")
        search.fill("no such project ~~~~~~~~")
        check(page.locator(".target:visible").count() == 0, "a search with no match still lists projects")
        check(page.locator("#search-empty").is_visible(), "a search with no match does not say so")
        search.fill("")
        check(page.locator(".target:visible").count() == total, "clearing the search does not list every project again")
        # Observations newer than the last visit carry a NEW mark, and only for that one visit.
        check(page.locator(".event-new").count() == 0, "observations are marked new on a first visit")
        newest = page.evaluate("localStorage.getItem('shipwatch-last-seen')")
        check(bool(newest) and newest.isdigit(), "the newest observation seen is not remembered")
        page.evaluate("localStorage.setItem('shipwatch-last-seen', '0')")
        page.reload()
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        check(page.locator(".event").count() == page.locator(".event .event-new").count(), "observations since the last visit are not marked new")
        seen = page.locator(".event").evaluate_all("nodes => Math.max(...nodes.map(node => Number(node.id.slice(6))))")
        page.reload()
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        check(page.locator(".event").evaluate_all("nodes => nodes.every(node => !node.querySelector('.event-new') || Number(node.id.slice(6)) > %d)" % seen), "NEW marks do not clear on the next visit")
        # Sorting the watchlist three ways, kept across a reload.
        sort = page.locator("#watchlist-sort")
        check(sort.locator("button[data-sort]").count() == 3, "the watchlist has no sort control")
        check(sort.locator("button[data-sort=latest]").get_attribute("aria-pressed") == "true", "the watchlist does not open sorted by latest change")
        sort.get_by_role("button", name="A-Z", exact=True).click()
        check(sort.locator("button[data-sort=az]").get_attribute("aria-pressed") == "true", "A-Z does not show as chosen")
        check(page.locator(".target .target-symbol").evaluate_all("nodes => nodes.every((node, i) => !i || nodes[i - 1].textContent.localeCompare(node.textContent, undefined, {sensitivity: 'base'}) <= 0)"), "A-Z does not order the watchlist by symbol")
        sort.get_by_role("button", name="Offline first", exact=True).click()
        offline = page.locator(".target").evaluate_all("rows => rows.map(row => Boolean(row.querySelector('.target-status.unavailable')))")
        check(offline == sorted(offline, reverse=True), "Offline first does not list unavailable projects first")
        page.reload()
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        check(page.locator("#watchlist-sort button[data-sort=offline]").get_attribute("aria-pressed") == "true", "the watchlist order is not kept after a reload")
        page.locator("#watchlist-sort button[data-sort=latest]").click()
        check(page.locator("#watchlist-sort button[data-sort=latest]").get_attribute("aria-pressed") == "true", "Latest change does not show as chosen")
        # The chosen feed filter is kept across a reload and shows as pressed.
        page.get_by_role("button", name="Availability", exact=True).click()
        page.reload()
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        check(page.locator("[data-filter=availability]").get_attribute("aria-pressed") == "true", "the feed filter is not kept after a reload")
        check(page.locator("[data-filter=all]").get_attribute("aria-pressed") == "false", "All checks shows as pressed beside a kept filter")
        check(all(kind in ("unavailable", "recovered") for kind in page.locator(".event").evaluate_all("nodes => nodes.map(node => node.dataset.kind)")), "the kept filter does not narrow the feed after a reload")
        page.get_by_role("button", name="All checks", exact=True).click()
        check(page.evaluate("localStorage.getItem('shipwatch-filter')") == "all", "choosing All checks is not remembered")
        # Narrowing the feed to one project, from the watchlist and from a project link.
        page.locator(".target-only").first.click()
        check(page.locator("#project-filter").is_visible(), "the feed does not say which project it is narrowed to")
        symbols = set(page.locator(".event h3 strong").all_inner_texts())
        check(len(symbols) <= 1, "the feed narrowed to one project shows others: " + ", ".join(sorted(symbols)))
        page.get_by_role("button", name="Show every project", exact=True).click()
        check(page.locator("#project-filter").is_hidden(), "the feed stays narrowed after asking for every project")
        mint = page.locator(".target").nth(1).get_attribute("id").replace("watch-", "")
        page.goto(base + "/#watch-" + mint)
        page.locator("#project-filter:not([hidden])").wait_for(timeout=30_000)
        check(len(set(page.locator(".event h3 strong").all_inner_texts())) <= 1, "a project link does not narrow the feed")
        page.goto(base + "/")
        page.get_by_text("Feed checked").wait_for(timeout=30_000)
        # Long enough for the form's length rule, but 0, O, I and l are not base58.
        page.get_by_role("textbox", name="Watch a Solana mint").fill("0OIl" * 8)
        page.get_by_role("button", name="Watch mint").click()
        check("full Solana mint" in page.locator("#form-status").inner_text(), "invalid mint is not refused in the browser")
        page.locator("#build-link").filter(has_text="verify").wait_for(timeout=10_000)
        page.locator("#token-panel[data-state=planned], #token-panel[data-state=live], #token-panel[data-state=unknown]").wait_for(timeout=20_000)
        check(page.locator("#token-panel").get_attribute("data-state") in ("planned", "live"), "home page token panel did not render")
        # The public data is documented on its own page, linked from the footer.
        check(page.locator("footer a[href='./api.html']").count() == 1, "the home page footer does not link the data API page")
        page.goto(base + "/api.html")
        check(page.locator("pre.commands").count() == 4, "the data API page does not show a command for each address")
        # The header stays on screen while scrolling, so it needs its backing on every page.
        page.evaluate("window.scrollTo(0, 600)")
        page.locator(".header.scrolled").wait_for(timeout=5_000)
        page.evaluate("window.scrollTo(0, 0)")
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(150)
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"data API page overflows sideways at {width}px")
        page.set_viewport_size({"width": 1440, "height": 900})

        page.goto(base + "/about.html")
        check("Reflex Works" in page.locator("h1").inner_text() or page.locator("h1").count() == 1, "the about page has no heading")
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(150)
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"about page overflows sideways at {width}px")
        page.set_viewport_size({"width": 1440, "height": 900})

        page.goto(base + "/verify.html")
        page.locator("#files-summary.ok, #files-summary.bad").wait_for(timeout=30_000)
        check("ok" in page.locator("#files-summary").get_attribute("class"), "verify page: " + page.locator("#files-summary").inner_text())
        page.locator("#observer-summary.ok, #observer-summary.warn, #observer-summary.bad").wait_for(timeout=30_000)
        check("bad" not in page.locator("#observer-summary").get_attribute("class"), "verify page: " + page.locator("#observer-summary").inner_text())
        check(page.locator("#token-status").get_attribute("data-state") in ("none", "live"), "token status did not render")
        check("gh attestation verify" in page.locator("#commands").inner_text(), "verification commands did not render")
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(150)
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"verify page overflows sideways at {width}px")
        check(not errors, "script errors: " + " | ".join(errors[:3]))
        browser.close()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="check this deployed origin instead of dist/")
    parser.add_argument("--dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    server = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        if not (args.dir / "build.json").is_file():
            print("nothing to check: run tools/build.py first", file=sys.stderr)
            return 1
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(args.dir)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
    try:
        problems = run(base)
    finally:
        if server:
            server.shutdown()
    for problem in problems:
        print("FAIL: " + problem, file=sys.stderr)
    if problems:
        return 1
    print(f"browser checks passed on every page at {', '.join(map(str, WIDTHS))} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
