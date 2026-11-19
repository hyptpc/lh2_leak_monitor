#!/usr/bin/env bash
set -euo pipefail
cd /home/sks/monitor_tools/lh2_leak_monitor
if (( $# )); then echo 'Usage: ./tools/daemon/lh2/install-service.sh' >&2; exit 2; fi
state=$(systemctl show lh2_monitor.service -p ActiveState --value)
case "$state" in inactive|failed) ;; *) echo "Refusing to replace definition while service is $state; stop it first." >&2; exit 1;; esac
unit=tools/daemon/units/lh2_monitor.service
systemd-analyze verify "$unit"
sudo install -m 0644 "$unit" /etc/systemd/system/lh2_monitor.service
sudo systemctl daemon-reload
echo 'Service definition updated. Not started or enabled.'
