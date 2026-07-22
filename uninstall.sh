#!/bin/bash
# Removes the LaunchAgent and stops the menu bar app.
# The project folder itself is left in place; delete it manually if desired.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.claude.usagemonitor.plist"

LABEL="com.claude.usagemonitor"
UID_NUM="$(id -u)"

if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "==> LaunchAgent removed (autostart disabled)."
else
    echo "==> No LaunchAgent found; nothing to remove."
fi

pkill -f "claude-usage-monitor/monitor.py" 2>/dev/null || true
echo "==> Stopped. Delete this folder to fully remove."
