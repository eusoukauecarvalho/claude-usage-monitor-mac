#!/usr/bin/env python3
"""Persistent user settings for the Claude Usage Monitor.

Pure persistence/merge logic (no AppKit) so it stays easily testable.
Settings live in ~/Library/Application Support/claude-usage-monitor/.

Schema:
    notifications_enabled  bool  master switch for every alert
    sound_enabled          bool  audible cue on/off (off = silenciar)
    levels                 dict  per-alert config keyed by level id:
        enabled            bool  fire this alert or not
        message            str   custom message; "" means "use the default"

Messages may use the placeholders {nome}, {pct} and {reset}, replaced at
alert time with the quota name, current percent and reset countdown.
"""

import json
import os

SETTINGS_DIR = os.path.expanduser(
    "~/Library/Application Support/claude-usage-monitor"
)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

# Ids of every alert level, in escalation order (renewal last).
LEVEL_KEYS = ["alert_70", "alert_80", "alert_90", "alert_95", "alert_100", "renewal"]

# Default (Portuguese) message per level — used whenever the custom one is "".
DEFAULT_MESSAGES = {
    "alert_70": "Você já usou {pct}% da janela. Bom ficar de olho. Renova em {reset}.",
    "alert_80": "Dica: reduza o effort ou troque para um modelo mais leve (ex.: Haiku) para economizar.",
    "alert_90": "Chegando perto do limite. Renova em {reset}.",
    "alert_95": "Quase no limite! Segura as tarefas pesadas. Renova em {reset}.",
    "alert_100": "Uso em {pct}%. Renova em {reset} — pause ou use créditos extras.",
    "renewal": "Sua janela voltou — quota disponível de novo.",
}

DEFAULT_SETTINGS = {
    "notifications_enabled": True,
    "sound_enabled": True,
    "levels": {key: {"enabled": True, "message": ""} for key in LEVEL_KEYS},
}


def _merged_level(raw_levels, key):
    """One validated level entry merged over its default."""
    raw = raw_levels.get(key) if isinstance(raw_levels, dict) else None
    if not isinstance(raw, dict):
        return {"enabled": True, "message": ""}
    enabled = raw.get("enabled")
    message = raw.get("message")
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "message": message if isinstance(message, str) else "",
    }


def load_settings(path=SETTINGS_PATH):
    """Load settings from disk, merging over defaults; invalid data falls back."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        raw = None
    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    notif = raw.get("notifications_enabled")
    sound = raw.get("sound_enabled")
    return {
        "notifications_enabled": notif if isinstance(notif, bool) else True,
        "sound_enabled": sound if isinstance(sound, bool) else True,
        "levels": {key: _merged_level(raw.get("levels"), key) for key in LEVEL_KEYS},
    }


def save_settings(settings, path=SETTINGS_PATH):
    """Persist settings as JSON. Returns True on success, False otherwise."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


def update_setting(settings, key, value):
    """New settings dict with top-level bool `key` set (immutable update)."""
    if key not in ("notifications_enabled", "sound_enabled"):
        return dict(settings)
    return {**settings, key: bool(value)}


def update_level(settings, key, enabled=None, message=None):
    """New settings dict with one level's enabled/message changed (immutable)."""
    if key not in LEVEL_KEYS:
        return dict(settings)
    current = settings["levels"][key]
    updated = {
        "enabled": bool(enabled) if enabled is not None else current["enabled"],
        "message": str(message) if message is not None else current["message"],
    }
    return {**settings, "levels": {**settings["levels"], key: updated}}


def level_message(settings, key):
    """The message template for a level: the custom one, or the default if empty."""
    custom = settings["levels"].get(key, {}).get("message", "")
    return custom.strip() or DEFAULT_MESSAGES[key]


def render_message(template, name, pct, reset):
    """Fill {nome}/{pct}/{reset} placeholders in a message template."""
    return (
        template.replace("{nome}", str(name))
        .replace("{pct}", str(pct))
        .replace("{reset}", str(reset))
    )
