#!/bin/bash
# Removes the LaunchAgent and stops the menu bar app.
# The project folder itself is left in place; delete it manually if desired.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.claude.usagemonitor.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "==> LaunchAgent removed."
else
    echo "==> No LaunchAgent found; nothing to remove."
fi

pkill -f "claude-usage-monitor/monitor.py" 2>/dev/null || true
echo "==> Stopped. Delete this folder to fully remove."
