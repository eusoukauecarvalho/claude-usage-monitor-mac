#!/usr/bin/env python3
"""Claude Usage Monitor — macOS menu bar app.

Shows your Claude session/weekly usage in the menu bar in real time,
reading the same data source the `/usage` command uses.
Token is read from the macOS Keychain on every refresh, so it stays
in sync when Claude Code rotates it.
"""

import json
import ssl
import subprocess
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import certifi
import rumps

# SSL context using certifi's CA bundle (framework Python lacks system certs).
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# --- Constants ---------------------------------------------------------------
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
REFRESH_SECONDS = 60
ANTHROPIC_BETA = "oauth-2025-04-20"
USER_AGENT = "claude-usage-monitor/1.0"
HTTP_TIMEOUT = 15

# Percentage thresholds for the colored indicator.
WARN_THRESHOLD = 70
CRIT_THRESHOLD = 90

DOT_OK = "🟢"
DOT_WARN = "🟡"
DOT_CRIT = "🔴"
DOT_ERR = "⚪️"


# --- Data access -------------------------------------------------------------
def read_access_token():
    """Read the OAuth access token from the macOS Keychain.

    Returns the token string, or None if unavailable.
    """
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if not raw:
            return None
        return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        return None


def fetch_usage(token):
    """Fetch usage JSON from the Anthropic OAuth usage endpoint."""
    req = urlrequest.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": ANTHROPIC_BETA,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlrequest.urlopen(req, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- Formatting helpers ------------------------------------------------------
def format_reset(resets_at):
    """Return a human countdown like '2h 14m' until the reset time."""
    if not resets_at:
        return "?"
    try:
        reset = datetime.fromisoformat(resets_at)
    except ValueError:
        return "?"
    delta = reset - datetime.now(timezone.utc)
    total = int(delta.total_seconds())
    if total <= 0:
        return "reseta agora"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def limit_label(item):
    """Build a readable label for one limit entry."""
    kind = item.get("kind", "")
    scope = item.get("scope") or {}
    model = (scope.get("model") or {}).get("display_name")

    names = {
        "session": "Sessão (5h)",
        "weekly_all": "Semanal (tudo)",
        "weekly_scoped": f"Semanal {model}" if model else "Semanal (modelo)",
    }
    name = names.get(kind, model or kind or "Limite")
    pct = item.get("percent", 0)
    reset = format_reset(item.get("resets_at"))
    return f"{name}: {pct}%  ·  reseta em {reset}"


def worst_dot(limits):
    """Pick a colored indicator based on the highest active percentage."""
    if not limits:
        return DOT_OK
    top = max((l.get("percent", 0) or 0) for l in limits)
    if top >= CRIT_THRESHOLD:
        return DOT_CRIT
    if top >= WARN_THRESHOLD:
        return DOT_WARN
    return DOT_OK


def compact_title(limits):
    """Short menu-bar title, e.g. '🟢 S44% W42%'."""
    by_kind = {l.get("kind"): l.get("percent", 0) for l in limits}
    session = by_kind.get("session")
    weekly = by_kind.get("weekly_all")
    parts = []
    if session is not None:
        parts.append(f"S{session}%")
    if weekly is not None:
        parts.append(f"W{weekly}%")
    return f"{worst_dot(limits)} " + " ".join(parts) if parts else f"{worst_dot(limits)} Claude"


# --- App ---------------------------------------------------------------------
class UsageMonitor(rumps.App):
    def __init__(self):
        super().__init__("⏳ Claude", quit_button=None)
        self.menu = ["Atualizar agora", None, "Sair"]
        self.timer = rumps.Timer(self.refresh, REFRESH_SECONDS)
        self.timer.start()
        self.refresh(None)

    @rumps.clicked("Atualizar agora")
    def manual_refresh(self, _):
        self.refresh(None)

    @rumps.clicked("Sair")
    def quit_app(self, _):
        rumps.quit_application()

    def refresh(self, _):
        token = read_access_token()
        if not token:
            self._render_error("Sem token — faça login no Claude Code")
            return
        try:
            data = fetch_usage(token)
        except HTTPError as exc:
            if exc.code == 401:
                self._render_error("Token expirado — rode /usage no Claude Code")
            else:
                self._render_error(f"Erro HTTP {exc.code}")
            return
        except (URLError, TimeoutError):
            self._render_error("Sem conexão")
            return
        except Exception as exc:  # noqa: BLE001 - never crash the menu bar
            self._render_error(f"Erro: {exc}")
            return

        limits = [l for l in data.get("limits", []) if l.get("percent") is not None]
        self.title = compact_title(limits)
        self._render_menu(limits, data)

    def _render_menu(self, limits, data):
        self.menu.clear()
        for item in sorted(limits, key=lambda l: -(l.get("percent", 0) or 0)):
            self.menu.add(rumps.MenuItem(limit_label(item)))

        extra = data.get("extra_usage") or {}
        if extra.get("is_enabled") and extra.get("utilization") is not None:
            self.menu.add(rumps.MenuItem(f"Créditos extras: {extra['utilization']}%"))

        self.menu.add(rumps.separator)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.menu.add(rumps.MenuItem(f"Atualizado {stamp}"))
        self.menu.add(rumps.MenuItem("Atualizar agora", callback=self.manual_refresh))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Sair", callback=self.quit_app))

    def _render_error(self, message):
        self.title = f"{DOT_ERR} Claude"
        self.menu.clear()
        self.menu.add(rumps.MenuItem(message))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Atualizar agora", callback=self.manual_refresh))
        self.menu.add(rumps.MenuItem("Sair", callback=self.quit_app))


if __name__ == "__main__":
    UsageMonitor().run()
