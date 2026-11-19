# LH2 leak monitor

Watches `Alert_H2leak` for a **0 → 1** transition and runs the configured
shutdown sequence.

This installation is dedicated to monitor2 at the fixed local path
/home/sks/monitor_tools/lh2_leak_monitor. daemon/start.sh selects /home/sks/.venv/bin/python
through the installed systemd service, so activation and the caller's working directory
do not affect normal startup. Code, config, credentials, logs and the process
lock belong here. The shared checkout is not an execution location; the configured
alert input still depends on the shared mount.

## Start

Use the equipment host's Python environment:

```bash
cd /home/sks/monitor_tools/lh2_leak_monitor
./daemon/start.sh
```

This is **normal live operation**: equipment commands and configured Discord
notifications are enabled. This launcher waits for a new 0 → 1 if the alert is
already 1 at startup; it does not immediately send shutdown commands. The raw
app/monitor.py CLI also supports an interactive exit/wait choice.

Once triggered, recovery to 0 does not cancel the sequence. The program exits
after completion or operator cancellation; restart it to monitor again.

## Configuration

Edit [config.toml](config/config.toml). Equipment addresses and order are kept here.

| Section | Contents |
| --- | --- |
| `targets` | Equipment names, IP addresses, ports, and channels |
| `sequence` | Shutdown order |
| `monitor` | Input-file path, polling interval, wait duration, log path |
| `retry` | Attempt timeout, retry interval, maximum attempts |
| `triggers` | Operator control-file paths |

To display the full order and addresses **without operating equipment**:

```bash
./plan.sh
```

Default order: **HV → mass-flow pair → Kikusui .45 → CAEN Chamber →
60-minute wait → USB power → Kikusui .42**. The wait starts after the preceding
shutdown attempts finish. Each equipment attempt has a **10-second** limit,
with **2 seconds** between attempts and **3 attempts** total.

Both mass-flow devices must pass read-only checks before closing outlet, then
inlet. If either check fails, neither close command is sent. A failure after
closure starts is reported as a critical incomplete closure; other equipment
shutdowns continue. Two independent valves cannot be guaranteed to close atomically.

## Controls during the wait

Create a file from another terminal:

| Command | Effect |
| --- | --- |
| `touch /tmp/skip.now` | Proceed to final power-off after 5 seconds |
| `touch /tmp/cancel.now` | Cancel remaining steps and exit the monitor |
| `touch /tmp/extend.now` | Reset the wait to 60 minutes |

These controls apply only during the wait. Normally create them as the monitor
user (sks), using regular files. Root-owned regular files are also accepted;
other owners, symlinks, and special files are ignored with a warning. A trusted
request that cannot be removed is accepted once and remembered by inode and
change times, so it cannot repeatedly extend the wait. Touching it again as its
owner creates a new request. Old accepted requests are consumed at startup.
Inspection errors are warned and ignored without aborting remaining shutdown work. If no control file is created, final power-off proceeds automatically
after 60 minutes. Logs are saved to `logs/monitor.log`.

After a worker times out, its process group is sent SIGKILL. Cleanup waits at
most 2 additional seconds. If the worker cannot be reaped promptly, its result
remains UNKNOWN and the monitor proceeds with its normal retry/remaining-target
policy; a background daemon only reaps the process later. The same cleanup
limit applies to preflight probes. This is not a guarantee that a remote command
did or did not take effect.

## First-time setup

Python 3.11+, `caen_libs` with its native library, and the equipment host's SSH
access are required. The Pis need trusted host keys and passwordless `sudo -n`
permission for `uhubctl`.

Create a local credential file if one does not already exist:

```bash
cp -n .env.example .env
chmod 600 .env
```

Set `CAEN_USERNAME` and `CAEN_PASSWORD`. Optionally set `DISCORD_WEBHOOK_URL`;
leave it empty to disable notifications. Loading `.env` requires `python-dotenv`;
exported environment variables also work. Missing CAEN credentials stop startup
before equipment commands. **Never commit `.env` or actual credentials.**

## Offline testing

**Add `--simulate` for tests.** It uses virtual devices, inputs, time, and
notifications; it does not contact equipment or Discord or read live input files.

```bash
./debug/simulate.sh
./debug/simulate.sh --scenario debug/scenarios/failure.json
./debug/simulate.sh --scenario debug/scenarios/cancel.json
/home/sks/.venv/bin/python -m tests
```

The test runner blocks real networking, subprocess creation, and CAEN-library
loading. These tests do not verify real equipment behavior.

## Service control

Top-level scripts provide preflight checks and plan display. Simulation is under debug/. Service controls
are under daemon/. All scripts use this fixed local
installation; activating a virtual environment is unnecessary.

| Script | Purpose |
| --- | --- |
| `./preflight.sh` | Read-only checks of imports, configuration and all configured devices; skips Discord |
| `./preflight.sh --local` | Local checks only, without device communication |
| `./plan.sh` | Print the configured shutdown plan without I/O |
| `./debug/simulate.sh` | Entirely virtual simulation, without equipment or Discord |
| `./daemon/install-service.sh` | Verify and install the unit, then reload systemd; does not start or enable |
| `./daemon/start.sh` | Start live monitoring through systemd; does not enable boot startup |
| `./daemon/stop.sh` | Stop monitoring; does not restore equipment power or disable boot startup |
| `./daemon/status.sh` | Show service state and recent journal messages |

After this directory reorganization, run `./daemon/install-service.sh` again before
starting. sudo will ask for your password. No backup copies are created.
`start.sh` refuses an outdated installed definition. The service remains
`Restart=no`: completion or cancellation ends this monitoring run.
To inspect additional logs: `journalctl -u lh2_monitor.service -n 100 --no-pager`.
`status.sh` returns systemctl's nonzero exit status when inactive.

## Directory layout

```text
README.md, *.sh       Guide, preflight checks and plan display
monitor.py           Link to app/monitor.py (foreground live entry point)
.env                 Local credentials (ignored by Git; mode 600)
app/                 Python entry points, package and compatibility tools
config/config.toml   Equipment addresses, sequence and timing
docs/operation-guide/ English and Japanese flowcharts
daemon/    LH2 service start, stop, status and installation
tools/port_on.sh     Legacy manual USB helper (not used by monitoring)
tests/, debug/       Offline tests, simulation launcher and scenarios
logs/, runtime/      Generated logs and lock files (ignored by Git)
```

Use this tool's daemon/ directory for service controls and its service template.
The only LH2 service template is
[daemon/units/lh2_monitor.service](daemon/units/lh2_monitor.service).
The top-level [monitor.py](monitor.py) links to [app/monitor.py](app/monitor.py).
Run `/home/sks/.venv/bin/python monitor.py` for foreground live monitoring.
Use `--show-plan` or `--simulate` for offline checks. The service keeps using
app/monitor.py directly; its definition does not change for this link.
Do not run compatibility tools to test readiness: some directly control equipment.

Visual guides: [English](docs/operation-guide/README.md) /
[日本語](docs/operation-guide/README.ja.md).
Unreadable or invalid alert input warns and monitoring continues; runtime file age
is not checked. Credentials, logs, runtime files and virtual environments are
excluded from Git. The alert input still depends on the shared mount.

Preflight uses green PASS, yellow WARN, red FAIL and cyan SKIP on terminals.
Redirected output and JSON remain plain; use `--color always` / `--color never`
to override text output, or set `NO_COLOR` to disable automatic color.
