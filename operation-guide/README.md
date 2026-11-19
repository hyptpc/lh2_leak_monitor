# LH2 monitor: visual operating guide

[日本語版 / Japanese](README.ja.md) · [Project README](../README.md)

Read the diagrams from top to bottom. They describe the current
[config.toml](../config.toml), [monitor entry point](../monitor.py), and
[engine](../lh2_monitor/engine.py). Configuration changes can change the order,
addresses, and timing below.

**The monitor's normal startup is LIVE. The separate preflight checker never
starts monitoring, sends shutdown commands, or sends Discord notifications.**
Preflight is an operator step; it is not automatically run by monitor.py.

## 1. Before starting: preflight

On monitor2, from the shared project:

~~~bash
cd /home/sks/share/monitor-tools/lh2_leak_monitor

# Python/native-library imports, configuration, credentials, and status input.
# No equipment communication.
/home/sks/.venv/bin/python preflight.py

# Also query every configured equipment target, read-only.
/home/sks/.venv/bin/python preflight.py --devices

# Optional machine-readable report; this directory is excluded from Git.
/home/sks/.venv/bin/python preflight.py --devices --json > logs/preflight.json
~~~

~~~mermaid
flowchart TD
    P["Run preflight.py with the monitor's Python"] --> C["Validate config and required credentials<br/>Import monitor modules and CAEN native library"]
    C --> I["Read Alert_H2leak and show input-file age"]
    I --> D{"--devices supplied?"}
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
| Credentials | Checks presence of CAEN username/password; never prints values | Authentication is tested only with --devices |
| Alert input | Reads the configured file, validates the flag, and reports file age | Producer health; the monitor itself does not reject stale input |
| HV1 (.12), HV2 (.13), monitor2 HV3 (.90) | GET OpenAPI, queue status, and port labels; checks configured ports and the shutdown API route | No POST, queued command, serial response test, or physical HV verification |
| Outlet / inlet mass flow | Read-only CPL valve-status request and checksum validation, independently for both valves | No close command or physical flow measurement |
| Kikusui .45 / .42 | OUTP? query | No OUTP OFF command or physical output measurement |
| CAEN Chamber .51 | Login and read slot 4 channel inventory | No channel parameter writes |
| Pi .12 / .13 USB | SSH authentication/known-host check, executable check, and sudo -n -l permission inspection for each configured command | No uhubctl execution, hub query, or USB power change |
| Discord | Always SKIP; only reports whether a URL is configured, without showing it | No webhook request or delivery test |

Each probe has a deadline; the CAEN/native-library and SSH probes cannot block
the checker indefinitely. The default I/O timeout comes from config.toml
(currently 10 seconds); --timeout 5 overrides it for the checker only. Checks
continue after a failure so that the report includes the other targets.

**Exit codes:** 0 means no FAIL in the selected scope; 1 means at least one
FAIL. WARN includes an already-active alert or an input file older than the
checker's advisory threshold (max of 10 seconds and three polling intervals).
A local-only result does not certify equipment communication. Authentication
or permission failures are reported, not repaired automatically.

Using /home/sks/.venv/bin/python explicitly selects that virtual environment even
when activate has not been sourced. Import failures and equipment communication
failures are reported separately; activating the local environment does not
start an API server on a remote equipment host.

## 2. When the LIVE monitor starts

~~~mermaid
flowchart TD
    A["Start monitor.py<br/>Default mode: LIVE"] --> B["Validate config; acquire single-monitor lock"]
    B --> C["Clear old accepted control files<br/>Load local .env; existing environment wins"]
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

