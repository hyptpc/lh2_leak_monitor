#!/bin/bash
set -euo pipefail

# This installation is dedicated to monitor2 at this fixed local path.
# The foreground process is owned by systemd or the operator's terminal.
cd /home/sks/monitor_tools/lh2_leak_monitor
exec /home/sks/.venv/bin/python -u \
  /home/sks/monitor_tools/lh2_leak_monitor/monitor.py --startup-alert wait "$@"
