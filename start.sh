#!/bin/bash
set -euo pipefail

# Foreground process: systemd (or an operator's tmux) owns its lifecycle.
# For the safe virtual mode, run: python3 monitor.py
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${LH2_MONITOR_PYTHON:-/home/sks/.venv/bin/python}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/monitor.py" --live --startup-alert wait "$@"
