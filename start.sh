#!/bin/bash

# --- Configuration ---
SESSION_NAME="lh2"  # The name of the tmux session this service will manage
TMUX_PATH="/usr/bin/tmux" # Full path to tmux (find with 'which tmux')

# Python virtual environment path
VENV_PYTHON_PATH="/home/sks/.venv/bin/python"
# Project working directory
WORKING_DIR="/home/sks/share/monitor-tools/lh2_leak_monitor"

# Command to execute inside tmux
# We add "; /bin/bash" at the end.
# This ensures that after the Python script finishes (or is stopped),
# a new bash shell is started, keeping the tmux pane alive.
EXECUTE_COMMAND="$VENV_PYTHON_PATH ./monitor.py; /bin/bash"
# ------------------


# Check if a session with the same name already exists
if $TMUX_PATH has-session -t $SESSION_NAME 2>/dev/null; then
    # If it exists:
    # This service should not start, so return an error.
    echo "Error: tmux session '$SESSION_NAME' is already running." >&2
    echo "This service cannot start." >&2
    exit 1 # <--- Notify systemd of the failure
else
    # If it does not exist:
    # Start the new session.
    echo "Starting new tmux session: $SESSION_NAME"
    $TMUX_PATH new-session -d -s $SESSION_NAME -c $WORKING_DIR "$EXECUTE_COMMAND"
    exit 0 # <--- Notify systemd of the success
fi
