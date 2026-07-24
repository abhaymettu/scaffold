#!/usr/bin/env python3
"""Scaffold nudge engine.

Every :00 and :30, send a check-up to Telegram: a firm-or-gentle line by time of
day, 1-2 tasks from the curated pool (or your plan), sometimes a side quest, and a
mindfulness quote. Driven by cron. Reads state from ~/.scaffold.
"""

import json
import os
import random
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".scaffold")
CONFIG_PATH = os.path.join(BASE, "config.json")
SCHEDULE_PATH = os.path.join(BASE, "schedule.json")
MINDFUL_PATH = os.path.join(BASE, "mindful.json")
CAUGHT_PATH = os.path.join(BASE, "caught.json")
PAUSE_PATH = os.path.join(BASE, "pause.json")
TASKS_PATH = os.path.join(BASE, "tasks.json")
SIDE_QUEST_CHANCE = 0.18  # fraction of nudges that surface a side quest


def load_json(path, required=True):
    if not os.path.exists(path):
        if required:
            sys.stderr.write(f"missing {path}\n")
            sys.exit(1)
        return None
    with open(path) as f:
        return json.load(f)


def to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def already_fired(slot, now):
    """True if this slot already fired today (dedup for the 10-min cron)."""
    marker = os.path.join(BASE, "nudge.log")
    if not os.path.exists(marker):
        return False
    stamp = f"{now.strftime('%Y-%m-%d')} FIRED {slot}"
    with open(marker) as f:
        return stamp in f.read()


def current_slot(schedule, now):
    """Which cadence slot are we in? Returns 'HH:00' or 'HH:30', or None if past end."""
    cadence = schedule.get("cadence_minutes", 30)
    minute_floor = (now.minute // cadence) * cadence
    slot = f"{now.hour:02d}:{minute_floor:02d}"
    end = schedule.get("active_hours", {}).get("end")
    if end and to_minutes(slot) > to_minutes(end):
        return None
    return slot


def tone_for(schedule, slot):
    slot_min = to_minutes(slot)
    for r in schedule.get("tone_by_hour", []):
        if to_minutes(r["from"]) <= slot_min <= to_minutes(r["to"]):
            return r["tone"]
    return "gentle"


def message_for(schedule, slot):
    """The check-up line for this slot: an override if defined, else the generic
    message for the slot's tone. Returns (message, tone)."""
    for o in schedule.get("overrides", []):
        if o["time"] == slot:
            return o["msg"], o.get("tone", tone_for(schedule, slot))
    tone = tone_for(schedule, slot)
    return schedule.get("generic", {}).get(tone, "Check-in."), tone


def active_caught_theme(now):
    """The theme the user said they're 'caught in', if the bias hasn't expired."""
    data = load_json(CAUGHT_PATH, required=False)
    if not data:
        return None
    try:
        if datetime.fromisoformat(data["until"]) > now:
            return data.get("theme")
    except (KeyError, ValueError):
        pass
    return None


def pick_mindful(now):
    """A mindfulness line, biased toward the active 'caught' theme when set."""
    lib = load_json(MINDFUL_PATH, required=False)
    if not lib or not lib.get("lines"):
        return None
    lines = lib["lines"]
    theme = active_caught_theme(now)
    if theme and random.random() < 0.8:
        pool = [l for l in lines if l.get("theme") == theme] or lines
    else:
        pool = lines
    line = random.choice(pool)
    return f"{line['text']}\n\n— {line['source']}"


def clean_task(task):
    """Make a plan line readable: drop a leading [[Page]] label, unwrap wikilinks."""
    task = task.strip()
    if task.startswith("[["):
        end = task.find("]]")
        if end != -1:
            task = task[end + 2:].strip()
    return task.replace("[[", "").replace("]]", "")


def unchecked_musts(vault_path, now):
    """Open Must-Do lines from today's daily plan, if the plan is readable."""
    plan = os.path.join(vault_path, "Daily Plans", now.strftime("%Y-%m-%d") + ".md")
    out, in_must = [], False
    try:
        with open(plan) as f:
            for line in f:
                s = line.strip()
                if s.startswith("## "):
                    in_must = s.lower().startswith("## must-do")
                    continue
                if in_must and s.startswith("- [ ]"):
                    task = clean_task(s[5:].split("`")[0].strip())
                    out.append(task)
    except (FileNotFoundError, PermissionError, OSError):
        return []
    return out


def load_task_pool(now):
    """Today's curated pool from curate.py, or None if missing/stale."""
    d = load_json(TASKS_PATH, required=False)
    if not d or not d.get("tasks"):
        return None
    try:
        if datetime.fromisoformat(d["generated"]).date() != now.date():
            return None
    except (KeyError, ValueError):
        return None
    return d


def surface_tasks(vault, now):
    """Pick 1-2 tasks and maybe a side quest. Prefers the curated pool, rotates for
    variety, falls back to the plan's open Must-Dos."""
    pool = load_task_pool(now)
    side_line = None
    if pool:
        tasks = [t["text"] for t in pool["tasks"]]
        random.shuffle(tasks)
        sq = pool.get("side_quest")
        if sq and random.random() < SIDE_QUEST_CHANCE:
            side_line = f"\U0001f3b2 Side quest: {sq.get('text', '').strip()}"
    else:
        tasks = unchecked_musts(vault, now) if vault else []
        random.shuffle(tasks)
    return tasks[:2], side_line


def compose(msg, tone, tasks, side_line, quote):
    lead = "" if tone == "gentle" else "⚡ "
    parts = [lead + msg]
    if tasks:
        parts.append("\nOn deck:")
        for t in tasks:
            parts.append("  • " + t)
    if side_line:
        parts.append("\n" + side_line)
    if quote:
        parts.append("\n\U0001f4ad " + quote)
    return "\n".join(parts)


def send_telegram(cfg, text):
    token = cfg.get("telegram_bot_token", "")
    chat = cfg.get("telegram_chat_id", "")
    if not token or token.startswith("PASTE") or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
            return r.status == 200
    except Exception as e:
        sys.stderr.write(f"telegram send failed: {e}\n")
        return False


def send_macos(text):
    body = text.replace('"', "'").split("\n")[0]
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{body}" with title "Scaffold"'], check=False)
        return True
    except Exception:
        return False


def main():
    cfg = load_json(CONFIG_PATH, required=False) or {}
    schedule = load_json(SCHEDULE_PATH)
    vault = cfg.get("vault_path", "")
    now = datetime.now()

    pause = load_json(PAUSE_PATH, required=False)
    if pause:
        try:
            if datetime.fromisoformat(pause["until"]) > now:
                return
        except (KeyError, ValueError):
            pass

    ah = schedule.get("active_hours", {})
    if ah and not (to_minutes(ah["start"]) <= now.hour * 60 + now.minute <= to_minutes(ah["end"])):
        return

    slot = current_slot(schedule, now)
    if not slot or already_fired(slot, now):
        return

    msg, tone = message_for(schedule, slot)
    tasks, side_line = surface_tasks(vault, now)
    quote = pick_mindful(now)
    text = compose(msg, tone, tasks, side_line, quote)

    sent = send_telegram(cfg, text)
    if not sent and cfg.get("fallback_macos_notification", True):
        send_macos(text)

    with open(os.path.join(BASE, "nudge.log"), "a") as f:
        f.write(f"{now.isoformat()} | slot {slot} {tone} | telegram={sent}\n")
        f.write(f"{now.strftime('%Y-%m-%d')} FIRED {slot}\n")


if __name__ == "__main__":
    main()
