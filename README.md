# Claude Usage Monitor for macOS

A lightweight macOS **menu bar app** that shows your Claude usage limits in real
time — session (5h), weekly, and per-model quotas — so you never have to run
`/usage` or dig through settings again.

It reads the exact same data source Claude Code's `/usage` command uses, and
authenticates through your existing Claude Code login (the OAuth token is read
from the macOS Keychain on every refresh — never logged, stored, or sent
anywhere else).

> Inspired by [usage-monitor-for-claude](https://github.com/jens-duttke/usage-monitor-for-claude)
> (a Windows tray app) — this is a native macOS menu bar equivalent.

## What it looks like

The menu bar shows a compact title such as:

```
🟢 S45% W42%
```

- `S` = session (5-hour) window
- `W` = weekly window
- 🟢 `<70%` · 🟡 `70–90%` · 🔴 `>90%` · ⚪️ error / no connection

Clicking it opens a dropdown with every active quota, its percentage, and a
countdown to the next reset:

```
Semanal Fable: 67%  ·  reseta em 3h 27m
Sessão (5h): 45%    ·  reseta em 2h 57m
Semanal (tudo): 42% ·  reseta em 3h 27m
```

## Requirements

- macOS
- [Claude Code](https://claude.com/claude-code) installed and logged in
- Python 3.9+ (the system or Homebrew `python3` is fine)

## Install

```bash
git clone https://github.com/eusoukauecarvalho/claude-usage-monitor-mac.git
cd claude-usage-monitor-mac
./install.sh
```

`install.sh` creates an isolated virtual environment, installs the two
dependencies (`rumps`, `certifi`), and registers a LaunchAgent so the app
starts automatically at login. The icon appears in the top-right of your menu
bar within a few seconds.

## Usage

- **Left-click** the menu bar item to see all quotas and reset times.
- **"Atualizar agora"** forces an immediate refresh (it auto-refreshes every 60s).
- **"Sair"** quits the current instance.

## Uninstall

```bash
./uninstall.sh   # removes the LaunchAgent and stops the app
```

Then delete the project folder. Nothing else is left on your system.

## How it works

| Piece            | Detail                                                                 |
| ---------------- | ---------------------------------------------------------------------- |
| Data source      | `GET https://api.anthropic.com/api/oauth/usage`                        |
| Auth             | `Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20`   |
| Token source     | macOS Keychain item `Claude Code-credentials` → `claudeAiOauth.accessToken` |
| Refresh          | Every 60 seconds; token re-read each cycle so it tracks token rotation |

The token never leaves your machine except in the `Authorization` header of the
request to `api.anthropic.com`.

## Configuration

Edit the constants at the top of `monitor.py`:

- `REFRESH_SECONDS` — how often to poll (default `60`)
- `WARN_THRESHOLD` / `CRIT_THRESHOLD` — the 🟡 / 🔴 cutoffs (default `70` / `90`)

Restart the app after editing (`launchctl unload`/`load` the plist, or re-run
`./install.sh`).

## Disclaimer

Unofficial, community-built tool. Not affiliated with or endorsed by Anthropic.
It uses the same authenticated endpoint Claude Code itself calls, but that
endpoint is undocumented and may change.

## License

[MIT](LICENSE) © 2026 Kauê Carvalho
