#!/usr/bin/env python3
"""Claude Usage Monitor — macOS menu bar app.

Shows your Claude session/weekly usage in the menu bar in real time,
reading the same data source the `/usage` command uses. Clicking
"Abrir monitor" opens a native window with progress bars per quota.
Token is read from the macOS Keychain on every refresh, so it stays
in sync when Claude Code rotates it.
"""

import json
import math
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
    NSAnimationContext,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSLineBreakByWordWrapping,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSTimer

# CoreAnimation classes are registered at runtime by AppKit but pyobjc has no
# import binding for them, so look them up dynamically for the confetti burst.
CAEmitterLayer = objc.lookUpClass("CAEmitterLayer")
CAEmitterCell = objc.lookUpClass("CAEmitterCell")

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

# Alert thresholds (percent) that trigger notifications when first crossed.
TIP_THRESHOLD = 80      # gentle tip: save tokens
ALERT_THRESHOLD = 90    # heads-up: getting close
NEAR_THRESHOLD = 95     # urgent: almost out
LIMIT_THRESHOLD = 100   # limit reached
# A drop of at least this many points between refreshes means the window
# renewed (quota is back) — worth celebrating with confetti.
RESET_DROP = 20

# System sounds played alongside each alert (paths always present on macOS).
TIP_SOUND = "/System/Library/Sounds/Tink.aiff"
ALERT_SOUND = "/System/Library/Sounds/Glass.aiff"
NEAR_SOUND = "/System/Library/Sounds/Blow.aiff"
LIMIT_SOUND = "/System/Library/Sounds/Sosumi.aiff"
CELEBRATE_SOUND = "/System/Library/Sounds/Hero.aiff"

# Portuguese weekday abbreviations (Mon=0), used when a reset is not today.
PT_WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

# In-app toast (the styled card shown for every alert).
TOAST_W = 360
TOAST_DURATION = 5.0          # seconds a normal toast stays on screen
TOAST_CELEBRATE_DURATION = 6.5  # celebrations linger a bit while confetti falls
TOAST_MARGIN = 16             # gap from the screen edge and between stacked toasts

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


def reset_local(resets_at):
    """Parse the ISO reset timestamp into a local-timezone datetime, or None."""
    if not resets_at:
        return None
    try:
        return datetime.fromisoformat(resets_at).astimezone()
    except ValueError:
        return None


def clock_suffix(resets_at):
    """Wall-clock time of the reset, e.g. '14:30' or 'ter 14:30' if not today."""
    local = reset_local(resets_at)
    if local is None:
        return ""
    now = datetime.now().astimezone()
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return f"{PT_WEEKDAYS[local.weekday()]} {local.strftime('%H:%M')}"


# --- Notifications -----------------------------------------------------------
def play_sound(path):
    """Play a system sound without blocking (fire-and-forget)."""
    try:
        subprocess.Popen(["afplay", path])
    except OSError:
        pass  # never let a missing binary crash the menu bar


def push_notification(title, message):
    """Post a macOS notification banner via osascript (fire-and-forget).

    ensure_ascii=False keeps accents/em-dashes/emoji as literal UTF-8 — the
    \\uXXXX escapes json emits by default are invalid in AppleScript strings.
    """
    ttl = json.dumps(title, ensure_ascii=False)
    msg = json.dumps(message, ensure_ascii=False)
    try:
        subprocess.Popen(["osascript", "-e", f"display notification {msg} with title {ttl}"])
    except OSError:
        pass


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
    clock = clock_suffix(item.get("resets_at"))
    when = f"{reset} · {clock}" if clock else reset
    return f"{limit_name(item)}: {pct}%  ·  reseta em {when}"


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


# --- Confetti ----------------------------------------------------------------
_PARTICLE_IMAGE = None


def confetti_particle():
    """A small white rounded-rect CGImage, tinted per-cell into confetti."""
    global _PARTICLE_IMAGE
    if _PARTICLE_IMAGE is not None:
        return _PARTICLE_IMAGE
    size = 10.0
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()
    NSColor.whiteColor().set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(1, 2, 8, 5), 1.5, 1.5
    ).fill()
    img.unlockFocus()
    _PARTICLE_IMAGE = img.CGImageForProposedRect_context_hints_(None, None, None)
    return _PARTICLE_IMAGE


def _confetti_cell(color):
    """One colored confetti stream falling downward with spin and fade."""
    cell = CAEmitterCell.alloc().init()
    cell.setContents_(confetti_particle())
    cell.setColor_(color.CGColor())
    cell.setBirthRate_(9.0)
    cell.setLifetime_(4.0)
    cell.setLifetimeRange_(1.0)
    cell.setVelocity_(170.0)
    cell.setVelocityRange_(70.0)
    cell.setEmissionLongitude_(-math.pi / 2)  # aim down (layer y grows upward)
    cell.setEmissionRange_(math.pi / 5)
    cell.setYAcceleration_(-220.0)  # gravity pulls toward the bottom
    cell.setSpin_(3.5)
    cell.setSpinRange_(5.0)
    cell.setScale_(0.9)
    cell.setScaleRange_(0.5)
    cell.setScaleSpeed_(-0.05)
    cell.setAlphaSpeed_(-0.18)
    return cell


