"""Deploy one observer file from the owner's fixed HTTPS publishing origin."""

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ORIGIN = "https://shipwatch.bwltd.kr/"
TARGET = Path("/opt/shipwatch/backend/shipwatch.py")
STATE = Path("/var/lib/shipwatch-deployer")


def read(url, timeout=8):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Shipwatch release deployer)"}), timeout=timeout) as response:
        if response.status != 200 or urlsplit(response.url).netloc != urlsplit(url).netloc:
            raise ValueError("Unexpected release response.")
        raw = response.read(600_001)
    if not raw or len(raw) > 600_000:
        raise ValueError("Release artifact has an invalid size.")
    return raw


def notify(digest):
    subprocess.run(
        ["sudo", "-n", "-u", "reflexfactory", "/usr/bin/python3", "/opt/reflex-factory/worker.py", "submit"],
        input=json.dumps({"id": "shipwatch-rollout-" + digest[:16], "kind": "blocker"}).encode(),
        check=True, capture_output=True, timeout=8,
    )


def install(raw):
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, delete=False) as handle:
        handle.write(raw)
        temporary = handle.name
    os.chmod(temporary, 0o644)
    os.replace(temporary, TARGET)


def healthy(digest):
    deadline = time.monotonic() + 5
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            result = json.loads(read("http://127.0.0.1:8789/health", timeout=min(1, remaining)))
            if result.get("project") == "shipwatch" and result.get("code_sha256") == digest:
                return True
        except (ValueError, OSError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))


def save_status(path, result):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(path)


def main():
    started = time.monotonic()
    STATE.mkdir(mode=0o700, exist_ok=True)
    path = STATE / "status.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    release = json.loads(read(ORIGIN + "release.json"))
    if release.get("project") != "shipwatch":
        return
    digest = release.get("backend_sha256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid observer digest.")
    if previous.get("failed_digest") == digest:
        if previous.get("notice_pending"):
            notify(digest)
            previous["notice_pending"] = False
            save_status(path, previous)
        return
    installed = TARGET.read_bytes()
    current = hashlib.sha256(installed).hexdigest()
    if current == digest and previous.get("active_digest") == digest:
        return
    raw = read(ORIGIN + "observer.py.txt")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Observer artifact did not match the release.")
    ast.parse(raw.decode("utf-8"))
    # Leave room inside the 90-second systemd limit for candidate/rollback
    # restarts (25s each), both health checks (5s each), and the alert (8s).
    # External socket timeouts are inactivity limits, so check elapsed time too.
    if time.monotonic() - started > 15:
        raise TimeoutError("Release verification exceeded the safe rollout time budget.")
    candidate_error = "health_check_failed"
    try:
        install(raw)
        subprocess.run(["systemctl", "restart", "shipwatch.service"], check=True, timeout=25)
        candidate_healthy = healthy(digest)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        candidate_healthy = False
        candidate_error = type(exc).__name__
    if candidate_healthy:
        result = {"active_digest": digest, "release": release["release"], "checked_unix": time.time(), "status": "active"}
    else:
        # Persist the blocker before attempting recovery. A failed recovery or
        # notification must not erase the incident or claim the old code is live.
        result = {"active_digest": None, "failed_digest": digest, "checked_unix": time.time(),
                  "notice_pending": True, "status": "rollback_pending", "candidate_error": candidate_error}
        save_status(path, result)
        rollback_healthy = False
        try:
            install(installed)
            result["installed_digest"] = current
            subprocess.run(["systemctl", "restart", "shipwatch.service"], check=True, timeout=25)
            rollback_healthy = healthy(current)
            if not rollback_healthy:
                result["rollback_error"] = "health_check_failed"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["rollback_error"] = type(exc).__name__
        result.update(active_digest=current if rollback_healthy else None,
                      rollback_verified=rollback_healthy,
                      status="rolled_back" if rollback_healthy else "rollback_failed")
    save_status(path, result)
    if result.get("notice_pending"):
        notify(digest)
        result["notice_pending"] = False
        save_status(path, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
