# Monitor service controls

| Script | Effect |
| --- | --- |
| `start.sh` | Enable automatic startup and start the service |
| `stop.sh` | Disable automatic startup and stop the service |
| `monitor.sh` | Show service state; no sudo |

Run in your terminal. sudo asks for your password when needed; passwords are
not stored. **Start launches real monitoring.**

From an individual tool directory:

```bash
./daemon/monitor.sh
./daemon/stop.sh
```

From the parent `monitor-tools` directory, specify the target:

```bash
./daemon/monitor.sh lh2
./daemon/stop.sh lh2
```

| Target | Tool directory | Unit |
| --- | --- | --- |
| `lh2` | `lh2_leak_monitor` | `lh2_monitor.service` |
| `blc` | `BLC_hv_monitoring` | `blc_monitor.service` |
| `ftof` | `CVC_hv_monitoring` | `ftof_monitor.service` |
| `trip` | `trip_monitor` | `trip_monitor.service` |

These scripts control the registered systemd service. For a monitor started
directly in a terminal, use Ctrl+C there. Existing oneshot/tmux units may show
`active (exited)` after Python has ended; status is systemd state, not a process
health check.

Offline shell checks, with fake sudo/systemctl only:

```bash
python daemon/test_monitor_scripts.py
```
