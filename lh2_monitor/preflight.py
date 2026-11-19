"""Read-only readiness checks. Never starts the monitor or sends Discord messages."""

import argparse
from dataclasses import asdict, dataclass
import http.client
import importlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

from .config import load_config


ROOT = Path(__file__).resolve().parent.parent
RESULT_PREFIX = "LH2_PREFLIGHT_JSON:"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def safe_error(error):
    text = f"{type(error).__name__}: {error}"
    for name in ("CAEN_USERNAME", "CAEN_PASSWORD", "DISCORD_WEBHOOK_URL"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"https?://\S+", "[URL redacted]", text)
    return text[:600]


def http_json(parameters, path, timeout):
    connection = http.client.HTTPConnection(parameters["host"], parameters["port"], timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"GET {path}: HTTP {response.status}")
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise RuntimeError("HTTP response exceeds 1 MiB")
        return json.loads(body)
    finally:
        connection.close()


def probe_hv(parameters, timeout):
    schema = http_json(parameters, "/openapi.json", timeout)
    if "post" not in schema.get("paths", {}).get("/serial/command", {}):
        raise RuntimeError("API does not advertise POST /serial/command; no POST was sent")
    queue = http_json(parameters, "/api/queue-status", timeout)
    if not isinstance(queue, dict) or type(queue.get("queue_size")) is not int:
        raise RuntimeError("Unexpected queue-status response")
    labels = http_json(parameters, "/api/port-labels", timeout)
    if not isinstance(labels, dict):
        raise RuntimeError("Unexpected port-labels response")
    missing = [port for port in parameters["ports"] if str(port) not in labels]
    if missing:
        raise RuntimeError(f"Configured HV ports absent from controller labels: {missing}")
    return (f"GET API/schema OK; ports {parameters['ports']}; queue={queue['queue_size']}. "
            "No command queued; serial-device response/output not verified.")


def probe_kikusui(parameters, timeout):
    with socket.create_connection((parameters["host"], parameters["port"]), timeout) as sock:
        sock.sendall(b"OUTP?\n")
        response = bytearray()
        while b"\n" not in response:
            block = sock.recv(256)
            if not block:
                raise RuntimeError("Connection closed before output-state reply")
            response.extend(block)
            if len(response) > 1024:
                raise RuntimeError("Output-state response too long")
    state = response.decode("ascii").strip()
    if state not in {"0", "1", "OFF", "ON"}:
        raise RuntimeError("Unrecognized output-state reply")
    return f"OUTP? replied {state}; output state was not changed."


def probe_massflow(parameters, timeout):
    from .hardware import MassFlowValve
    valve = MassFlowValve(parameters)
    try:
        valve.preflight(timeout)
    finally:
        valve.close_connection()
    return "CPL valve-status reply/checksum OK; no valve-close command sent."


def probe_caen(parameters, timeout):
    from .hardware import caen_operation
    result = caen_operation(parameters, timeout, inventory=True)
    return (f"Login and crate inventory OK; slot {parameters['slot']}, "
            f"{len(result['channels'])} channels. No channel parameter written.")


def usb_read_only_command(parameters, timeout):
    # sudo -l only inspects permissions; the following uhubctl arguments are NOT executed.
    commands = ["test -x /usr/sbin/uhubctl"]
    for port in parameters["ports"]:
        arguments = ["sudo", "-n", "-l", "/usr/sbin/uhubctl", "-l",
                     str(parameters["hub"]), "-p", str(port), "-a", "0"]
        commands.append(shlex.join(arguments) + " >/dev/null")
    commands.append("printf LH2_PREFLIGHT_SSH_OK")
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectionAttempts=1", "-o", f"ConnectTimeout={max(1, int(timeout))}",
        "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2",
        f"{parameters['user']}@{parameters['host']}", " && ".join(commands),
    ]


def probe_usb(parameters, timeout):
    command = usb_read_only_command(parameters, timeout)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"SSH/uhubctl/sudo-list check failed (exit {result.returncode}); "
                           + result.stderr.strip()[-350:])
    if "LH2_PREFLIGHT_SSH_OK" not in result.stdout:
        raise RuntimeError("SSH permission check did not return its completion marker")
    return ("SSH authentication/host key, uhubctl executable, and sudo -n -l policy OK "
            f"for ports {parameters['ports']}. No USB command executed; hub not queried.")


def probe_imports(parameters, timeout):
    modules = ["ssl", "tomllib", "ctypes", "fcntl", "monitor", "lh2_monitor.adapters",
               "lh2_monitor.hardware", "lh2_monitor.worker"]
    if parameters.get("caen"):
        modules.append("caen_libs.caenhvwrapper")
    for module in modules:
        importlib.import_module(module)
    return "Monitor modules and required native libraries import successfully; no device opened."


def probe_input(parameters, timeout):
    from .adapters import read_status
    path = Path(parameters["path"])
    status = read_status(path)
    if status.error:
        raise RuntimeError(status.error)
    age = max(0, time.time() - path.stat().st_mtime)
    return {"value": status.value, "age_seconds": round(age, 1)}


PROBES = {
    "hv": probe_hv, "kikusui": probe_kikusui, "massflow": probe_massflow,
    "caen": probe_caen, "usb": probe_usb, "imports": probe_imports, "input": probe_input,
}