def build_confetti_emitter(width, height):
    """A CAEmitterLayer that rains confetti from the top edge of a view."""
    palette = [
        NSColor.systemRedColor(), NSColor.systemOrangeColor(),
        NSColor.systemYellowColor(), NSColor.systemGreenColor(),
        NSColor.systemBlueColor(), NSColor.systemPurpleColor(),
        NSColor.systemPinkColor(),
    ]
    emitter = CAEmitterLayer.layer()
    emitter.setEmitterPosition_((width / 2.0, height))
    emitter.setEmitterSize_((width, 1.0))
    emitter.setEmitterShape_("line")
    emitter.setEmitterMode_("outline")
    emitter.setEmitterCells_([_confetti_cell(c) for c in palette])
    return emitter


# --- Toast (in-app notification card) ----------------------------------------
def make_wrapping_label(frame, text, size, color):
    """A non-editable label that word-wraps within its frame."""
    lbl = NSTextField.alloc().initWithFrame_(frame)
    lbl.setStringValue_(text)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setFont_(NSFont.systemFontOfSize_(size))
    lbl.setTextColor_(color)
    lbl.setLineBreakMode_(NSLineBreakByWordWrapping)
    lbl.cell().setWraps_(True)
    lbl.cell().setScrollable_(False)
    return lbl


def build_toast_card(title, message, accent, width, height):
    """The rounded card content: accent stripe, logo, title and message."""
    card = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    card.setWantsLayer_(True)
    layer = card.layer()
    layer.setCornerRadius_(14.0)
    layer.setMasksToBounds_(True)
    layer.setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
    layer.setBorderWidth_(0.5)
    layer.setBorderColor_(NSColor.separatorColor().CGColor())

    stripe = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 5, height))
    stripe.setWantsLayer_(True)
    stripe.layer().setBackgroundColor_(accent.CGColor())
    card.addSubview_(stripe)

    logo = NSImageView.alloc().initWithFrame_(NSMakeRect(18, 16, 26, 26))
    logo.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    logo.setImage_(load_logo(26))
    card.addSubview_(logo)

    tx = 56
    card.addSubview_(make_label(NSMakeRect(tx, 15, width - tx - 16, 20), title, size=14, bold=True))
    card.addSubview_(
        make_wrapping_label(
            NSMakeRect(tx, 38, width - tx - 16, height - 38 - 12),
            message, 12, NSColor.secondaryLabelColor(),
        )
    )
    return card


def build_toast_panel(card, width, height):
    """A borderless, non-activating floating panel that hosts a toast card."""
    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setReleasedWhenClosed_(False)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    panel.setContentView_(card)
    return panel


def animate_alpha(window, target, duration, done=None):
    """Fade a window's alpha to `target` over `duration`, then call `done`."""
    def changes(ctx):
        ctx.setDuration_(duration)
        window.animator().setAlphaValue_(target)

    NSAnimationContext.runAnimationGroup_completionHandler_(changes, done or (lambda: None))


# --- Alert levels ------------------------------------------------------------
def deep_orange():
    """A darker, more urgent orange than systemOrange (used at 95%)."""
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(0.92, 0.38, 0.05, 1.0)


