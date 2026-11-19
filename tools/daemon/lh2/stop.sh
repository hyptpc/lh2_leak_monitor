#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./tools/daemon/lh2/stop.sh' >&2; exit 2; fi
# Stops monitoring; does not restore any equipment outputs.
exec sudo systemctl stop lh2_monitor.service
