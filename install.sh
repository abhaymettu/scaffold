#!/usr/bin/env bash
# Scaffold installer (macOS). Copies the engine to ~/.scaffold, installs the cron
# nudge job, and loads the listen + curate LaunchAgents. Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.scaffold"
LA="$HOME/Library/LaunchAgents"

echo "==> Installing Scaffold engine to $DEST"
mkdir -p "$DEST" "$LA"
cp "$REPO/engine/"{nudge.py,listen.py,curate.py,schedule.json,mindful.json} "$DEST/"
chmod +x "$DEST/"*.py

if [ ! -f "$DEST/config.json" ]; then
  cp "$REPO/engine/config.example.json" "$DEST/config.json"
  chmod 600 "$DEST/config.json"
  echo "==> Created $DEST/config.json  (EDIT THIS: add your Telegram token, chat id, and vault path)"
else
  echo "==> Keeping existing $DEST/config.json"
fi

echo "==> Installing cron job for nudges (every 10 minutes)"
NUDGE="*/10 * * * * /usr/bin/python3 $DEST/nudge.py >> $DEST/cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'scaffold/nudge.py' ; echo "$NUDGE" ) | crontab -

echo "==> Rendering + loading LaunchAgents (listen, curate)"
UID_NUM="$(id -u)"
for name in listen curate; do
  PLIST="$LA/com.scaffold.$name.plist"
  sed "s|__HOME__|$HOME|g" "$REPO/launchagents/com.scaffold.$name.plist.template" > "$PLIST"
  launchctl bootout "gui/$UID_NUM/com.scaffold.$name" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$PLIST"
done

cat <<EOF

Done. Next steps:
  1. Edit $DEST/config.json  (Telegram token + chat id + your vault path).
  2. Create a Telegram bot with @BotFather, message it once, then get your chat id
     from https://api.telegram.org/bot<TOKEN>/getUpdates  (the "chat":{"id":...} number).
  3. macOS only: give cron Full Disk Access so it can read an iCloud vault
     (System Settings > Privacy & Security > Full Disk Access > + > Cmd+Shift+G > /usr/sbin/cron).
  4. Text your bot "themes" to confirm it answers, or wait for the next :00/:30 nudge.

Turn it off:  crontab -l | grep -v scaffold/nudge.py | crontab -
              launchctl bootout gui/$UID_NUM/com.scaffold.listen
              launchctl bootout gui/$UID_NUM/com.scaffold.curate
EOF
