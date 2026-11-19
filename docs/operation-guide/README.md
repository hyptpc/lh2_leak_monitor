# LH2 monitor: visual operating guide

[日本語版 / Japanese](README.ja.md) · [Project README](../../README.md)

Read the diagrams from top to bottom. They describe the current
[config.toml](../../config/config.toml), [monitor entry point](../../app/monitor.py), and
[engine](../../app/lh2_monitor/engine.py). Configuration changes can change the order,
addresses, and timing below.

**The monitor's normal startup is LIVE. The separate preflight checker never
starts monitoring, sends shutdown commands, or sends Discord notifications.**
Preflight is an operator step; it is not automatically run by monitor.py.

This installation runs only on monitor2 at
/home/sks/monitor_tools/lh2_leak_monitor. Use daemon/start.sh for live startup;
it always uses /home/sks/.venv/bin/python and the fixed project directory.
No activate step is required. The installed systemd unit must use the prepared
local Type=exec definition, not the old oneshot/tmux launcher.

## 1. Before starting: preflight

On monitor2, from the shared project:

~~~bash
cd /home/sks/monitor_tools/lh2_leak_monitor
mkdir -p logs

# Default: local checks AND read-only communication with every configured target.
./preflight.sh

# Optional: local imports, configuration, credentials and input only; no equipment I/O.
./preflight.sh --local

# Optional machine-readable report; this directory is excluded from Git.
./preflight.sh --json > logs/preflight.json
~~~

~~~mermaid
flowchart TD
    P["Run preflight.py with the monitor's Python"] --> C["Validate config and required credentials<br/>Import monitor modules and CAEN native library"]
    C --> I["Read Alert_H2leak and show input-file age"]
    I --> D{"Equipment checks enabled?<br/>Default yes; --local skips them"}
    D -->|No| L["Local checks only<br/>Equipment communication: SKIP"]
    D -->|Yes| Q["Read-only checks of all configured targets<br/>See the table below"]
    L --> R["Review PASS / WARN / FAIL / SKIP"]
    Q --> R
    R --> F{"Any FAIL?"}
    F -->|Yes| X["Resolve the failure before choosing to start LIVE"]
    F -->|No| W["Review WARN and SKIP too<br/>PASS does not guarantee a future shutdown"]
    X --> N["Checker exits; monitor stays untouched"]
    W --> N
    classDef check fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef stop fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class C,I,Q,R check;
    class X stop;
~~~

| Check | Read-only operation | What remains untested |
| --- | --- | --- |
| Python / native imports | Imports monitor modules and caen_libs.caenhvwrapper in a bounded subprocess | Later code paths and physical behavior |
| Credentials | Checks presence of CAEN username/password; never prints values | Authentication is tested in the default equipment-check mode |
| Alert input | Reads the configured file, validates the flag, and reports file age | Producer health; the monitor itself does not reject stale input |
| HV1 (.12), HV2 (.13), monitor2 HV3 (.90) | GET OpenAPI, queue status, and port labels; checks configured ports and the shutdown API route | No POST, queued command, serial response test, or physical HV verification |
| Outlet / inlet mass flow | Read-only CPL valve-status request and checksum validation, independently for both valves | No close command or physical flow measurement |
| Kikusui .45 / .42 | OUTP? query | No OUTP OFF command or physical output measurement |
| CAEN BLC2 .51 | Read channel names across occupied slots and list matches containing BLC2 | No channel parameter writes |
| Pi .12 / .13 USB | SSH authentication/known-host check, executable check, and sudo -n -l permission inspection for each configured command | No uhubctl execution, hub query, or USB power change |
| Discord | Always SKIP; only reports whether a URL is configured, without showing it | No webhook request or delivery test |

Each probe has a deadline; the CAEN/native-library and SSH probes cannot block
the checker indefinitely. The default I/O timeout comes from config.toml
(currently 10 seconds); --timeout 5 overrides it for the checker only. Checks
continue after a failure so that the report includes the other targets.

**Exit codes:** 0 means no FAIL in the selected scope; 1 means at least one
FAIL. WARN includes an already-active alert or an input file older than the
checker's advisory threshold (max of 10 seconds and three polling intervals).
--local does not certify equipment communication. --devices remains available as an explicit alias for the default mode. Authentication
or permission failures are reported, not repaired automatically.

