#!/bin/bash

./toggle_kikusui.py on

# --- Configuration (check and modify for your environment) ---

# Username for SSH login
TARGET_USER="sks"

# Target Raspberry Pi IP addresses (space-separated, can specify multiple)
TARGET_HOSTS=("192.168.20.12" "192.168.20.13")

# Full path to uhubctl on the remote host
UHUBCTL_PATH="/usr/sbin/uhubctl"

# Hub location to control
HUB_LOCATION="1-1"

# Port numbers to enable (space-separated)
PORTS_TO_ENABLE=(1 2 3 4)

# SSH options to skip interactive "yes/no" host key verification
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# --- Main Script ---

echo "Turning ON USB ports..."

# Loop through each host
for host in "${TARGET_HOSTS[@]}"; do
    echo "--- [ Target Host: $host ] ---"

    # Loop through each port
    for port in "${PORTS_TO_ENABLE[@]}"; do

        # Define the remote command to execute (-a 1 = power ON)
        REMOTE_CMD="sudo $UHUBCTL_PATH -l $HUB_LOCATION -p $port -a 1"

        echo "  Enabling port $port... (ssh $TARGET_USER@$host ...)"

        # Execute the command via SSH
        ssh $SSH_OPTS "$TARGET_USER@$host" "$REMOTE_CMD"

        # Check the exit code (simple error message)
        if [ $? -ne 0 ]; then
            echo "  [ERROR] Command failed on $host port $port."
        fi

    done
done

echo "--- All done ---"
