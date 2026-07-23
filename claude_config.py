#!/usr/bin/env python3
"""Read/write the Claude Code global settings (~/.claude/settings.json).

Used by the monitor's settings window to change the default `model` and
`effortLevel` for NEW Claude Code sessions (a running session only changes
via /model and /effort inside it). Every other key in the file is preserved
untouched, and nothing is ever written over a corrupt file.
"""

import json
import os

CLAUDE_SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")

# (value, label) choices for the pickers; None means "remove the key".
MODEL_CHOICES = [
    (None, "Padrão (não definir)"),
    ("fable", "Fable"),
    ("fable[1m]", "Fable · 1M contexto"),
    ("opus", "Opus"),
    ("opus[1m]", "Opus · 1M contexto"),
    ("opusplan", "Opus Plan"),
    ("sonnet", "Sonnet"),
    ("sonnet[1m]", "Sonnet · 1M contexto"),
    ("haiku", "Haiku"),
]
EFFORT_CHOICES = [
    (None, "Padrão (não definir)"),
    ("low", "Baixo (low)"),
    ("medium", "Médio (medium)"),
    ("high", "Alto (high)"),
    ("xhigh", "Extra alto (xhigh)"),
]


def read_claude_settings(path=CLAUDE_SETTINGS_PATH):
    """The raw settings dict; {} if the file is missing, None if corrupt.

    None signals "do not write back" — overwriting a file we could not
    parse would destroy the user's Claude Code configuration.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def with_option(raw, key, value):
    """New dict with `key` set to `value`, or removed when value is None."""
    if value is None:
        return {k: v for k, v in raw.items() if k != key}
    return {**raw, key: value}


def write_claude_settings(raw, path=CLAUDE_SETTINGS_PATH):
    """Persist the settings dict. Returns True on success, False otherwise."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


def set_claude_option(key, value, path=CLAUDE_SETTINGS_PATH):
    """Set (or remove, when value is None) one option in settings.json.

    Returns True on success; False if the file is corrupt or unwritable.
    """
    raw = read_claude_settings(path)
    if raw is None:
        return False
    return write_claude_settings(with_option(raw, key, value), path)