Using /home/sks/.venv/bin/python explicitly selects that virtual environment even
when activate has not been sourced. Import failures and equipment communication
failures are reported separately; activating the local environment does not
start an API server on a remote equipment host.

## 2. When the LIVE monitor starts

~~~mermaid
flowchart TD
    A["Start monitor.py<br/>Default mode: LIVE"] --> B["Validate config; acquire single-monitor lock"]
    B --> C["Consume old accepted control requests<br/>Load local .env; existing environment wins"]
    C --> K{"Required CAEN credentials present?"}
    K -->|No| E["Log startup error; exit<br/>No equipment commands"]
    K -->|Yes| S["Read Alert_H2leak every 1 second"]
    S --> V{"Exactly one valid 0 or 1?"}
    V -->|No| BAD["Warn and keep last valid state<br/>Wait for a valid input"]
    BAD --> S
    V -->|Yes| FIRST{"First valid value?"}
    FIRST -->|Yes| ONE{"Value already 1?"}
    ONE -->|No: 0| S
    ONE -->|Yes| CHOICE{"Startup choice"}
    CHOICE -->|q / exit| EXIT["Exit without equipment commands"]
    CHOICE -->|Enter / wait / no terminal| RESET["Wait for 0, then a NEW 0 to 1 transition"]
    RESET --> S
    FIRST -->|No| EDGE{"Last valid value 0<br/>and new value 1?"}
    EDGE -->|No| S
    EDGE -->|Yes| RUN["Latch the shutdown sequence<br/>Continue to section 3"]
    classDef alert fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    class RUN alert;
    class S,BAD,RESET wait;
~~~

The default input is
/home/sks/share/monitor-tmp/H2tgtPresentStatus.txt, key Alert_H2leak.
Missing, unreadable, malformed, or duplicate values do not trigger a shutdown;
they also do not erase the last valid state. An unchanged valid 0 does not
repeatedly trigger anything.

.env belongs at the project root (above app/). It is read at LIVE startup if python-dotenv is
available; changes require a new process. Exported environment variables take
precedence. Missing CAEN credentials stop startup. Missing Discord configuration
disables notifications but does not disable monitoring. Never commit .env,
real credentials, or webhook URLs.

## 3. After a new 0 → 1 transition

**The sequence runs once and then the Python process exits. Recovery to 0 does
not cancel it: the input is no longer polled after the sequence is latched.**

~~~mermaid
flowchart TD
    A["Alert_H2leak: 0 to 1"] --> N1["Queue Discord notification<br/>Shutdown sequence started"]
    N1 --> H12["1. HV1 .12 ports 1, 2, 3: TURN_OFF<br/>BAC, KVC-1, KVC-2"]
    H12 --> H13["2. HV2 .13 ports 0, 1, 2, 3: TURN_OFF<br/>HTOF Up/Down/High, T1"]
    H13 --> H90["3. monitor2 .90:8000 ports 0, 1 and 2: TURN_OFF<br/>T2, SCH and BH2"]
    H90 --> MF["4. Mass-flow pair<br/>Check BOTH connections, then outlet to inlet<br/>See section 5"]
    MF --> K45["5. BLC2Vth, Kikusui .45: OUTP OFF<br/>Query output state"]
    K45 --> CA["6. CAEN .51: names containing BLC2<br/>Batch OFF per matching slot; read back OFF<br/>Currently slot 4, ch 4-7; exclude BLC1/T0"]
    CA --> WAIT["7. Wait 60 minutes<br/>Timer starts AFTER the preceding attempts finish"]
    WAIT --> CONT{"Wait completed or skipped?<br/>See section 4"}
    CONT -->|Canceled| CANCEL["Skip ALL remaining equipment actions<br/>Earlier actions are not undone"]
    CONT -->|Yes| U12["8. Pi .12: USB ports 1, 2, 3, 4 OFF"]
    U12 --> U13["9. Pi .13: USB ports 1, 2, 3, 4 OFF"]
    U13 --> K42["10. LAST: shutdown tap 5V, Kikusui .42 OFF<br/>Query output state"]
    K42 --> SUM["Queue final Discord summary<br/>Completed / completed with errors"]
    CANCEL --> CS["Queue final Discord summary<br/>Canceled, including any earlier errors"]
    SUM --> EXIT["Log result; exit Python process"]
    CS --> EXIT
    classDef action fill:#fff0d9,stroke:#a66a17,color:#51350c;
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef notify fill:#eee7ff,stroke:#7656a5,color:#35234f;
    class H12,H13,H90,MF,K45,CA,U12,U13,K42 action;
    class WAIT wait;
    class N1,SUM,CS notify;
