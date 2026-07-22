#!/usr/bin/env python3
"""Claude Usage Monitor — macOS menu bar app.

Shows your Claude session/weekly usage in the menu bar in real time,
reading the same data source the `/usage` command uses. Clicking
"Abrir monitor" opens a native window with progress bars per quota.
Token is read from the macOS Keychain on every refresh, so it stays
in sync when Claude Code rotates it.
"""

import json
import os
import ssl
import subprocess
import time
import warnings
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import certifi
import objc
import rumps

# Setting a layer's CGColor is safe but pyobjc lacks metadata for the opaque
# CGColorRef and warns on every call; silence it to keep the log clean.
warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect

# --- Constants ---------------------------------------------------------------
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
REFRESH_SECONDS = 90
ANTHROPIC_BETA = "oauth-2025-04-20"
USER_AGENT = "claude-usage-monitor/1.0"
HTTP_TIMEOUT = 15

# Percentage thresholds for the colored bars.
WARN_THRESHOLD = 70
CRIT_THRESHOLD = 90

# Default backoff (seconds) when the endpoint returns 429 without Retry-After.
DEFAULT_RETRY_AFTER = 120

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO_PATH = os.path.join(ASSET_DIR, "claude-logo.svg")

# Window layout (points).
WIN_W = 380
PAD = 20
HEADER_H = 34
ROW_H = 44
FOOTER_H = 30

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


# --- Data access -------------------------------------------------------------
def read_access_token():
    """Read the OAuth access token from the macOS Keychain."""
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
    total = int((reset - datetime.now(timezone.utc)).total_seconds())
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


def limit_name(item):
    """Readable name for one limit entry."""
    kind = item.get("kind", "")
    scope = item.get("scope") or {}
    model = (scope.get("model") or {}).get("display_name")
    names = {
        "session": "Sessão (5h)",
        "weekly_all": "Semanal (tudo)",
        "weekly_scoped": f"Semanal {model}" if model else "Semanal (modelo)",
    }
    return names.get(kind, model or kind or "Limite")


def limit_label(item):
    """Full one-line label used in the dropdown menu."""
    pct = item.get("percent", 0)
    reset = format_reset(item.get("resets_at"))
    return f"{limit_name(item)}: {pct}%  ·  reseta em {reset}"


def compact_title(limits):
    """Short menu-bar title, e.g. 'S44% W42%' (logo shown separately)."""
    by_kind = {l.get("kind"): l.get("percent", 0) for l in limits}
    parts = []
    if by_kind.get("session") is not None:
        parts.append(f"S{by_kind['session']}%")
    if by_kind.get("weekly_all") is not None:
        parts.append(f"W{by_kind['weekly_all']}%")
    return " ".join(parts) if parts else "Claude"


def retry_after_seconds(http_error):
    """Seconds to wait after a 429, from the Retry-After header if present."""
    header = http_error.headers.get("Retry-After") if http_error.headers else None
    try:
        return max(int(header), 1)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def severity_color(percent):
    """NSColor for a bar based on its percentage."""
    if percent >= CRIT_THRESHOLD:
        return NSColor.systemRedColor()
    if percent >= WARN_THRESHOLD:
        return NSColor.systemOrangeColor()
    return NSColor.systemGreenColor()


# --- AppKit widget helpers ---------------------------------------------------
class FlippedView(NSView):
    """A container whose origin is top-left (y grows downward)."""

    def isFlipped(self):
        return True


def load_logo(size):
    """Load the Claude logo as an NSImage sized to `size` points."""
    img = NSImage.alloc().initWithContentsOfFile_(LOGO_PATH)
    if img is not None:
        img.setSize_((size, size))
    return img


def make_label(frame, text, size=13, bold=False, color=None, align_right=False):
    lbl = NSTextField.alloc().initWithFrame_(frame)
    lbl.setStringValue_(text)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    lbl.setTextColor_(color or NSColor.labelColor())
    if align_right:
        lbl.setAlignment_(NSTextAlignmentRight)
    return lbl


def make_bar(frame, percent, color):
    """A pill-shaped track with a colored fill proportional to percent."""
    track = NSView.alloc().initWithFrame_(frame)
    track.setWantsLayer_(True)
    track.layer().setBackgroundColor_(NSColor.tertiaryLabelColor().CGColor())
    track.layer().setCornerRadius_(frame.size.height / 2.0)
    fill_w = frame.size.width * min(max(percent, 0), 100) / 100.0
    fill = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, fill_w, frame.size.height))
    fill.setWantsLayer_(True)
    fill.layer().setBackgroundColor_(color.CGColor())
    fill.layer().setCornerRadius_(frame.size.height / 2.0)
    track.addSubview_(fill)
    return track


