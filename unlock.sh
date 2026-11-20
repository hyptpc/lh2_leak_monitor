#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/sks/monitor_tools/lh2_leak_monitor
cd "$ROOT"
mode=${1:-ask}
if (( $# > 1 )); then echo 'Usage: ./unlock.sh [--check|--stop-service]' >&2; exit 2; fi
case "$mode" in
  --help|-h) echo 'Usage: ./unlock.sh [--check|--stop-service]'; echo 'Inspect the live lock; optionally stop the LH2 systemd service. Never deletes lock files or kills manual monitors.'; exit 0;;
  ask|--check|--stop-service) ;;
  *) echo 'Usage: ./unlock.sh [--check|--stop-service]' >&2; exit 2;;
esac
lock="$ROOT/runtime/monitor.lock"
# flock is held by an open file descriptor, not by the presence of this file.
# Never unlink it: doing so could allow a second monitor on a different inode.
if [[ ! -e "$lock" ]] || flock -n "$lock" true; then
  echo 'Monitor lock is free. Nothing to unlock.'
  exit 0
fi
echo 'Monitor lock is held by a running process:'
lslocks --noheadings --output PID,COMMAND,TYPE,MODE,PATH | grep -F -- "$lock" || true
state=$(systemctl show lh2_monitor.service -p ActiveState --value)
echo "LH2 systemd service: $state"
if [[ "$mode" == --check ]]; then exit 1; fi
case "$state" in
  active|activating|reloading) ;;
  *) echo 'No running LH2 service to stop. Stop the manual monitor with Ctrl+C in its terminal/tmux pane.' >&2; exit 1;;
esac
if [[ "$mode" == ask ]]; then
  if [[ ! -t 0 ]]; then echo 'To stop the service explicitly: ./unlock.sh --stop-service' >&2; exit 1; fi
  read -r -p 'Stop LH2 monitoring to release its lock? This does not restore equipment power. [y/N] ' answer
  case "$answer" in y|Y|yes|YES) ;; *) echo 'Not stopped; lock unchanged.'; exit 1;; esac
fi
sudo systemctl stop lh2_monitor.service
if flock -n "$lock" true; then
  echo 'LH2 service stopped. Monitor lock is free. No monitor started.'
else
  echo 'Lock is still held. No lock file was deleted and no other process was killed.' >&2
  exit 1
fi