~~~

| Group | Current destination |
| --- | --- |
| HV1 | 192.168.20.12:8000; port 1 BAC, 2 KVC-1, 3 KVC-2 |
| HV2 | 192.168.20.13:8000; port 0 HTOF Up, 1 HTOF Down, 2 HTOF High, 3 T1 |
| monitor2 HV3 | 192.168.20.90:8000; port 0 T2, 1 SCH, 2 BH2 (55V) |
| Mass-flow pair | Outlet 192.168.20.17:4196 → inlet 192.168.20.14:4001; device address 1 |
| BLC2Vth (first Kikusui) | 192.168.20.45:5025 |
| CAEN BLC2 | 192.168.20.51; name match BLC2 across slots, currently slot 4 ch 4–7 |
| USB power | SSH to Pi .12, then .13; hub 1-1, ports 1–4 |
| Shutdown tap 5V (last Kikusui) | 192.168.20.42:5025 |

The obsolete .91 HV target is removed. Monitor2 ports 0 T2, 1 SCH and 2 BH2
are included; its separate HV4 API (:8001) is not part of this HV group.
Kikusui .45 is BLC2Vth. Kikusui .42 is the 5 V supply feeding the shutdown tap
and remains the last step after USB power-off.

CAEN reads channel names in all occupied slots and selects names containing
BLC2 (case-insensitive). The confirmed current selection is slot 4 channels
4–7: BLC2a-cat, BLC2a-pot, BLC2b-cat, BLC2b-pot. BLC1 (slot 4 ch 0–3)
and T0 (slot 8) are excluded. Names and the selected layout are checked again
before OFF; no match or changed names/layout causes an error instead of a write.

Each selected slot receives ONE batch Pw=0 (power OFF) call for its matching
channels, using the same power-switch parameter as monitor3's chamber_off.py.
V0Set is never written. Different slots are processed in order, not atomically
or at precisely the same time. The selected channels' Pw values are read back,
allowing up to 3 seconds (or half the attempt timeout) for OFF. Unconfirmed OFF
is an error; the voltage setpoint is preserved and physical discharge is not verified.

Each HV/USB port and selected CAEN slot batch has its own retry budget: currently
**3 attempts total**, a **10-second attempt timeout**, and **2 seconds between
attempts**. A failed name inventory prevents CAEN OFF attempts; a failed selected
slot does not prevent attempts on other selected slots. Errors are recorded,
and attempts continue with the remaining configured equipment. A timeout means
the equipment result is unknown, not that the command definitely had no effect.

After a timeout, the worker process group is sent SIGKILL and cleanup waits at
most 2 additional seconds. If cleanup cannot finish, the monitor records UNKNOWN
and continues its existing retry/remaining-target policy. A daemon reaper waits
outside the control loop; it does not delay monitoring or program exit. The
same bounded cleanup is used by preflight. Kernel-stuck processes cannot be
forced to exit immediately, and remote effects may remain unknown.

Discord uses background, best-effort delivery. Failure or absence of the webhook
does not stop equipment shutdown. Current code sends an initial and a final
summary; it does not send a separate recovery notification.

