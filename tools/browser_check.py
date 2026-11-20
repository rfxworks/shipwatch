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
        page.locator(".event summary").first.click()
        check(page.locator(".event details[open] .evidence").first.is_visible(), "evidence does not expand")
        page.get_by_role("button", name="Availability", exact=True).click()
        kinds = page.locator(".event").evaluate_all("nodes => nodes.map(node => node.dataset.kind)")
        check(all(kind in ("unavailable", "recovered") for kind in kinds), "availability filter shows other events")
        page.get_by_role("button", name="All checks", exact=True).click()
        # Long enough for the form's length rule, but 0, O, I and l are not base58.
        page.get_by_role("textbox", name="Watch a Solana mint").fill("0OIl" * 8)
        page.get_by_role("button", name="Watch mint").click()
        check("full Solana mint" in page.locator("#form-status").inner_text(), "invalid mint is not refused in the browser")
        page.locator("#build-link").filter(has_text="verify").wait_for(timeout=10_000)

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
