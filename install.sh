#!/bin/bash
# Installs Claude Usage Monitor: sets up the venv, installs dependencies,
# and registers a LaunchAgent so the menu bar app starts at login.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.claude.usagemonitor.plist"
LABEL="com.claude.usagemonitor"

echo "==> Creating virtual environment"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

echo "==> Generating LaunchAgent at $PLIST"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${DIR}/.venv/bin/python</string>
        <string>${DIR}/monitor.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-usage-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-usage-monitor.log</string>
</dict>
</plist>
PLISTEOF

echo "==> Loading LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "==> Done. Look for the icon in your menu bar (top-right)."
echo "    To stop:  launchctl unload \"$PLIST\""
