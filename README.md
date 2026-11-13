# LH2 leak monitor

The equipment inventory and shutdown order are defined in [config.toml](config.toml).
The monitor detects a valid `Alert_H2leak: 0 -> 1` transition, latches the shutdown
sequence, and exits after completion or operator cancellation.

## Inspect and simulate

Python 3.11+ is required. These commands do not contact equipment or Discord:

```bash
python3 monitor.py --show-plan
python3 monitor.py
python3 monitor.py --simulate --scenario debug/scenarios/failure.json
python3 monitor.py --simulate --scenario debug/scenarios/cancel.json
python3 monitor.py --simulate --scenario debug/scenarios/startup-alert.json
python3 -m tests
```

Running `monitor.py` without a mode selects the virtual simulation. The simulator
uses an in-memory clock, equipment responses, alert values, trigger events, and
notification recorder. It does not read the live status file, read `.env`, consume
real trigger files, start child processes, load the CAEN library, or send anything.
A simulated hour passes immediately. Scenario event times are virtual seconds
since monitor startup, not since the wait begins. A finite scenario must supply
enough status values to reach a transition or an explicit startup exit.

The offline test runner installs an audit guard **before importing the application**.
It rejects real socket creation/connections, subprocess creation, native-library
loading, and access to production status/trigger paths. Hardware and process
adapter tests use in-memory fakes/mocks. They do not run the real worker, SSH,
legacy equipment scripts, or HTTP clients against any endpoint, including localhost.

## Shutdown order

All IP addresses below are in `192.168.20.0/24`. The defaults match the reviewed
monitor2 working-tree settings, with the agreed changes: remove T0, wait 60 minutes,
and turn off Kikusui .42 **last**.

| Order | Target | Address / scope | Operation |
| --- | --- | --- | --- |
| 1 | HV controller | .12:8000, ports 0–3 | TURN_OFF, in port order |
| 2 | HV controller | .13:8000, ports 0–3 | TURN_OFF, in port order |
| 3 | HV controller | .91:8000, ports 0–3 | TURN_OFF, in port order |
| 4 | Outlet + inlet mass-flow pair | .17:4196 → .14:4001, address 1 | Check both connections; close outlet, then inlet |
| 5 | Kikusui BLC2 Vth | .45:5025 | Output OFF |
| 6 | CAEN Chamber | .51, slot 4, all channels | Discover channels; OFF in channel order |
| 7 | Wait | 60 minutes | Starts after all preceding attempts finish |
| 8 | Pi .12 USB power | .12, hub 1-1, ports 1–4 | Power OFF, in port order |
| 9 | Pi .13 USB power | .13, hub 1-1, ports 1–4 | Power OFF, in port order |
| 10 | Kikusui BLC2 | .42:5025 | Output OFF, **last** |

Each equipment attempt has a 10-second limit, with 2 seconds between attempts and
at most 3 attempts **including the first**. Each HV port, USB port, and discovered
CAEN channel has its own budget. Failed targets are recorded and other targets
are still attempted. CAEN inventory discovery is also bounded; if discovery fails,
the slot cannot be enumerated and is reported as failed.

The mass-flow pair owns its retries internally. Both TCP connections and read-only
valve-status replies are checked, and the connections are retained before either
close command is sent. If either access check fails all
three attempts, **neither close command is sent**. Once closure starts, each valve
gets its own close-command retry budget, in outlet → inlet order. A successful
side is not repeated merely because the other side failed. A remaining failure is
reported as a **critical incomplete/possibly partial closure**, then the remaining
equipment sequence continues. There is no automatic valve reopening.

Access checks and retries cannot make two independent devices atomic. An
acknowledgment lost after a write can leave the result unknown. Protocol
acknowledgments do not verify physical flow or physical voltage. Log summaries
distinguish command completion from physical verification.

## Alert and operator behavior

- At startup, an alert already at 1 produces a warning, without shutdown commands.
  In an interactive terminal, choose exit (`q`) or wait (Enter).
  Without a terminal, the default is to wait for 0 and then a new 0 → 1 transition.
  `--startup-alert wait` / `exit` can select the behavior explicitly.
- Missing/unreadable files, missing/duplicate keys, and values other than exactly
  0 or 1 produce warnings. Monitoring waits for valid input and preserves the last
  valid value. File age is intentionally **not** checked.
- Once 0 → 1 is detected, a later recovery to 0 does not cancel shutdown.
- The full 60-minute wait starts after the preceding shutdown attempts finish,
  including exhausted failures. Elapsed time uses a monotonic clock.
- Cancel/skip/extend requests are accepted only in the wait phase:

| Trigger file | Effect |
| --- | --- |
| `/tmp/cancel.now` | Cancel all remaining power-off steps and exit the monitor |
| `/tmp/skip.now` | Advance to final power-off after a 5-second grace period |
| `/tmp/extend.now` | Reset the remaining wait to a full 60 minutes |

Cancellation also works in the skip grace period. Old trigger files are removed
at live startup, after acquiring the monitor lock. Requests are not checked while
equipment operations are running. The monitor exits after completion or cancellation;
an operator must restart it to monitor again.

Exit codes: 0 = completed without reported command errors (or startup exit),
1 = equipment/runtime failure, 2 = operator cancellation/configuration error,
130 = keyboard interruption. Errors before cancellation remain in the final report.

## Live operation

**The commands above are for virtual verification. Live operation is separate.**
`--live` enables real equipment commands and configured Discord delivery.
`start.sh` explicitly selects live mode and the noninteractive startup policy;
do not run it as a test.

Python uses one bounded worker process per equipment attempt. On timeout, the
worker and its local descendants are killed; the result is logged as unknown.
Workers use the same Python interpreter as the monitor. The CAEN dependency is
loaded only in live CAEN workers. Required on the equipment host: `caen_libs`
with its native library, SSH, and `uhubctl` on the target Pis.
SSH requires pre-established trusted host keys and key authentication; remote
`sudo -n` must permit `uhubctl` without a password.

Optional Discord configuration is `DISCORD_WEBHOOK_URL` in the environment or
`.env` (`python-dotenv` is needed for `.env` loading). Missing configuration and
delivery errors do not disable monitoring. Delivery is asynchronous and bounded
waiting at exit cannot block any equipment operation. It remains best-effort.

Logs append to `logs/monitor.log`; no screen-clearing hides earlier errors.
A process lock prevents duplicate live monitors using the same configuration.

An example foreground systemd unit is provided in [lh2_monitor.service](lh2_monitor.service).
It supervises the Python process and deliberately does not restart completed or
canceled sequences. It replaces the old oneshot/tmux service arrangement when
explicitly deployed. Nothing installs or restarts the service automatically.

The compatibility `turn_off_hv.py` tool also simulates by default and requires
`--live` for actual commands. Other existing standalone maintenance tools are
not used by the new monitor and retain their own behavior.

## Code layout

- `config.toml`: addresses, target names, retry policy, triggers, and ordered plan.
- `lh2_monitor/engine.py`: alert handling, retries, wait controls, and sequence lifecycle.
- `lh2_monitor/hardware.py`: live device protocols, including paired mass-flow handling.
- `lh2_monitor/adapters.py` and `worker.py`: bounded subprocesses, input, logging, notifications.
- `lh2_monitor/simulation.py`: entirely virtual substitutes.
- `tests/`: offline regression and fault-injection tests.

The mass-flow protocol implementation follows the deployed MQV0002 scripts;
virtual protocol tests use fixed packets, error replies, EOF, and trickling replies.
Real device behavior and the vendor CAEN runtime have **not** been tested by this
offline validation.
