#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' \
        'Usage: monitor.sh {start|stop|restart|status|enable|disable} [lh2|blc|ftof|trip]' \
        'In a tool directory, the target is inferred from its directory name.' \
        'start/restart: run the real registered monitor.' \
        'stop: stop now, preserving boot registration.' \
        'enable: enable boot startup without starting now.' \
        'disable: stop now and disable boot startup.' \
        'status: show systemd state without sudo.'
}

if (( $# > 2 )); then
    usage >&2
    exit 2
fi
action="${1:-status}"
case "$action" in
    -h|--help|help) usage; exit 0 ;;
    start|stop|restart|status|enable|disable) ;;
    *) printf 'Unknown action: %s\n' "$action" >&2; usage >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="${SCRIPT_DIR%/*}"
target="${2:-${TOOL_DIR##*/}}"
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
        --property=ActiveState --property=SubState --property=Type --property=FragmentPath
fi

if [[ "$action" == disable ]]; then
    set -- disable --now "$unit"
else
    set -- "$action" "$unit"
fi
if (( EUID == 0 )); then
    exec systemctl "$@"
else
    # Keep stdin/TTY attached so sudo can ask the operator for a password.
    exec sudo -- systemctl "$@"
fi
