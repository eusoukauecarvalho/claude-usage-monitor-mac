#!/bin/bash
# Double-click to launch the Claude Usage Monitor menu bar app.
cd "$(dirname "$0")"
exec ./.venv/bin/python monitor.py