# --- App ---------------------------------------------------------------------
class UsageMonitor(rumps.App):
    def __init__(self):
        super().__init__("", icon=LOGO_PATH, template=False, quit_button=None)
        self.title = "…"
        self._window = None
        self._limits = []
        self._stamp = "—"
        self._extra_line = None
        self._stale = None
        self._retry_after_ts = 0.0
        self.menu = ["Abrir monitor", "Atualizar agora", None, "Sair"]
        self.timer = rumps.Timer(self.refresh, REFRESH_SECONDS)
        self.timer.start()
        self.refresh(None)

    # --- menu actions ---
    @rumps.clicked("Abrir monitor")
    def open_monitor(self, _):
        if self._window is None:
            self._build_window()
        self._refresh_window()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    @rumps.clicked("Atualizar agora")
    def manual_refresh(self, _):
        self._retry_after_ts = 0.0  # user asked explicitly: bypass any backoff
        self.refresh(None)

    @rumps.clicked("Sair")
    def quit_app(self, _):
        rumps.quit_application()

    # --- data cycle ---
    def refresh(self, _):
        # Respect an active backoff window after a 429 (keep showing last data).
        if time.monotonic() < self._retry_after_ts:
            return

        token = read_access_token()
        if not token:
            self._stale = "Sem token — faça login no Claude Code"
            self._render()
            return

        try:
            data = fetch_usage(token)
        except HTTPError as exc:
            if exc.code == 429:
                self._retry_after_ts = time.monotonic() + retry_after_seconds(exc)
                self._stale = "Limite de requisições atingido — mostrando último dado"
            elif exc.code == 401:
                self._stale = "Token expirado — rode /usage no Claude Code"
            else:
                self._stale = f"Erro HTTP {exc.code} — mostrando último dado"
            self._render()
            return
        except (URLError, TimeoutError):
            self._stale = "Sem conexão — mostrando último dado"
            self._render()
            return
        except Exception as exc:  # noqa: BLE001 - never crash the menu bar
            self._stale = f"Erro: {exc}"
            self._render()
            return

        # Success: update the cached data and clear any stale flag.
        self._limits = [l for l in data.get("limits", []) if l.get("percent") is not None]
        self._stamp = datetime.now().strftime("%H:%M:%S")
        extra = data.get("extra_usage") or {}
        if extra.get("is_enabled") and extra.get("utilization") is not None:
            self._extra_line = f"Créditos extras: {extra['utilization']}%"
        else:
            self._extra_line = None
        self._stale = None
        self._render()

    def _render(self):
        """Rebuild the menu bar title and dropdown from cached state.

        Keeps showing the last good percentages even while stale, so a
        transient rate-limit or network blip never hides the numbers.
        """
        ordered = sorted(self._limits, key=lambda l: -(l.get("percent", 0) or 0))
        self.title = compact_title(self._limits) if self._limits else "⚠️"

        self.menu.clear()
        self.menu.add(rumps.MenuItem("Abrir monitor", callback=self.open_monitor))
        self.menu.add(rumps.separator)
        if self._stale:
            self.menu.add(rumps.MenuItem(f"⚠️ {self._stale}"))
            self.menu.add(rumps.separator)
        if ordered:
            for item in ordered:
                self.menu.add(rumps.MenuItem(limit_label(item)))
            if self._extra_line:
                self.menu.add(rumps.MenuItem(self._extra_line))
        elif not self._stale:
            self.menu.add(rumps.MenuItem("Sem dados de uso."))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(f"Atualizado {self._stamp}"))
        self.menu.add(rumps.MenuItem("Atualizar agora", callback=self.manual_refresh))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Sair", callback=self.quit_app))

        if self._window is not None and self._window.isVisible():
            self._refresh_window()

    # --- window ---
    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
        )
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, 300), style, NSBackingStoreBuffered, False
        )
        win.setTitle_("Claude Usage")
        win.setReleasedWhenClosed_(False)
        win.center()
        self._window = win

    def _refresh_window(self):
        if self._window is None:
            return
        ordered = sorted(self._limits, key=lambda l: -(l.get("percent", 0) or 0))
        content_h = PAD + HEADER_H + max(len(ordered), 1) * ROW_H + FOOTER_H + PAD

        # Resize the window while keeping its top edge anchored.
        frame = self._window.frame()
        top = frame.origin.y + frame.size.height
        self._window.setContentSize_((WIN_W, content_h))
        new_frame = self._window.frame()
        new_frame.origin.y = top - new_frame.size.height
        self._window.setFrameOrigin_(new_frame.origin)

        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, WIN_W, content_h))
        cw = WIN_W - 2 * PAD

        # Header: logo + title.
        logo = NSImageView.alloc().initWithFrame_(NSMakeRect(PAD, PAD, 26, 26))
        logo.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        logo.setImage_(load_logo(26))
        root.addSubview_(logo)
        root.addSubview_(
            make_label(NSMakeRect(PAD + 34, PAD + 3, cw - 34, 20), "Claude Usage", size=15, bold=True)
        )

        # One row per quota.
        y = PAD + HEADER_H
        if not ordered:
            root.addSubview_(
                make_label(NSMakeRect(PAD, y, cw, 18), "Sem dados de uso.", color=NSColor.secondaryLabelColor())
            )
        for item in ordered:
            pct = item.get("percent", 0) or 0
            root.addSubview_(make_label(NSMakeRect(PAD, y, cw * 0.6, 16), limit_name(item), bold=True))
            stats = f"{pct}%  ·  {format_reset(item.get('resets_at'))}"
            root.addSubview_(
                make_label(
                    NSMakeRect(PAD + cw * 0.4, y, cw * 0.6, 16), stats,
                    color=NSColor.secondaryLabelColor(), align_right=True,
                )
            )
            root.addSubview_(make_bar(NSMakeRect(PAD, y + 22, cw, 10), pct, severity_color(pct)))
            y += ROW_H

        if self._stale:
            footer_text = f"⚠️ {self._stale} · último dado {self._stamp}"
            footer_color = NSColor.systemOrangeColor()
        else:
            footer_text = f"Atualizado {self._stamp} · atualiza a cada {REFRESH_SECONDS}s"
            footer_color = NSColor.tertiaryLabelColor()
        root.addSubview_(
            make_label(NSMakeRect(PAD, y + 4, cw, 16), footer_text, size=11, color=footer_color)
        )
        self._window.setContentView_(root)


if __name__ == "__main__":
    UsageMonitor().run()