A successful command or acknowledgment is **not physical verification that every
HV, valve, and power output is off**. Process completion or cancellation ends this
one monitoring run. Whether it restarts depends on the installed service unit;
see [service management](../../README.md#service-control), not just the repository template.

## 4. During the 60-minute wait

Controls are accepted only during the wait, including the skip grace period.
They do not undo preceding actions or turn anything back on.

~~~mermaid
flowchart TD
    W["Wait loop"] --> C{"cancel request?"}
    C -->|Yes| STOP["Cancel remaining steps<br/>Final notification; exit"]
    C -->|No| S{"skip request?"}
    S -->|Yes| G["5-second grace period"]
    G --> GC{"cancel before or at grace deadline?"}
    GC -->|Yes| STOP
    GC -->|No| FINAL["Run final USB steps, then shutdown tap 5V (.42)"]
    S -->|No| E{"extend request?"}
    E -->|Yes| RESET["Reset to a FULL 60 minutes from now"]
    RESET --> T{"Deadline reached?"}
    E -->|No| T
    T -->|Yes| FINAL
    T -->|No| PAUSE["Sleep up to 1 second"]
    PAUSE --> W
    classDef wait fill:#e8f1ff,stroke:#3568a8,color:#182c44;
    classDef stop fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class W,G,RESET,PAUSE wait;
    class STOP stop;
~~~

| From another terminal on the monitor host | Result |
| --- | --- |
| touch /tmp/cancel.now | Cancel the remaining steps; highest priority |
| touch /tmp/skip.now | Start the final steps after 5 seconds; cancel still works during that grace period |
| touch /tmp/extend.now | Reset the normal wait to 60 minutes from the request |
| No request | Continue automatically at the wait deadline |

If requests coexist, priority is **cancel → skip → extend**. Extend is not
processed after skip has entered its 5-second grace period. Cancellation is also
checked at the normal/grace deadline boundary.

Normally use regular files owned by the monitor user (sks); root-owned regular
files are also accepted. Symlinks, special files, and other owners are ignored.
If a trusted request cannot be removed, its inode and modification/change times
are remembered: it is honored once, not repeatedly. Touch it again as its owner
to create a new request. Inspection errors are warned and ignored, without
aborting the remaining shutdown steps. Old accepted requests are consumed at
LIVE startup, including retained files, so do not pre-create a request expecting
it to fire after startup.

## 5. Mass-flow pair: the important branch

~~~mermaid
flowchart TD
    O["Read-only outlet access/status check"] --> I["Read-only inlet access/status check"]
    I --> BOTH{"BOTH checks succeeded?"}
    BOTH -->|No| NONE["Send NEITHER close command<br/>Record failure; continue to BLC2Vth (.45)"]
    BOTH -->|Yes| CLOSEO["Attempt OUTLET close; validate acknowledgment"]
    CLOSEO --> CLOSEI["Attempt INLET close even if outlet close failed"]
    CLOSEI --> GOOD{"Both close commands acknowledged?"}
    GOOD -->|Yes| DONE["Record command-level success<br/>Continue to BLC2Vth (.45)"]
    GOOD -->|No| PART["Record CRITICAL incomplete closure<br/>One valve may be closed and the other not<br/>Continue to BLC2Vth (.45)"]
    classDef error fill:#ffe7e7,stroke:#b33a3a,color:#501919;
    class NONE,PART error;
~~~

Each access check and each close operation has its own retry/timeout budget.
The pair is not retried wholesale, so an already-successful side is not repeated
just because the other side failed. There is no atomic two-valve guarantee and
no automatic reopen/rollback.

## Source map

| Behavior | Source |
| --- | --- |
| Addresses, timing, ordered targets | [config.toml](../../config/config.toml) |
| LIVE startup, environment, lock, exit | [monitor.py](../../app/monitor.py) |
| Alert edge, retries, wait controls, summary | [engine.py](../../app/lh2_monitor/engine.py) |
| Status input, control files, notification delivery | [adapters.py](../../app/lh2_monitor/adapters.py) |
| Equipment protocol operations | [hardware.py](../../app/lh2_monitor/hardware.py) |
| Read-only readiness checks | [preflight.py](../../app/lh2_monitor/preflight.py) |
| Bounded worker cleanup | [processes.py](../../app/lh2_monitor/processes.py) |
| Entirely virtual behavior | [simulation.py](../../app/lh2_monitor/simulation.py) |

For visual/logic experiments without real devices, use the explicit --simulate
mode described in the [project README](../../README.md#offline-testing). Unlike the
default equipment preflight, simulation contacts no equipment at all.
