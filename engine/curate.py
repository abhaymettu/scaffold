#!/usr/bin/env python3
"""Scaffold task curator.

A few times a day, ask Claude Code to read your real notes (today's plan, today's
captured thoughts, active project/area next-actions, the side-quest pool) and distill
a short, realistic task pool into tasks.json, which nudge.py rotates through. This
keeps the expensive 'smart' work to a few runs a day while nudges stay cheap.

Runs from a LaunchAgent (macOS) / systemd (Linux) so the claude subprocess can reach
the credential store, same as listen.py.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".scaffold")
CONFIG_PATH = os.path.join(BASE, "config.json")
TASKS_PATH = os.path.join(BASE, "tasks.json")
CLAUDE_BIN = shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
CLAUDE_TIMEOUT = 150


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def main():
    cfg = load_json(CONFIG_PATH) or {}
    vault = cfg.get("vault_path", "")
    if not vault:
        return
    now = datetime.now()
    plan = os.path.join(vault, "Daily Plans", now.strftime("%Y-%m-%d") + ".md")
    inbox = os.path.join(vault, "00-Inbox", "Daily", now.strftime("%Y-%m-%d") + ".md")
    sidequests = os.path.join(vault, "03-Resources", "Side-Quests.md")

    prompt = (
        "Curate a short, realistic task pool for the user for right now. Read their real "
        "notes first, using the Read and Glob tools:\n"
        f"- Today's plan: {plan}\n"
        f"- Today's captured thoughts (their own words): {inbox}\n"
        f"- Active work: glob {vault}/01-Projects/*.md and {vault}/02-Areas/*.md and read the "
        "lines under '## High Priority' and '## Next Actions / Current Tasks' headings.\n"
        f"- Side-quest pool: {sidequests}\n\n"
        "Context: the user has ADHD and struggles with follow-through. Favor concrete tasks "
        "that take 15 to 45 minutes and can be started now. Pull from their ACTUAL notes and "
        "captured thoughts, not invented work. Do not pile on more of whatever they already "
        "did a lot of today. Phrase each as a short, doable imperative, realistic for someone "
        "with low activation energy.\n\n"
        "Output ONLY valid JSON, no prose, no code fences:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"text": "<short specific imperative>", "why": "<6-10 words, why it is worth doing now>"}\n'
        "  ],\n"
        '  "side_quest": {"number": <int>, "text": "<one side quest, low-effort, for leftover time>"}\n'
        "}\n"
        "Give 3 to 5 tasks, best first. Pick the side quest at random from the pool. If a file "
        "is missing, just use what you can read."
    )
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json",
           "--permission-mode", "acceptEdits", "--allowedTools", "Read,Glob",
           "--add-dir", BASE, "--add-dir", vault]
    env = os.environ.copy()
    env["HOME"] = HOME
    env["PATH"] = f"{os.path.dirname(CLAUDE_BIN)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT, env=env)
        raw = json.loads(r.stdout).get("result", "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        pool = json.loads(raw)
    except Exception as e:
        sys.stderr.write(f"curate failed: {e}\n")
        return

    pool["generated"] = now.isoformat()
    with open(TASKS_PATH, "w") as f:
        json.dump(pool, f, indent=2)
    sys.stderr.write(f"curated {len(pool.get('tasks', []))} tasks at {now.isoformat()}\n")


if __name__ == "__main__":
    main()
