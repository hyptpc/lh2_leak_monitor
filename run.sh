#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/sks/monitor_tools/lh2_leak_monitor
SESSION=lh2-monitor
cd "$ROOT"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Usage: ./run.sh'
  echo 'Start LIVE monitoring in the left tmux pane; the right pane is an operator shell.'
  echo 'Reconnect to an existing session without starting another monitor.'
  echo 'Detach: Ctrl+b then d. Stop monitor: Ctrl+C in the left pane.'
  exit 0
fi
if (( $# )); then echo 'Usage: ./run.sh' >&2; exit 2; fi
command -v tmux >/dev/null || { echo 'tmux is not installed.' >&2; exit 1; }
if ! tmux has-session -t "=$SESSION" 2>/dev/null; then
  state=$(systemctl show lh2_monitor.service -p ActiveState --value)
  case "$state" in
    inactive|failed) ;;
    *) echo "LH2 service is $state. Run ./unlock.sh --stop-service first; no tmux monitor started." >&2; exit 1 ;;
  esac
  # Session name creation is atomic: concurrent launchers cannot create two monitors.
  left=$(tmux new-session -d -s "$SESSION" -n monitor -c "$ROOT" -P -F '#{pane_id}' /bin/bash)
  tmux set-window-option -t "$left" remain-on-exit on
  tmux set-window-option -t "$left" pane-border-status top
  right=$(tmux split-window -h -p 50 -t "$left" -c "$ROOT" -P -F '#{pane_id}' /bin/bash)
  tmux select-pane -t "$left" -T 'LH2 monitor (Ctrl+C to stop)'
  tmux select-pane -t "$right" -T 'Operator shell'
  tmux select-pane -t "$left"
  # Keep the left pane after exit so the final result/error stays visible. Never auto-restart.
  tmux respawn-pane -k -t "$left" -c "$ROOT" \
    '/home/sks/.venv/bin/python -B -u /home/sks/monitor_tools/lh2_leak_monitor/app/monitor.py --startup-alert wait'
fi
if [[ -n "${TMUX:-}" ]]; then
  exec tmux switch-client -t "=$SESSION"
else
  exec tmux attach-session -t "=$SESSION"
fi
