# Monitor service controls

| Script | Effect |
| --- | --- |
| `start.sh` | Enable automatic startup and start the service |
| `stop.sh` | Disable automatic startup and stop the service |
| `monitor.sh` | Show state, Python PID, exit result, and restart count |

Run in your terminal; sudo asks for your password. **Start launches real
monitoring.** From an individual tool directory, no target is needed:

```bash
./daemon/monitor.sh
./daemon/stop.sh
```

From `/home/sks/monitor_tools`, specify `lh2`, `blc`, `ftof`, or
`trip`, for example `./daemon/stop.sh lh2`. FTOF corresponds to the
`CVC_hv_monitoring` directory. Services manage Python directly, without tmux.

## One-time service-definition update

Stop the target before installing its updated definition:

```bash
./daemon/stop.sh lh2
sudo /usr/bin/python3 daemon/configure.py lh2
```

Use `all` with `configure.py` to install all four definitions, after stopping
all four services. The installer refuses active services, validates templates,
backs up previous definitions under `/var/backups/monitor-services/`, and reloads
systemd. **It does not start or enable anything.** Start only when ready using
`./daemon/start.sh TARGET`. Old tmux/oneshot definitions are rejected by start.

Definition validation without installation or service operations:

```bash
python daemon/configure.py --check all
```

## Restart policy

- LH2 never restarts automatically after completion, cancellation, or failure.
- BLC, FTOF, and Trip restart after 30 seconds if they exit. These legacy programs
  can return status 0 on errors, so any unexpected process exit is retried.
- At most **5 starts per hour, including the initial start**. Once the limit is
  reached, systemd leaves the service failed instead of retrying indefinitely.
- After fixing the cause, an explicit `start.sh` resets the retry limit.
  A manual `stop.sh` never triggers automatic restart.

LH2 code stays in `/home/sks/share/monitor-tools/lh2_leak_monitor`. BLC,
FTOF, Trip, and common controls use `/home/sks/monitor_tools`. Input files and
databases keep their existing shared paths, so units still wait for that mount. Logs are available with `journalctl -u lh2_monitor.service -f` (replace the
unit name as needed). Trip's audio runtime is configured for the existing sks
user; actual audio and equipment behavior are not exercised by offline tests.

For a monitor started directly in a terminal, use Ctrl+C there. These scripts
operate the registered service only.

## Offline checks

```bash
python daemon/test_monitor_scripts.py
python daemon/test_service_units.py
```

Tests use fake system tools and temporary files, never real service or equipment
operations.
