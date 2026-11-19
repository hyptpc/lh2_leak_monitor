#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./daemon/stop.sh' >&2; exit 2; fi
# Stops monitoring; does not restore any equipment outputs.
exec sudo systemctl stop lh2_monitor.service
