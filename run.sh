#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/sks/monitor_tools/lh2_leak_monitor
SESSION=lh2-monitor
cd "$ROOT"
if (( $# )); then echo 'Usage: ./run.sh' >&2; exit 2; fi
if tmux has-session -t "=$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' already exists. Use: tmux attach -t $SESSION" >&2
  exit 1
fi
left=$(tmux new-session -d -s "$SESSION" -n monitor -c "$ROOT" -P -F '#{pane_id}' \
  '/home/sks/.venv/bin/python -B -u app/monitor.py --startup-alert wait; exec /bin/bash')
tmux split-window -h -p 50 -t "$left" -c "$ROOT" /bin/bash
tmux select-pane -t "$left"
echo "Started session '$SESSION'. View it with: tmux attach -t $SESSION"
