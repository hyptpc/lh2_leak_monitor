#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
exec /home/sks/.venv/bin/python -B app/monitor.py --show-plan "$@"
