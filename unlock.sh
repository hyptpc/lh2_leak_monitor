#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./unlock.sh' >&2; exit 2; fi
exec /home/sks/.venv/bin/python -B app/reset_runtime.py
