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
        check(page.locator(".event").count() >= 1, "feed shows no observations")
        check(page.locator(".event").count() == page.locator(".event time[datetime]").count(), "an observation has no exact time")
        shares = page.locator(".event .event-links a.share-x")
        check(shares.count() == page.locator(".event").count(), "an observation has no Share on X link")
        check(shares.evaluate_all("nodes => nodes.every(node => node.href.startsWith('https://x.com/intent/post?') && node.target === '_blank' && node.rel === 'noopener noreferrer')"), "a Share on X link does not open X in a new tab")
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
    print(f"browser checks passed on both pages at {', '.join(map(str, WIDTHS))} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