def run_probe(kind, parameters, timeout):
    """Isolate native imports, network I/O, and SSH in a bounded process group."""
    if kind not in PROBES:
        raise ValueError("Unknown read-only probe")
    worker = subprocess.Popen(
        [sys.executable, "-B", str(ROOT / "preflight.py"), "--probe", kind],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    budget = timeout * 3 + 2 if kind == "hv" else timeout + 2
    try:
        output, _ = worker.communicate(
            json.dumps({"parameters": parameters, "timeout": timeout}), timeout=budget)
    except BaseException:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        worker.communicate()
        raise
    records = [line[len(RESULT_PREFIX):] for line in output.splitlines()
               if line.startswith(RESULT_PREFIX)]
    if len(records) != 1:
        raise RuntimeError(f"Probe failed without a result (exit {worker.returncode})")
    result = json.loads(records[0])
    if not result.get("ok") or worker.returncode:
        raise RuntimeError(result.get("error", "Read-only probe failed"))
    return result["detail"]


def equipment_checks(config):
    """Group per-port actions into one read-only check per configured target."""
    grouped = {}
    for action in config.actions:
        if action.kind == "wait":
            continue
        if action.kind == "massflow_pair":
            for side in ("outlet", "inlet"):
                parameters = dict(action.parameters[side])
                grouped[("massflow", side)] = (f"Mass-flow {side}", "massflow", parameters)
            continue
        p = dict(action.parameters)
        key = (action.kind, p["host"], p.get("port"), p.get("slot"), p.get("hub"))
        if key not in grouped:
            p["ports"] = []
            grouped[key] = (p["name"], action.kind, p)
        if action.kind in {"hv", "usb"}:
            ports = grouped[key][2]["ports"]
            if p["target_port"] not in ports:
                ports.append(p["target_port"])
    return list(grouped.values())


def perform_check(name, kind, parameters, timeout):
    try:
        return Check(name, "PASS", run_probe(kind, parameters, timeout))
    except Exception as error:
        return Check(name, "FAIL", safe_error(error))


def collect_checks(config, *, devices=False, timeout=None):
    from .credentials import load_environment
    checks = [Check("Python", "PASS", f"{sys.version.split()[0]}: {sys.executable}"),
              Check("Configuration", "PASS", f"{len(config.actions)} ordered actions validated.")]
    caen_required = any(action.kind == "caen" for action in config.actions)
    loaded = load_environment()
    checks.append(Check(".env loader", "PASS" if loaded else "WARN",
                        "python-dotenv available; existing environment variables take precedence."
                        if loaded else "python-dotenv unavailable; only exported variables are used."))
    credentials_ok = True
    if caen_required:
        missing = [key for key in ("CAEN_USERNAME", "CAEN_PASSWORD")
                   if not os.environ.get(key) or (key == "CAEN_USERNAME" and not os.environ[key].strip())]
        credentials_ok = not missing
        checks.append(Check("CAEN credentials", "PASS" if credentials_ok else "FAIL",
                            "Required values are set; values hidden."
                            if credentials_ok else "Missing: " + ", ".join(missing)))
    timeout = timeout or config.retry.attempt_timeout_seconds
    checks.append(perform_check("Python/native imports", "imports", {"caen": caen_required}, timeout))
    try:
        detail = run_probe("input", {"path": str(config.status_file)}, timeout)
        age = detail["age_seconds"]
        warning = detail["value"] == "1" or age > max(10, config.poll_seconds * 3)
        checks.append(Check("Alert input", "WARN" if warning else "PASS",
                            f"Alert_H2leak={detail['value']}; file age={age:g}s. "
                            "Monitor does not reject stale input. No monitor was started."))
    except Exception as error:
        checks.append(Check("Alert input", "FAIL", safe_error(error)))
    checks.append(Check("Discord", "SKIP", "No notification, HTTP request, or delivery test. "
                        + ("Webhook is configured (value hidden)." if os.environ.get("DISCORD_WEBHOOK_URL")
                           else "Webhook is not configured.")))
    if devices:
        for name, kind, parameters in equipment_checks(config):
            if kind == "caen" and not credentials_ok:
                checks.append(Check(name, "FAIL", "Not contacted: CAEN credentials are missing."))
                continue
            checks.append(perform_check(name, kind, parameters, timeout))
    else:
        checks.append(Check("Equipment communication", "SKIP",
                            "Not contacted. Add --devices for read-only communication checks."))
    return checks


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Timeout must be positive and finite")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--devices", action="store_true", help="Query configured devices read-only")
    parser.add_argument("--timeout", type=positive_timeout, help="Per-probe I/O timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    parser.add_argument("--probe", choices=tuple(PROBES), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        try:
            request = json.load(sys.stdin)
            detail = PROBES[args.probe](request["parameters"], request["timeout"])
            result = {"ok": True, "detail": detail}
            code = 0
        except Exception as error:
            result = {"ok": False, "error": safe_error(error)}
            code = 1
        print(RESULT_PREFIX + json.dumps(result), flush=True)
        return code
    try:
        config = load_config(args.config)
        checks = collect_checks(config, devices=args.devices, timeout=args.timeout)
    except Exception as error:
        checks = [Check("Preflight", "FAIL", safe_error(error))]
    code = int(any(check.status == "FAIL" for check in checks))
    if args.json:
        print(json.dumps({"scope": "read-only devices" if args.devices else "local only",
                          "checks": [asdict(check) for check in checks],
                          "exit_code": code}, ensure_ascii=False, indent=2))
    else:
        print("LH2 PREFLIGHT: " + ("READ-ONLY DEVICES" if args.devices else "LOCAL ONLY"))
        print("No shutdown commands, trigger-file changes, monitor startup, or Discord messages.")
        for check in checks:
            print(f"[{check.status:4}] {check.name}: {check.detail}")
        counts = {status: sum(check.status == status for check in checks)
                  for status in ("PASS", "WARN", "FAIL", "SKIP")}
        print("Summary: " + ", ".join(f"{n} {key}" for key, n in counts.items()))
        print("PASS is a point-in-time read-only check, not proof that a future shutdown will succeed.")
    return code
