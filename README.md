# LH2 leak monitor

Monitors `Alert_H2leak` for a **0 → 1** transition and runs the configured
shutdown sequence.

## Start

Use Python 3.11+ and the existing equipment-host environment:

```bash
python monitor.py
```

This is **live operation**, including equipment commands and configured Discord
notifications. If the alert is already 1 at startup, choose `q` to exit or Enter
to wait for a new 0 → 1. Without a terminal, it waits.

Live operation requires `caen_libs` and its native library, trusted SSH keys for
the Pis, and passwordless `sudo -n` permission for `uhubctl`.

## Settings

Edit [config.toml](config.toml):

| Section | Settings |
| --- | --- |
| `targets` | Equipment names, IP addresses, ports, and channels |
| `sequence` | Shutdown order |
| `monitor` | Status-file path, polling interval, wait time, log path |
| `retry` | Attempt timeout, retry interval, maximum attempts |
| `triggers` | Paths for operator control files |

Show the complete order and addresses without operating equipment:

```bash
python monitor.py --show-plan
```

Defaults: **60 minutes** after preceding shutdown attempts finish; **10 seconds**
per attempt, **2 seconds** between attempts, **3 attempts** total. T0 is removed;
Kikusui .42 is last. The two mass-flow devices must both pass read-only checks
before closing outlet, then inlet. Remaining closure failures are recorded as
critical; independent equipment shutdowns continue.

Store CAEN credentials and the optional Discord webhook locally:

```bash
cp .env.example .env
chmod 600 .env
```

Fill in `CAEN_USERNAME`, `CAEN_PASSWORD`, and optionally `DISCORD_WEBHOOK_URL`.
Loading `.env` requires `python-dotenv`; exported environment variables also work.
Missing CAEN credentials stop live startup before any equipment commands.
An empty Discord URL disables notifications. **Never commit the filled-in file.**

## During the wait

Create a control file in another terminal:

| Command | Effect |
| --- | --- |
| `touch /tmp/skip.now` | Proceed to final power-off after 5 seconds |
| `touch /tmp/cancel.now` | Cancel remaining steps and exit the monitor |
| `touch /tmp/extend.now` | Reset the wait to 60 minutes |

These controls apply only during the wait. A recovered alert does not cancel a
started sequence. The program exits after completion or cancellation.
Unreadable/invalid input produces a warning and monitoring continues; file age
is not checked. Logs are saved to `logs/monitor.log`.

## Offline testing

**Use `--simulate` for testing.** Virtual time, devices, inputs, and notifications
are used; no equipment or Discord communication occurs.

```bash
python monitor.py --simulate
python monitor.py --simulate --scenario debug/scenarios/failure.json
python monitor.py --simulate --scenario debug/scenarios/cancel.json
python -m tests
```

The test runner blocks real networking, subprocess creation, and CAEN-library
loading. Real equipment behavior has not been validated by these tests.

`start.sh` is a foreground **live** launcher for the equipment host. An example
systemd unit is provided in [lh2_monitor.service](lh2_monitor.service); it is not
installed automatically. Credentials, logs, runtime files, virtual environments,
and `config.local.toml` are excluded from Git; an optional local configuration
can be selected with `--config config.local.toml`.
