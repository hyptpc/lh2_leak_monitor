#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./tools/daemon/lh2/start.sh (live service); use ./plan.sh or ./simulate.sh for offline checks.' >&2; exit 2; fi
unit=tools/daemon/units/lh2_monitor.service
if ! cmp -s "$unit" /etc/systemd/system/lh2_monitor.service; then
  echo 'Service definition needs updating: run ./tools/daemon/lh2/install-service.sh first.' >&2
  exit 1
fi
if [[ "$(systemctl show lh2_monitor.service -p NeedDaemonReload --value)" != no ]]; then
  echo 'Run ./tools/daemon/lh2/install-service.sh to reload the service definition first.' >&2
  exit 1
fi
# Starts only; does not enable automatic startup.
exec sudo systemctl start lh2_monitor.service
