#!/usr/bin/env python3
"""Scaffold change detector (the FDA half, runs on cron).

Walks the vault, and when a markdown note has changed and then settled (you've
stopped editing it), queues its path in pending.json for the extractor to read.
Pure stat/mtime, no Claude. Runs on cron because cron has Full Disk Access to the
iCloud vault; the extractor half runs on a LaunchAgent for Claude/Keychain access.
"""

import fcntl
import json
import os
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".scaffold")
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "detect_state.json")
PENDING_PATH = os.path.join(BASE, "pending.json")
LOCK_PATH = os.path.join(BASE, "detect.lock")

DEBOUNCE_SECONDS = 120
MAX_QUEUE = 40
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "Daily Plans",
             "04-Archives", "Attachments", "Raw"}


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def walk_mtimes(vault):
    out = {}
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith(".md"):
                p = os.path.join(root, fn)
                try:
                    out[p] = os.path.getmtime(p)
                except OSError:
                    pass
    return out


def main():
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return

    cfg = load_json(CONFIG_PATH, default={}) or {}
    vault = cfg.get("vault_path", "")
    if not vault or not os.path.isdir(vault):
        return
    now, now_ts = datetime.now(), time.time()
    mtimes = walk_mtimes(vault)
    if not mtimes:
        sys.stderr.write(f"{now.isoformat()} detect: 0 notes visible (vault read perms?)\n")
        return

    # First run: baseline, don't queue history.
    if not os.path.exists(STATE_PATH):
        with open(STATE_PATH, "w") as f:
            json.dump(mtimes, f)
        sys.stderr.write(f"{now.isoformat()} baselined {len(mtimes)} notes\n")
        return

    state = load_json(STATE_PATH, default={}) or {}
    pending = load_json(PENDING_PATH, default=[]) or []
    pset = set(pending)
    queued = 0
    for path, mt in mtimes.items():
        if mt > state.get(path, 0) and now_ts - mt >= DEBOUNCE_SECONDS:
            if path not in pset:
                pending.append(path)
                pset.add(path)
                queued += 1
            state[path] = mt
    pending = pending[-MAX_QUEUE:]
    with open(PENDING_PATH, "w") as f:
        json.dump(pending, f)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)
    if queued:
        sys.stderr.write(f"{now.isoformat()} queued {queued} changed notes\n")


if __name__ == "__main__":
    main()