.env belongs beside monitor.py. It is read at LIVE startup if python-dotenv is
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
    H13 --> H90["3. monitor2 .90:8000 ports 0 and 2: TURN_OFF<br/>T2 and BH2 only; leave SCH untouched"]
    H90 --> MF["4. Mass-flow pair<br/>Check BOTH connections, then outlet to inlet<br/>See section 5"]
    MF --> K45["5. Kikusui .45: OUTP OFF<br/>Query output state"]
    K45 --> CA["6. CAEN Chamber .51, slot 4<br/>Discover channels, attempt Pw=0 for EACH"]
    CA --> WAIT["7. Wait 60 minutes<br/>Timer starts AFTER the preceding attempts finish"]
    WAIT --> CONT{"Wait completed or skipped?<br/>See section 4"}
    CONT -->|Canceled| CANCEL["Skip ALL remaining equipment actions<br/>Earlier actions are not undone"]
    CONT -->|Yes| U12["8. Pi .12: USB ports 1, 2, 3, 4 OFF"]
    U12 --> U13["9. Pi .13: USB ports 1, 2, 3, 4 OFF"]
    U13 --> K42["10. LAST: Kikusui .42 OUTP OFF<br/>Query output state"]
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
| monitor2 HV3 | 192.168.20.90:8000; port 0 T2, 2 BH2 (55V) |
| Mass-flow pair | Outlet 192.168.20.17:4196 → inlet 192.168.20.14:4001; device address 1 |
| First Kikusui | 192.168.20.45:5025 |
| CAEN Chamber | 192.168.20.51, slot 4 |
| USB power | SSH to Pi .12, then .13; hub 1-1, ports 1–4 |
| Last Kikusui | 192.168.20.42:5025 |

The obsolete .91 HV target is removed. SCH (monitor2 port 1) and its separate
HV4 API (:8001) are not part of this HV group. The existing dedicated Kikusui
.45 and .42 steps below remain unchanged.

Each HV/USB port and CAEN channel has its own retry budget: currently **3 attempts
total**, a **10-second attempt timeout**, and **2 seconds between attempts**.
A CAEN inventory failure prevents channel attempts for that slot; a failure of
one discovered channel does not skip the other channels. Errors are recorded,
and attempts continue with the remaining configured equipment. A timeout means
the equipment result is unknown, not that the command definitely had no effect.

Discord uses background, best-effort delivery. Failure or absence of the webhook
does not stop equipment shutdown. Current code sends an initial and a final
summary; it does not send a separate recovery notification.

A successful command or acknowledgment is **not physical verification that every
HV, valve, and power output is off**. Process completion or cancellation ends this
one monitoring run. Whether it restarts depends on the installed service unit;
see [service management](../daemon/README.md), not just the repository template.

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
    GC -->|No| FINAL["Run final USB steps, then Kikusui .42"]
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

Use regular files owned by the monitor user (sks) or root. Symlinks, special
files, and other owners are ignored. Accepted requests are consumed once; stale
accepted files are cleared at LIVE startup. Do not create them in advance
expecting them to survive startup.

## 5. Mass-flow pair: the important branch

~~~mermaid
flowchart TD
    O["Read-only outlet access/status check"] --> I["Read-only inlet access/status check"]
    I --> BOTH{"BOTH checks succeeded?"}
    BOTH -->|No| NONE["Send NEITHER close command<br/>Record failure; continue to Kikusui .45"]
    BOTH -->|Yes| CLOSEO["Attempt OUTLET close; validate acknowledgment"]
    CLOSEO --> CLOSEI["Attempt INLET close even if outlet close failed"]
    CLOSEI --> GOOD{"Both close commands acknowledged?"}
    GOOD -->|Yes| DONE["Record command-level success<br/>Continue to Kikusui .45"]
    GOOD -->|No| PART["Record CRITICAL incomplete closure<br/>One valve may be closed and the other not<br/>Continue to Kikusui .45"]
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
| Addresses, timing, ordered targets | [config.toml](../config.toml) |
| LIVE startup, environment, lock, exit | [monitor.py](../monitor.py) |
| Alert edge, retries, wait controls, summary | [engine.py](../lh2_monitor/engine.py) |
| Status input, control files, notification delivery | [adapters.py](../lh2_monitor/adapters.py) |
| Equipment protocol operations | [hardware.py](../lh2_monitor/hardware.py) |
| Read-only readiness checks | [preflight.py](../lh2_monitor/preflight.py) |
| Entirely virtual behavior | [simulation.py](../lh2_monitor/simulation.py) |

For visual/logic experiments without real devices, use the explicit --simulate
mode described in the [project README](../README.md#offline-testing). Unlike the
--devices preflight, simulation contacts no equipment at all.
