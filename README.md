# LH2 leak monitor

Watches `Alert_H2leak` for a **0 → 1** transition and runs the configured
shutdown sequence.

## Start

Use the equipment host's Python environment:

```bash
cd /home/sks/share/monitor-tools/lh2_leak_monitor
python monitor.py
```

This is **normal live operation**: equipment commands and configured Discord
notifications are enabled. If the alert is already 1 at startup, choose `q` to
exit or Enter to wait for a new 0 → 1. Without a terminal, it waits.

Once triggered, recovery to 0 does not cancel the sequence. The program exits
after completion or operator cancellation; restart it to monitor again.

## Configuration

Edit [config.toml](config.toml). Equipment addresses and order are kept here.

| Section | Contents |
| --- | --- |
| `targets` | Equipment names, IP addresses, ports, and channels |
| `sequence` | Shutdown order |
| `monitor` | Input-file path, polling interval, wait duration, log path |
| `retry` | Attempt timeout, retry interval, maximum attempts |
| `triggers` | Operator control-file paths |

To display the full order and addresses **without operating equipment**:

```bash
python monitor.py --show-plan
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

These controls apply only during the wait. Old control files are cleared at
startup. If no control file is created, final power-off proceeds automatically
after 60 minutes. Logs are saved to `logs/monitor.log`.

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
python monitor.py --simulate
python monitor.py --simulate --scenario debug/scenarios/failure.json
python monitor.py --simulate --scenario debug/scenarios/cancel.json
python -m tests
```

The test runner blocks real networking, subprocess creation, and CAEN-library
loading. These tests do not verify real equipment behavior.

`start.sh` is a foreground **live** launcher; [lh2_monitor.service](lh2_monitor.service)
is an optional systemd unit, not installed automatically. Unreadable or invalid
input produces a warning and monitoring continues; file age is not checked.
Credentials, logs, runtime files, and virtual environments are excluded from Git.

## Service control

Use [daemon/](daemon/README.md): `start.sh` starts the service and enables automatic
startup, `stop.sh` stops it and disables automatic startup, and `monitor.sh` shows
its state. sudo asks for your password.

```bash
./daemon/monitor.sh
./daemon/stop.sh
```

Before first use of the updated units, stop the target and install its definition
with `sudo /usr/bin/python3 daemon/configure.py TARGET`. Installation backs up old
units and reloads systemd without starting anything. See the daemon README for
all targets and the bounded automatic-restart policy.
