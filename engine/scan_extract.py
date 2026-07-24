#!/usr/bin/env python3
"""Scaffold task extractor (the Claude half, runs on a LaunchAgent).

Reads pending.json (paths queued by scan_detect.py), asks Claude to aggressively
pull any tasks those notes imply, and merges new ones into candidates.json, which
the curator folds into the task pool. No Telegram ping, no plan edit.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".scaffold")
CONFIG_PATH = os.path.join(BASE, "config.json")
PENDING_PATH = os.path.join(BASE, "pending.json")
CAND_PATH = os.path.join(BASE, "candidates.json")
LOCK_PATH = os.path.join(BASE, "extract.lock")
CLAUDE_BIN = shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
CLAUDE_TIMEOUT = 150

MAX_FILES_PER_RUN = 8
CAND_KEEP = 40
CAND_TTL_DAYS = 7


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def extract(vault, paths):
    listing = "\n".join(f"- {p}" for p in paths)
    prompt = (
        "Read these recently-changed notes and aggressively extract any tasks they imply. "
        "Include latent and implied tasks, not just explicit ones, but skip pure feelings or "
        "musings with no possible action. Phrase each as a short, doable imperative.\n\n"
        f"Files:\n{listing}\n\n"
        "Output ONLY valid JSON, no prose, no code fences:\n"
        '{"candidates": [{"text": "<imperative task>", "source": "<file name>"}]}\n'
        'If nothing is actionable, output {"candidates": []}.'
    )
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json",
           "--permission-mode", "acceptEdits", "--allowedTools", "Read,Glob",
           "--add-dir", BASE, "--add-dir", vault]
    env = os.environ.copy()
    env["HOME"] = HOME
    env["PATH"] = f"{os.path.dirname(CLAUDE_BIN)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                       timeout=CLAUDE_TIMEOUT, env=env)
    raw = json.loads(r.stdout).get("result", "").strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw).get("candidates", [])


def merge_candidates(new, now):
    existing = load_json(CAND_PATH, default=[]) or []
    cutoff = now - timedelta(days=CAND_TTL_DAYS)
    kept = []
    for c in existing:
        try:
            if datetime.fromisoformat(c["found"]) >= cutoff:
                kept.append(c)
        except (KeyError, ValueError):
            pass
    seen = {c["text"].strip().lower() for c in kept}
    added = 0
    for c in new:
        text = (c.get("text") or "").strip()
        if not text or text.lower() in seen:
            continue
        kept.append({"text": text, "source": c.get("source", ""), "found": now.isoformat()})
        seen.add(text.lower())
        added += 1
    with open(CAND_PATH, "w") as f:
        json.dump(kept[-CAND_KEEP:], f, indent=2)
    return added


def main():
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return

    cfg = load_json(CONFIG_PATH, default={}) or {}
    vault = cfg.get("vault_path", "")
    pending = load_json(PENDING_PATH, default=[]) or []
    if not vault or not pending:
        return

    now = datetime.now()
    batch, rest = pending[:MAX_FILES_PER_RUN], pending[MAX_FILES_PER_RUN:]
    try:
        cands = extract(vault, batch)
    except Exception as e:
        sys.stderr.write(f"extract failed (leaving queue intact): {e}\n")
        return

    added = merge_candidates(cands, now)
    with open(PENDING_PATH, "w") as f:  # drop the processed batch, keep the rest
        json.dump(rest, f)
    sys.stderr.write(f"{now.isoformat()} extracted from {len(batch)} notes, +{added} candidates\n")


if __name__ == "__main__":
    main()
