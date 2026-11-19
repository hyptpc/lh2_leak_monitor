#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
exec systemctl --no-pager --full status lh2_monitor.service "$@"
