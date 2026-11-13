#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' \
        'Usage: daemon/start.sh [TARGET] | daemon/stop.sh [TARGET] | daemon/monitor.sh [TARGET]' \
        'TARGET: lh2, blc, ftof, or trip; inferred in individual tool directories.' \
        'start.sh: enable boot startup and start the service.' \
        'stop.sh: disable boot startup and stop the service.' \
        'monitor.sh: show service state without sudo.'
}

action=status
case "${1:-}" in
    --start) action=start; shift ;;
    --stop) action=stop; shift ;;
    -h|--help) usage; exit 0 ;;
esac
if (( $# > 1 )); then usage >&2; exit 2; fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="${SCRIPT_DIR%/*}"
target="${1:-${TOOL_DIR##*/}}"
case "$target" in
    lh2|lh2_leak_monitor) unit='lh2_monitor.service' ;;
    blc|BLC_hv_monitoring) unit='blc_monitor.service' ;;
    ftof|CVC_hv_monitoring) unit='ftof_monitor.service' ;;
    trip|trip_monitor) unit='trip_monitor.service' ;;
    *) printf 'Unknown target: %s. Specify lh2, blc, ftof, or trip.\n' "$target" >&2; exit 2 ;;
esac

printf 'Service: %s; action: %s\n' "$unit" "$action"
if [[ "$action" == status ]]; then
    exec systemctl show "$unit" --no-pager \
        --property=Id --property=LoadState --property=UnitFileState \
        --property=ActiveState --property=SubState --property=Type --property=MainPID \
        --property=NRestarts --property=Result --property=ExecMainStatus --property=FragmentPath
fi

privileged() {
    if (( EUID == 0 )); then
        systemctl "$@"
    else
        # sudo reads the password directly from the operator's terminal.
        sudo -- systemctl "$@"
    fi
}

if [[ "$action" == start ]]; then
    unit_type="$(systemctl show "$unit" --property=Type --value)"
    if [[ "$unit_type" != exec ]]; then
        printf '%s\n' 'Update the old systemd definition before starting.' >&2
        printf 'Stop it with daemon/stop.sh, then run: sudo /usr/bin/python3 "%s/configure.py" %s\n' \
            "$SCRIPT_DIR" "$target" >&2
        exit 2
    fi
    # Only an explicit operator start resets the bounded automatic-retry budget.
    privileged reset-failed "$unit"
    privileged enable --now "$unit"
else
    privileged disable --now "$unit"
fi
