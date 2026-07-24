#!/usr/bin/env python3
"""Scaffold Telegram control listener.

Polls Telegram for messages you send the bot and reacts:
  - a feeling / theme word ("anxious", "stuck")  -> biases calm nudges toward it for 3h
  - "themes" / "help"                            -> lists what you can say
  - "clear" / "reset"                            -> drops the bias
  - "quiet" / "pause"                            -> silences nudges for 3h
  - "go" / "resume"                              -> un-silences
  - "status"                                     -> current bias + pause
  - anything else                                -> routed to Claude Code (the bridge),
        which reads your schedule + today's plan, replies, and can edit them.

Runs from a launchd LaunchAgent (macOS) or systemd/cron (Linux) every minute. Tracks
an update offset so nothing is processed twice.
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".scaffold")
CONFIG_PATH = os.path.join(BASE, "config.json")
SCHEDULE_PATH = os.path.join(BASE, "schedule.json")
MINDFUL_PATH = os.path.join(BASE, "mindful.json")
CAUGHT_PATH = os.path.join(BASE, "caught.json")
PAUSE_PATH = os.path.join(BASE, "pause.json")
OFFSET_PATH = os.path.join(BASE, "tg_offset")
LOCK_PATH = os.path.join(BASE, "listen.lock")
THREAD_PATH = os.path.join(BASE, "thread.json")
THREAD_KEEP = 12

CLAUDE_BIN = shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
CLAUDE_TIMEOUT = 120
BIAS_HOURS = 3
PAUSE_HOURS = 3

SYNONYMS = {
    "rumination": ["rumination", "ruminating", "replaying", "past", "dwelling", "loop", "overthinking", "spiraling"],
    "avoidance": ["avoidance", "avoiding", "procrastination", "procrastinating", "stuck", "resisting", "cant start", "can't start"],
    "self-criticism": ["self-criticism", "self criticism", "shame", "criticism", "worthless", "lazy", "undisciplined", "self-hate", "self hate", "hating myself"],
    "overwhelm": ["overwhelm", "overwhelmed", "too much", "swamped", "drowning", "buried"],
    "future-anxiety": ["anxiety", "anxious", "future", "worry", "worrying", "dread", "fear", "scared", "nervous"],
    "comparison": ["comparison", "comparing", "jealous", "envy", "envious", "behind", "not enough"],
    "craving": ["craving", "restless", "restlessness", "urge", "scrolling", "distraction", "distracted", "dopamine", "bored"],
    "control": ["control", "controlling", "letting go", "acceptance", "outcome", "helpless"],
    "presence": ["presence", "present", "grounding", "grounded", "here", "now", "breathe"],
}


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def read_offset():
    try:
        with open(OFFSET_PATH) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def write_offset(v):
    with open(OFFSET_PATH, "w") as f:
        f.write(str(v))


def tg(cfg, method, params):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
        return json.load(r)


def send(cfg, text):
    tg(cfg, "sendMessage", {"chat_id": cfg["telegram_chat_id"], "text": text})


def match_theme(text):
    """Detect a mood-bias command conservatively. Only fires on an explicit
    'caught in X' / 'i feel X', or a short phrase (<= 3 words) that IS the feeling.
    Real sentences and questions fall through to the Claude bridge."""
    t = text.lower().strip().rstrip("?.!,")
    explicit = False
    for prefix in ("i'm caught in ", "im caught in ", "caught in ", "caught ", "feeling ", "i feel ", "i'm ", "im "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            explicit = True
            break
    if not explicit and len(t.split()) > 3:
        return None
    tokens = set(t.split())
    for theme in SYNONYMS:
        if t == theme:
            return theme
    for theme, words in SYNONYMS.items():
        for w in words:
            if (" " in w and w in t) or (" " not in w and w in tokens):
                return theme
    return None


def themes_help():
    lib = load_json(MINDFUL_PATH) or {}
    themes = lib.get("themes", list(SYNONYMS.keys()))
    return (
        "Text me what you're caught in and I'll aim the calm nudges there for 3h.\n\n"
        "Themes: " + ", ".join(themes) + "\n\n"
        "Or just say how you feel (\"anxious\", \"stuck\", \"can't focus\").\n"
        "Also: clear, quiet, go, status. Or ask me anything and I'll answer."
    )


def load_thread():
    data = load_json(THREAD_PATH)
    return data if isinstance(data, list) else []


def append_thread(role, text):
    thread = load_thread()
    thread.append({"role": role, "text": text})
    thread = thread[-THREAD_KEEP:]
    with open(THREAD_PATH, "w") as f:
        json.dump(thread, f)


def handle(cfg, text, now):
    t = text.lower().strip()
    if t in ("themes", "help", "?", "commands"):
        return themes_help()
    if t in ("clear", "reset", "none", "stop bias"):
        if os.path.exists(CAUGHT_PATH):
            os.remove(CAUGHT_PATH)
        return "Bias cleared. Nudges back to the full mix."
    if t in ("quiet", "pause", "shush", "mute"):
        until = now + timedelta(hours=PAUSE_HOURS)
        with open(PAUSE_PATH, "w") as f:
            json.dump({"until": until.isoformat()}, f)
        return f"Quiet until {until.strftime('%-I:%M %p')}. Text 'go' to bring me back sooner."
    if t in ("go", "resume", "start", "unpause", "back"):
        if os.path.exists(PAUSE_PATH):
            os.remove(PAUSE_PATH)
        return "Back on. I'll nudge you at the next :00 or :30."
    if t == "status":
        caught, pause = load_json(CAUGHT_PATH), load_json(PAUSE_PATH)
        bits = []
        if caught:
            bits.append(f"biasing: {caught.get('theme')} until {caught.get('until', '')[11:16]}")
        if pause:
            bits.append(f"paused until {pause.get('until', '')[11:16]}")
        return " | ".join(bits) if bits else "Running normally, no bias, not paused."

    theme = match_theme(text)
    if theme:
        until = now + timedelta(hours=BIAS_HOURS)
        with open(CAUGHT_PATH, "w") as f:
            json.dump({"theme": theme, "until": until.isoformat()}, f)
        return f"Got it. Aiming the calm nudges at '{theme}' until {until.strftime('%-I:%M %p')}. Breathe."
    return None  # freeform -> Claude bridge


def claude_bridge(cfg, text, now):
    """Route a freeform message to Claude Code headless with recent turns for context."""
    vault = cfg.get("vault_path", "")
    plan_path = os.path.join(vault, "Daily Plans", now.strftime("%Y-%m-%d") + ".md")
    convo = "\n".join(f"{t['role']}: {t['text']}" for t in load_thread()) or "(no prior messages)"
    prompt = (
        "You are the user's accountability bot, replying over Telegram. Reply in plain text "
        "only: no markdown, no bullet characters, no em dashes. Keep it to 1 to 4 short "
        "sentences, warm and direct. Assume the user has ADHD and struggles with follow-through, "
        "so be concrete and kind.\n\n"
        "RECENT CONVERSATION (oldest first, so you keep context and never contradict yourself "
        "or re-suggest something just rejected):\n"
        f"{convo}\n\n"
        "You can take real actions by editing these files. cron/launchd re-reads them "
        "automatically, no restart needed:\n"
        f"- Nudge schedule: {SCHEDULE_PATH}. JSON with active_hours (start,end), cadence_minutes, "
        "tone_by_hour (from,to,tone where tone is 'gentle' or 'firm'), overrides (time,tone,msg).\n"
        f"- Today's plan (Obsidian markdown): {plan_path}. Tasks look like "
        "'- [ ] [[Page]] text `#tags`' under '## Must-Do', '## Should-Do', '## Quick Wins'.\n\n"
        "Rules: Use the conversation above. If a task was rejected, do not suggest it again and "
        "remove or replace it in the plan. If the user says they did something, believe them. To "
        "change nudge frequency set cadence_minutes; for quiet windows edit active_hours. Never "
        "edit other files. Never write secrets.\n\n"
        f"New message: <<<{text}>>>\n\n"
        "Make any needed edit, then output ONLY the reply to send back."
    )
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json",
           "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write",
           "--add-dir", BASE]
    if vault:
        cmd += ["--add-dir", vault]
    env = os.environ.copy()
    env["HOME"] = HOME
    env["PATH"] = f"{os.path.dirname(CLAUDE_BIN)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT, env=env)
        reply = (json.loads(r.stdout).get("result") or "").strip()
        return reply or "Done. Text 'status' to check."
    except subprocess.TimeoutExpired:
        return "That took too long to work out. Try again, or keep it simpler."
    except Exception as e:
        sys.stderr.write(f"claude_bridge failed: {e}\n")
        return "Something broke handling that. Try 'themes' or 'status'."


def main():
    cfg = load_json(CONFIG_PATH)
    if not cfg or cfg.get("telegram_bot_token", "").startswith("PASTE"):
        return
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    offset = read_offset()
    try:
        resp = tg(cfg, "getUpdates", {"offset": offset + 1, "timeout": 0})
    except Exception as e:
        sys.stderr.write(f"getUpdates failed: {e}\n")
        return
    if not resp.get("ok"):
        return
    now = datetime.now()
    my_chat = str(cfg.get("telegram_chat_id", ""))
    max_id = offset
    for upd in resp.get("result", []):
        max_id = max(max_id, upd["update_id"])
        msg = upd.get("message") or upd.get("edited_message")
        if not msg or "text" not in msg:
            continue
        if str(msg.get("chat", {}).get("id")) != my_chat:
            continue  # only obey the owner
        try:
            reply = handle(cfg, msg["text"], now)
            if reply is None:
                try:
                    tg(cfg, "sendChatAction", {"chat_id": my_chat, "action": "typing"})
                except Exception:
                    pass
                reply = claude_bridge(cfg, msg["text"], now)
                append_thread("user", msg["text"])
                append_thread("bot", reply)
            send(cfg, reply)
        except Exception as e:
            sys.stderr.write(f"handle failed: {e}\n")
    if max_id != offset:
        write_offset(max_id)


if __name__ == "__main__":
    main()
