"""Assemble the deployable site in dist/ and stamp it with its source commit.

    python tools/build.py              full build, renders post media with Playwright
    python tools/build.py --no-media   everything except rendered media (no browser needed)
    python tools/build.py --check      validate inputs only; writes nothing

What is deterministic: every file under site/ is copied byte for byte, and
observer.py.txt is a byte copy of backend/shipwatch.py. A visitor can therefore
compare any served file with the same path in the repository at the commit
named in build.json. Rendered post media and build.json itself vary per run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import posts as post_queue  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SITE_FILES = ["index.html", "verify.html", "style.css", "app.js", "verify.js", "token-plan.json", "geist.woff2",
              "geist-OFL.txt", "favicon.png", "social.png", "demo.mp4"]
ADDRESS = r"[1-9A-HJ-NP-Za-km-z]{32,44}"


def write(path: Path, text: str) -> None:
    """Write text with LF endings on every platform so hashes do not depend on the build machine."""
    path.write_bytes(text.encode("utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def source_identity() -> dict:
    commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain")) if not os.environ.get("GITHUB_SHA") else False
    identity = {
        "commit": commit or None,
        "commit_time": git("show", "-s", "--format=%cI", "HEAD") or None,
        "dirty": dirty,
    }
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        identity["run_url"] = f"{server}/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return identity


def load_inputs() -> dict:
    project = tomllib.loads((ROOT / "project.toml").read_text(encoding="utf-8"))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must look like 1.2.3")
    token = tomllib.loads((ROOT / "token.toml").read_text(encoding="utf-8"))
    status = token.get("status")
    if status not in {"not_launched", "launched"}:
        raise ValueError("token.toml status must be 'not_launched' or 'launched'")
    if status == "launched":
        for key in ("mint", "creator_wallet", "creation_signature", "launchpad", "launched_at", "metadata_uri"):
            if not token.get(key):
                raise ValueError(f"token.toml is marked launched but '{key}' is empty")
        for key in ("mint", "creator_wallet"):
            if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", token[key]):
                raise ValueError(f"token.toml {key} is not a Solana address")
    # TOKEN.md is what people read; it must agree with what the site shows.
    statement = (ROOT / "TOKEN.md").read_text(encoding="utf-8")
    if status == "launched" and token["mint"] not in statement:
        raise ValueError("TOKEN.md does not state the mint address recorded in token.toml")
    if status == "not_launched" and "no token has been launched" not in statement.lower():
        raise ValueError("TOKEN.md must say that no token has been launched while token.toml says so")
    for name in SITE_FILES:
        if not (ROOT / "site" / name).is_file() or not (ROOT / "site" / name).stat().st_size:
            raise ValueError(f"site/{name} is missing or empty")
    # The creator wallet is named before the launch. A launch from any other
    # wallet must not be published as ours.
    plan = json.loads((ROOT / "site" / "token-plan.json").read_text(encoding="utf-8"))
    planned_wallet = plan.get("creator_wallet") or ""
    if not re.fullmatch(ADDRESS, planned_wallet) or not plan.get("planned") or not plan.get("launchpad"):
        raise ValueError("site/token-plan.json needs planned, launchpad and a creator_wallet that is a Solana address")
    if status == "not_launched" and planned_wallet not in statement:
        raise ValueError("TOKEN.md does not name the creator wallet published in site/token-plan.json")
    if status == "launched" and token["creator_wallet"] != planned_wallet:
        raise ValueError("token.toml names a creator wallet other than the one published before launch in site/token-plan.json")
    index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    site = project["project"]["site"].rstrip("/")
    if site not in index:
        raise ValueError("site/index.html must reference the branded site URL in its share tags")
    posts = post_queue.load_all(ROOT / "posts")
    return {"project": project, "version": version, "token": token, "posts": posts}


def build(out: Path, render: bool, include_drafts: bool = False) -> dict:
    inputs = load_inputs()
    project, version = inputs["project"], inputs["version"]
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    observer = ROOT / "backend" / "shipwatch.py"
    shutil.copyfile(observer, out / "observer.py.txt")
    identity = source_identity()
    observer_sha = sha256(observer)
    # The VPS deployer reads exactly these three keys; the rest is for people.
    release = {"project": project["project"]["id"], "release": version, "backend_sha256": observer_sha,
               "commit": identity["commit"]}
    write(out / "release.json", json.dumps(release, indent=2) + "\n")
    token = dict(inputs["token"])
    if isinstance(token.get("launched_at"), (dt.date, dt.datetime)):
        token["launched_at"] = token["launched_at"].isoformat()
    write(out / "token.json", json.dumps(token, indent=2, sort_keys=True) + "\n")
    write(out / ".nojekyll", "")  # GitHub Pages: serve files exactly as built
    built_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def write_manifest() -> dict:
        files = {path.relative_to(out).as_posix(): sha256(path) for path in sorted(out.rglob("*"))
                 if path.is_file() and path.name not in {".nojekyll", "build.json", "SHA256SUMS"}}
        manifest = {
            "schema": 1,
            "project": {"id": project["project"]["id"], "name": project["project"]["name"],
                        "site": project["project"]["site"], "api": project["project"]["api"],
                        "repository": project["project"]["repository"], "x": project["x"]["handle"],
                        "studio_x": project["x"]["studio"]},
            "version": version,
            "source": identity,
            "built_at": built_at,
            "observer_sha256": observer_sha,
            "token_status": token["status"],
            "files": files,
        }
        write(out / "build.json", json.dumps(manifest, indent=2) + "\n")
        return manifest

    # Media is captured from this very build, so the pages being captured
    # (the verify page in particular) need a manifest before rendering starts.
    write_manifest()

    ready = [post for post in inputs["posts"] if include_drafts or not post["draft"]]
    queue_posts = []
    media_root = out / "posts" / "media"
    renderer = None
    if render and any(post["media"] for post in ready):
        import render_media
        renderer = render_media.Renderer(out)
    try:
        for post in ready:
            entry = {"id": post["id"], "date": post["date"], "not_before": post["not_before"],
                     "segments": [{"text": text, "media": []} for text in post["segments"]]}
            for index, item in enumerate(post["media"], 1):
                target_dir = media_root / post["id"]
                target_dir.mkdir(parents=True, exist_ok=True)
                if item["kind"] == "file":
                    source = ROOT / "posts" / item["src"]
                    if not source.is_file():
                        raise ValueError(f"{post['id']}: posts/{item['src']} does not exist")
                    target = target_dir / f"{index}{source.suffix.lower()}"
                    shutil.copyfile(source, target)
                elif renderer is not None:
                    target = renderer.render(post, item, index, target_dir)
                else:
                    continue  # --no-media: leave rendered media out of the queue entirely
                entry["segments"][0]["media"].append({
                    "path": target.relative_to(out).as_posix(), "sha256": sha256(target),
                    "bytes": target.stat().st_size, "type": post_queue.FILE_TYPES[target.suffix.lower()],
                    "alt": item["alt"],
                })
            if post["draft"] or len(entry["segments"][0]["media"]) != len(post["media"]):
                entry["incomplete"] = True  # previews and media-less builds must never publish
            queue_posts.append(entry)
    finally:
        if renderer is not None:
            renderer.close()
    (out / "posts").mkdir(exist_ok=True)
    queue = {"schema": 1, "project": project["project"]["id"], "account": project["x"]["handle"],
             "commit": identity["commit"], "posts": queue_posts}
    write(out / "posts" / "queue.json", json.dumps(queue, indent=2, ensure_ascii=False) + "\n")

    manifest = write_manifest()
    lines = [f"{digest}  {name}" for name, digest in manifest["files"].items()]
    lines.append(f"{sha256(out / 'build.json')}  build.json")
    write(out / "SHA256SUMS", "\n".join(lines) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-media", action="store_true", help="skip Playwright rendering of post media")
    parser.add_argument("--check", action="store_true", help="validate inputs and exit")
    parser.add_argument("--include-drafts", action="store_true", help="render draft posts too, for previewing; they stay unpublishable")
    args = parser.parse_args()
    try:
        if args.check:
            inputs = load_inputs()
            print(f"inputs valid: version {inputs['version']}, {len(inputs['posts'])} post(s), token {inputs['token']['status']}")
            return 0
        manifest = build(args.out.resolve(), render=not args.no_media, include_drafts=args.include_drafts)
    except (ValueError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        print("build failed: " + str(exc), file=sys.stderr)
        return 1
    source = manifest["source"]
    print(f"built {manifest['project']['id']} {manifest['version']} from commit {(source['commit'] or 'unknown')[:12]}"
          f"{' (uncommitted changes)' if source['dirty'] else ''}: {len(manifest['files'])} files, observer {manifest['observer_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
