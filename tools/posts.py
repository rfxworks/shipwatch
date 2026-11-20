"""Parse and validate the announcement queue in posts/.

Each post is a Markdown file with TOML front matter:

    +++
    id = "2026-11-21-verifiable-builds"
    date = 2026-11-21

    [[media]]
    kind = "card"
    headline = "Verify what we ship."
    alt = "Shipwatch card reading: Verify what we ship."
    +++
    First post of the thread.

    ---

    Optional second post of the thread.

Committing a valid file to main is what publishes it. This module only uses
the standard library so the checks run anywhere Python 3.11+ exists.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
import tomllib

MAX_WEIGHTED = 280
MAX_SEGMENTS = 6
MAX_ALT = 1000
URL = re.compile(r"https?://[^\s]+")
POST_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
MEDIA_KINDS = {"card", "screenshot", "video", "file"}
FILE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
              ".gif": "image/gif", ".mp4": "video/mp4"}

# Posts are public statements about a product that has a token attached.
# These patterns catch promises about price or returns before they ship.
BANNED = [
    (re.compile(r"\b\d+\s*x\b", re.I), "return multiples such as '10x'"),
    (re.compile(r"\b(guarantee[ds]?|risk[- ]free|can't lose|cannot lose|sure thing)\b", re.I), "guarantees"),
    (re.compile(r"\b(to the moon|moon(ing|shot)?|pump(ing)? (it|hard)|send it)\b", re.I), "price hype"),
    (re.compile(r"\b(financial advice|get rich|passive income|profit[s]? guaranteed)\b", re.I), "earnings claims"),
    (re.compile(r"\b(buy now|don't miss out|last chance|fomo)\b", re.I), "urgency to buy"),
    (re.compile(r"chatgpt\.site|github\.io", re.I), "a provider hostname instead of the branded URL"),
]


class PostError(ValueError):
    pass


def weighted_length(text: str) -> int:
    """Approximate X's weighted character count (conservative for emoji)."""
    total = 0
    position = 0
    for match in URL.finditer(text):
        total += _weigh(text[position:match.start()]) + 23
        position = match.end()
    return total + _weigh(text[position:])


def _weigh(text: str) -> int:
    light = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    return sum(1 if any(low <= ord(char) <= high for low, high in light) else 2 for char in text)


def parse(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("+++\n"):
        raise PostError(f"{path.name}: must begin with a '+++' TOML front matter block")
    try:
        header, body = raw[4:].split("\n+++\n", 1)
    except ValueError:
        raise PostError(f"{path.name}: front matter is not closed with '+++'") from None
    try:
        meta = tomllib.loads(header)
    except tomllib.TOMLDecodeError as exc:
        raise PostError(f"{path.name}: front matter is not valid TOML ({exc})") from None
    segments = [part.strip() for part in re.split(r"^\s*---\s*$", body, flags=re.M)]
    return validate(path.name, meta, [part for part in segments if part])


def validate(name: str, meta: dict, segments: list[str]) -> dict:
    def fail(message):
        raise PostError(f"{name}: {message}")

    unknown = set(meta) - {"id", "date", "not_before", "media", "draft"}
    if unknown:
        fail(f"unknown front matter keys {sorted(unknown)}")
    post_id = meta.get("id")
    if not isinstance(post_id, str) or not POST_ID.fullmatch(post_id):
        fail("id must be 3-64 lowercase letters, digits or dashes")
    if name != post_id + ".md":
        fail(f"file must be named {post_id}.md so ids stay unique")
    date = meta.get("date")
    if not isinstance(date, dt.date) or isinstance(date, dt.datetime):
        fail("date must be a TOML date such as 2026-11-21")
    not_before = meta.get("not_before")
    if not_before is not None:
        if not isinstance(not_before, dt.datetime) or not_before.tzinfo is None:
            fail("not_before must be a TOML datetime with an offset, e.g. 2026-11-21T15:00:00Z")
        not_before = not_before.astimezone(dt.timezone.utc).isoformat()
    if not 1 <= len(segments) <= MAX_SEGMENTS:
        fail(f"needs 1-{MAX_SEGMENTS} text segments separated by '---' lines")
    for index, text in enumerate(segments, 1):
        length = weighted_length(text)
        if length > MAX_WEIGHTED:
            fail(f"segment {index} is {length} weighted characters; the limit is {MAX_WEIGHTED}")
        for pattern, label in BANNED:
            if pattern.search(text):
                fail(f"segment {index} contains {label}; describe what shipped instead")
    media = meta.get("media", [])
    if not isinstance(media, list) or len(media) > 4:
        fail("media must be a list of at most 4 items (they attach to the first segment)")
    cleaned = []
    for index, item in enumerate(media, 1):
        if not isinstance(item, dict) or item.get("kind") not in MEDIA_KINDS:
            fail(f"media {index} needs kind = one of {sorted(MEDIA_KINDS)}")
        alt = item.get("alt")
        if not isinstance(alt, str) or not 10 <= len(alt) <= MAX_ALT:
            fail(f"media {index} needs alt text of 10-{MAX_ALT} characters describing the image")
        kind = item["kind"]
        allowed = {"kind", "alt"} | {
            "card": {"headline", "kicker", "capture", "viewport", "selector", "wait_for"},
            "screenshot": {"path", "viewport", "selector", "wait_for"},
            "video": {"script", "viewport"},
            "file": {"src"},
        }[kind]
        if set(item) - allowed:
            fail(f"media {index} ({kind}) has unknown keys {sorted(set(item) - allowed)}")
        if kind == "card" and not isinstance(item.get("headline"), str):
            fail(f"media {index} (card) needs a headline")
        if kind == "screenshot" and not str(item.get("path", "")).startswith("/"):
            fail(f"media {index} (screenshot) needs a site path beginning with '/'")
        if kind == "video" and not re.fullmatch(r"[a-z0-9-]{1,40}", str(item.get("script", ""))):
            fail(f"media {index} (video) needs script = the name of a file in tools/video/")
        if kind == "file":
            src = str(item.get("src", ""))
            if not re.fullmatch(r"media/[A-Za-z0-9._/-]{1,120}", src) or ".." in src or Path(src).suffix.lower() not in FILE_TYPES:
                fail(f"media {index} (file) needs src under posts/media/ ending in {sorted(FILE_TYPES)}")
        if item.get("viewport", "desktop") not in {"desktop", "phone"}:
            fail(f"media {index} viewport must be 'desktop' or 'phone'")
        cleaned.append(item)
    kinds = [item["kind"] for item in cleaned]
    if "video" in kinds and len(kinds) > 1:
        fail("a post can carry one video or up to four images, not both")
    return {"id": post_id, "date": date.isoformat(), "not_before": not_before, "draft": bool(meta.get("draft", False)),
            "segments": segments, "media": cleaned}


def load_all(directory: Path) -> list[dict]:
    posts = []
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        posts.append(parse(path))
    seen = set()
    for post in posts:
        if post["id"] in seen:
            raise PostError(f"duplicate post id {post['id']}")
        seen.add(post["id"])
    return posts


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[1]
    try:
        loaded = load_all(root / "posts")
    except PostError as exc:
        print("post check failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
    for post in loaded:
        state = "draft" if post["draft"] else "ready"
        lengths = ", ".join(str(weighted_length(text)) for text in post["segments"])
        print(f"{post['id']:<44} {state:<6} segments[{lengths}] media[{', '.join(m['kind'] for m in post['media']) or '-'}]")
    print(f"{len(loaded)} post(s) valid.")
