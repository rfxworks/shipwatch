"""Confirm the public site serves a given commit and matches its own manifest.

    python tools/verify_live.py                    report what is live
    python tools/verify_live.py --commit SHA       fail unless that commit is live
    python tools/verify_live.py --wait 600         keep trying for up to 10 minutes

This is the same check a visitor can make by hand; see VERIFY.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "shipwatch-verify", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def inspect(site: str, api: str) -> dict:
    manifest = get(site + "/build.json")
    build = json.loads(manifest)
    mismatched = []
    for name, expected in build["files"].items():
        if hashlib.sha256(get(f"{site}/{name}")).hexdigest() != expected:
            mismatched.append(name)
    listed = {}
    for line in get(site + "/SHA256SUMS").decode().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    if listed.get("build.json") != hashlib.sha256(manifest).hexdigest():
        mismatched.append("build.json (against SHA256SUMS)")
    try:
        running = json.loads(get(api + "/health")).get("code_sha256")
    except (OSError, ValueError):
        running = None
    return {"commit": build["source"]["commit"], "version": build["version"], "files": len(build["files"]),
            "mismatched": mismatched, "observer_expected": build["observer_sha256"], "observer_running": running}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit")
    parser.add_argument("--site", help="check this origin instead of the one in project.toml")
    parser.add_argument("--wait", type=int, default=0, help="seconds to keep retrying")
    parser.add_argument("--require-observer", action="store_true", help="also fail unless the observer runs this build's source")
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "project.toml").read_text(encoding="utf-8"))["project"]
    site, api = (args.site or project["site"]).rstrip("/"), project["api"].rstrip("/")
    deadline = time.monotonic() + args.wait
    while True:
        problem = None
        try:
            result = inspect(site, api)
            if result["mismatched"]:
                problem = "served files differ from the manifest: " + ", ".join(result["mismatched"][:5])
            elif args.commit and result["commit"] != args.commit:
                problem = f"site still serves commit {str(result['commit'])[:12]}, expected {args.commit[:12]}"
            elif args.require_observer and result["observer_running"] != result["observer_expected"]:
                problem = "observer has not picked up this build's source yet"
        except (OSError, ValueError, KeyError) as exc:
            problem = f"site not readable yet ({type(exc).__name__}: {exc})"
        if problem is None:
            print(json.dumps(result, indent=2))
            return 0
        if time.monotonic() >= deadline:
            print("verify failed: " + problem, file=sys.stderr)
            return 1
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
