# Monitor service controls

These scripts manage the existing **systemd service**. They do not install or
rewrite service definitions, and they do not store passwords. Run them in your
terminal; `sudo` asks for your password when required.

From an individual tool directory:

```bash
./daemon/status_monitor.sh
./daemon/stop_monitor.sh
./daemon/disable_monitor.sh
```

| Script | Effect |
| --- | --- |
| `start_monitor.sh` | Start the registered monitor now |
| `stop_monitor.sh` | Stop now; leave boot startup unchanged |
| `restart_monitor.sh` | Stop and start the registered monitor |
| `status_monitor.sh` | Show systemd state; no sudo |
| `enable_monitor.sh` | Enable boot startup; do not start now |
| `disable_monitor.sh` | Stop now **and** disable boot startup |

**Start/restart launch real monitoring and may lead to equipment commands.**
Use neither command as a test.

The unit is inferred from the tool directory name:

| Directory / explicit target | Unit |
| --- | --- |
| `lh2_leak_monitor` / `lh2` | `lh2_monitor.service` |
| `BLC_hv_monitoring` / `blc` | `blc_monitor.service` |
| `CVC_hv_monitoring` / `ftof` | `ftof_monitor.service` |
| `trip_monitor` / `trip` | `trip_monitor.service` |

From the parent `monitor-tools` directory, specify the target:

```bash
./daemon/disable_monitor.sh lh2
./daemon/status_monitor.sh lh2
./daemon/monitor.sh status blc
```

The generic interface is `./daemon/monitor.sh ACTION [TARGET]`.
Service definitions decide which checkout is started. Existing tmux/oneshot
units can show `active (exited)` after the Python process has ended; this is
systemd state, not proof that monitoring is alive. Use restart only when you
intend to relaunch it. Errors from sudo/systemctl are returned to the terminal.

For monitoring started directly with `python monitor.py`, use Ctrl+C in that
terminal. These scripts operate the registered service.

Offline shell checks (fake sudo/systemctl only):

```bash
python daemon/test_monitor_scripts.py
```
