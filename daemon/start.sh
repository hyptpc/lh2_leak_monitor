#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./daemon/start.sh (live service); use ./plan.sh or ./debug/simulate.sh for offline checks.' >&2; exit 2; fi
unit=daemon/units/lh2_monitor.service
if ! cmp -s "$unit" /etc/systemd/system/lh2_monitor.service; then
  echo 'Service definition needs updating: run ./daemon/install-service.sh first.' >&2
  exit 1
fi
if [[ "$(systemctl show lh2_monitor.service -p NeedDaemonReload --value)" != no ]]; then
  echo 'Run ./daemon/install-service.sh to reload the service definition first.' >&2
  exit 1
fi
# Starts only; does not enable automatic startup.
exec sudo systemctl start lh2_monitor.service
