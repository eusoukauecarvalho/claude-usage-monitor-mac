#!/usr/bin/env python3
"""Last-good usage snapshot, persisted so a restart does not blank the display.

Pure persistence/validation logic (no AppKit) so it stays easily testable,
mirroring settings.py. The snapshot lives next to the settings file in
~/Library/Application Support/claude-usage-monitor/.

Why this exists: the usage endpoint answers a 429 with a Retry-After of up to
half an hour. While that window is open every fetch is skipped, so a process
that starts inside it has nothing to show — the menu bar goes empty and stays
empty until the limit lifts. Keeping the cache only in memory meant any
restart ("Reiniciar", a relaunch by launchd, a crash) threw away the one thing
worth showing at exactly the moment it could not be fetched again.

Schema (last_usage.json):
    saved_at    str   ISO-8601 stamp of when the snapshot was written
    stamp       str   "HH:MM:SS" of the fetch, shown verbatim in the UI
    limits      list  the API's limit dicts (kind / percent / resets_at)
    extra_line  str   the "Créditos extras: N%" line, or null
"""

import json
import os
from datetime import datetime, timezone

SNAPSHOT_DIR = os.path.expanduser(
    "~/Library/Application Support/claude-usage-monitor"
)
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, "last_usage.json")

# Percentages age badly: a session window is 5h, so a snapshot older than this
# would show numbers that are almost certainly wrong. Discard it and show
# nothing rather than something misleading.
SNAPSHOT_MAX_AGE_HOURS = 12

EMPTY_SNAPSHOT = {"limits": [], "stamp": None, "extra_line": None}


def _valid_limits(raw):
    """Keep only limit entries shaped like the API's, with a usable percent."""
    if not isinstance(raw, list):
        return []
    return [
        item for item in raw
        if isinstance(item, dict) and isinstance(item.get("percent"), (int, float))
    ]


def _is_fresh(saved_at, max_age_hours, now=None):
    """True when saved_at parses and is within max_age_hours of now."""
    if not isinstance(saved_at, str):
        return False
    try:
        saved = datetime.fromisoformat(saved_at)
    except ValueError:
        return False
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_hours = (now - saved).total_seconds() / 3600.0
    return 0 <= age_hours <= max_age_hours


def load_snapshot(path=SNAPSHOT_PATH, max_age_hours=SNAPSHOT_MAX_AGE_HOURS, now=None):
    """Read the last saved snapshot; anything missing, invalid or stale is empty.

    Never raises: a broken cache file must not stop the monitor from starting.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return dict(EMPTY_SNAPSHOT)
    if not isinstance(raw, dict):
        return dict(EMPTY_SNAPSHOT)
    if not _is_fresh(raw.get("saved_at"), max_age_hours, now):
        return dict(EMPTY_SNAPSHOT)

    limits = _valid_limits(raw.get("limits"))
    if not limits:
        return dict(EMPTY_SNAPSHOT)

    stamp = raw.get("stamp")
    extra = raw.get("extra_line")
    return {
        "limits": limits,
        "stamp": stamp if isinstance(stamp, str) else None,
        "extra_line": extra if isinstance(extra, str) else None,
    }


def save_snapshot(limits, stamp, extra_line, path=SNAPSHOT_PATH, now=None):
    """Persist the latest good fetch. Returns True on success, False otherwise."""
    now = now or datetime.now(timezone.utc)
    payload = {
        "saved_at": now.isoformat(),
        "stamp": stamp,
        "limits": _valid_limits(limits),
        "extra_line": extra_line,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False