def alert_levels():
    """Ordered high→low: (threshold, accent, sound, title_fn, message_fn).

    The highest crossed level wins, so a jump from 80→100 fires only the
    limit alert. Each level has its own emoji, color, sound and copy.
    """
    return [
        (
            LIMIT_THRESHOLD, NSColor.systemRedColor(), LIMIT_SOUND,
            lambda name, pct: f"🚫 {name} atingiu o limite",
            lambda name, pct, reset: f"Uso em {pct}%. Renova em {reset} — pause ou use créditos extras.",
        ),
        (
            NEAR_THRESHOLD, deep_orange(), NEAR_SOUND,
            lambda name, pct: f"🔥 {name} em {pct}%",
            lambda name, pct, reset: f"Quase no limite! Segura as tarefas pesadas. Renova em {reset}.",
        ),
        (
            ALERT_THRESHOLD, NSColor.systemOrangeColor(), ALERT_SOUND,
            lambda name, pct: f"⚠️ {name} em {pct}%",
            lambda name, pct, reset: f"Chegando perto do limite. Renova em {reset}.",
        ),
        (
            TIP_THRESHOLD, NSColor.systemYellowColor(), TIP_SOUND,
            lambda name, pct: f"💡 {name} em {pct}%",
            lambda name, pct, reset: "Dica: reduza o effort ou troque para um modelo mais leve (ex.: Haiku) para economizar.",
        ),
    ]


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
        self._prev_percents = {}  # kind -> last seen percent, for edge alerts
        self._toasts = []  # live toast panels (kept referenced so ARC won't drop them)
        # Run as an accessory app: no Dock icon (so it can't be closed by
        # accident) and no generic "Python" name — it lives in the menu bar only.
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        self.menu = ["Abrir monitor", "Atualizar agora", None, "Sair"]
        self.timer = rumps.Timer(self.refresh, REFRESH_SECONDS)
        self.timer.start()
        self.refresh(None)

    # --- menu actions ---
    @rumps.clicked("Abrir monitor")
    def open_monitor(self, _):
        self._present_window()

    def _present_window(self):
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
        self._check_alerts()
        self._render()

    # --- alerts ---
    def _check_alerts(self):
        """Fire threshold notifications and reset confetti on state changes.

        Compares each limit's current percent against the value seen on the
        previous refresh so an alert fires once, on the crossing, not on every
        poll. The very first refresh only records a baseline (no alerts).
        """
        had_baseline = bool(self._prev_percents)
        celebrated = False
        for item in self._limits:
            kind = item.get("kind")
            pct = item.get("percent", 0) or 0
            prev = self._prev_percents.get(kind)
            self._prev_percents[kind] = pct
            if prev is None or not had_baseline:
                continue
            if prev - pct >= RESET_DROP and not celebrated:
                celebrated = True
                self._on_reset(item)
            else:
                self._on_threshold(item, prev, pct)

    def _on_threshold(self, item, prev, pct):
        """Alert when a limit first crosses 80 / 90 / 95 / 100% (highest wins)."""
        name = limit_name(item)
        reset = format_reset(item.get("resets_at"))
        for at, accent, sound, title_fn, msg_fn in alert_levels():
            if prev < at <= pct:
                self._show_toast(title_fn(name, pct), msg_fn(name, pct, reset), accent, sound=sound)
                return

    def _on_reset(self, item):
        """Celebrate a renewed window with a confetti toast."""
        name = limit_name(item)
        self._show_toast(
            f"🎉 {name} renovada!", "Sua janela voltou — quota disponível de novo.",
            NSColor.systemGreenColor(), sound=CELEBRATE_SOUND, confetti=True,
        )

    # --- toast presentation ---
    def _show_toast(self, title, message, accent, sound=None, confetti=False, push=True):
        """Fire all three channels at once: in-app card + OS push + sound.

        The card is the branded on-screen experience; the push banner is the
        native notification (also shows in Notification Center); the sound is
        the audible cue. Every alert uses all three.
        """
        if push:
            push_notification(title, message)
        if sound:
            play_sound(sound)

        width = TOAST_W
        height = 168 if confetti else 94
        card = build_toast_card(title, message, accent, width, height)
        panel = build_toast_panel(card, width, height)

        screen = NSScreen.mainScreen()
        vf = screen.visibleFrame() if screen else NSMakeRect(0, 0, 1440, 900)
        offset = len(self._toasts) * (height + 10)
        x = vf.origin.x + vf.size.width - width - TOAST_MARGIN
        y = vf.origin.y + vf.size.height - height - TOAST_MARGIN - offset
        panel.setFrameOrigin_((x, y))

        panel.setAlphaValue_(0.0)
        panel.orderFrontRegardless()
        self._toasts.append(panel)
        animate_alpha(panel, 1.0, 0.22)

        if confetti:
            bounds = card.bounds()
            overlay = NSView.alloc().initWithFrame_(bounds)
            overlay.setWantsLayer_(True)
            card.addSubview_(overlay)
            emitter = build_confetti_emitter(bounds.size.width, bounds.size.height)
            overlay.layer().addSublayer_(emitter)
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.7, False, lambda _t: emitter.setBirthRate_(0.0)
            )

        duration = TOAST_CELEBRATE_DURATION if confetti else TOAST_DURATION
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            duration, False, lambda _t: self._dismiss_toast(panel)
        )

    def _dismiss_toast(self, panel):
        """Fade the toast out and drop our reference to it."""
        def done():
            if panel in self._toasts:
                self._toasts.remove(panel)
            panel.orderOut_(None)

        animate_alpha(panel, 0.0, 0.35, done)

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
            root.addSubview_(make_label(NSMakeRect(PAD, y, cw * 0.5, 16), limit_name(item), bold=True))
            reset = format_reset(item.get("resets_at"))
            clock = clock_suffix(item.get("resets_at"))
            stats = f"{pct}%  ·  {reset}" + (f"  ·  {clock}" if clock else "")
            root.addSubview_(
                make_label(
                    NSMakeRect(PAD + cw * 0.5, y, cw * 0.5, 16), stats, size=12,
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
