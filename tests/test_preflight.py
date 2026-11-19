from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import types
import unittest
from unittest.mock import MagicMock, patch

from lh2_monitor.config import load_config
from lh2_monitor import preflight as p


ROOT = Path(__file__).resolve().parent.parent


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config.toml")

    def test_groups_all_ten_targets_without_shutdown_execution(self):
        checks = p.equipment_checks(self.config)
        self.assertEqual(len(checks), 10)
        self.assertEqual([kind for _, kind, _ in checks],
                         ["hv", "hv", "hv", "massflow", "massflow",
                          "kikusui", "caen", "usb", "usb", "kikusui"])
        self.assertEqual(checks[0][2]["ports"], [1, 2, 3])
        self.assertEqual(checks[2][2]["host"], "192.168.20.90")
        self.assertEqual(checks[2][2]["ports"], [0, 1, 2])
        self.assertEqual(checks[-3][2]["ports"], [1, 2, 3, 4])

    def test_http_helper_uses_get_only(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"queue_size":0}'
        with patch.object(p.http.client, "HTTPConnection", return_value=connection):
            self.assertEqual(p.http_json({"host": "192.0.2.1", "port": 8000},
                                        "/api/queue-status", 1), {"queue_size": 0})
        connection.request.assert_called_once_with("GET", "/api/queue-status")
        connection.close.assert_called_once()

    def test_hv_checks_api_and_ports_without_post(self):
        response = [{"paths": {"/serial/command": {"post": {}}}},
                    {"queue_size": 0}, {"0": "one", "1": "two"}]
        with patch.object(p, "http_json", side_effect=response) as get:
            detail = p.probe_hv({"ports": [0, 1]}, 1)
        self.assertIn("No command queued", detail)
        self.assertEqual([call.args[1] for call in get.call_args_list],
                         ["/openapi.json", "/api/queue-status", "/api/port-labels"])

    def test_hv_missing_configured_port_is_failure(self):
        response = [{"paths": {"/serial/command": {"post": {}}}},
                    {"queue_size": 0}, {"1": "one"}]
        with patch.object(p, "http_json", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "absent"):
                p.probe_hv({"ports": [0, 1]}, 1)

    def test_kikusui_only_sends_output_query_even_when_on(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.return_value = b"1\n"
        with patch.object(p.socket, "create_connection", return_value=sock):
            self.assertIn("replied 1", p.probe_kikusui({"host": "192.0.2.1", "port": 5025}, 1))
        sock.sendall.assert_called_once_with(b"OUTP?\n")

    def test_massflow_reads_status_and_never_closes_valve(self):
        valve = MagicMock()
        with patch("lh2_monitor.hardware.MassFlowValve", return_value=valve):
            p.probe_massflow({}, 1)
        valve.preflight.assert_called_once_with(1)
        valve.close_valve.assert_not_called()
        valve.close_connection.assert_called_once()

    def test_massflow_closes_connection_after_failed_read(self):
        valve = MagicMock()
        valve.preflight.side_effect = TimeoutError("read failed")
        with patch("lh2_monitor.hardware.MassFlowValve", return_value=valve):
            with self.assertRaises(TimeoutError):
                p.probe_massflow({}, 1)
        valve.close_valve.assert_not_called()
        valve.close_connection.assert_called_once()

    def test_caen_requests_inventory_only(self):
        with patch("lh2_monitor.hardware.caen_operation",
                   return_value={"channels": [0, 1]}) as operation:
            detail = p.probe_caen({"slot": 4}, 1)
        operation.assert_called_once_with({"slot": 4}, 1, inventory=True)
        self.assertIn("No channel parameter written", detail)

    def test_named_caen_preflight_is_inventory_only(self):
        groups = [{'slot': 4, 'channels': [4, 5, 6, 7],
                   'names': ['BLC2a-cat', 'BLC2a-pot', 'BLC2b-cat', 'BLC2b-pot']}]
        with patch('lh2_monitor.hardware.caen_operation', return_value={'groups': groups}) as operation:
            detail = p.probe_caen({'channel_name_contains': 'BLC2'}, 1)
        operation.assert_called_once_with({'channel_name_contains': 'BLC2'}, 1, inventory=True)
        self.assertIn('4, 5, 6, 7', detail)

    def test_usb_only_lists_sudo_policy_and_does_not_run_uhubctl(self):
        command = p.usb_read_only_command({
            "user": "test-user", "host": "192.0.2.1", "hub": "1-1", "ports": [1, 2],
        }, 1)
        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        pieces = command[-1].split(" && ")
        self.assertEqual(pieces[0], "test -x /usr/sbin/uhubctl")
        for piece in pieces[1:-1]:
            tokens = shlex.split(piece)
            self.assertEqual(tokens[:4], ["sudo", "-n", "-l", "/usr/sbin/uhubctl"])
        self.assertEqual(pieces[-1], "printf LH2_PREFLIGHT_SSH_OK")

    def test_usb_permission_failure_is_not_reported_as_success(self):
        result = types.SimpleNamespace(returncode=1, stdout="", stderr="permission denied")
        with patch.object(p.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                p.probe_usb({"user": "sks", "host": "192.0.2.1", "hub": "1-1", "ports": [1]}, 1)

    def test_probe_timeout_kills_worker_process_group(self):
        process = MagicMock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired("probe", 3), ("", "")]
        with patch.object(p.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(p.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                p.run_probe("caen", {}, 1)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        kill.assert_called_once_with(1234, p.signal.SIGKILL)

    def test_worker_failure_does_not_echo_unstructured_output(self):
        process = MagicMock(returncode=1)
        process.communicate.return_value = ("private raw output", "private stderr")
        with patch.object(p.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "without a result") as error:
                p.run_probe("imports", {}, 1)
        self.assertNotIn("private", str(error.exception))

    def test_secret_values_are_redacted(self):
        with patch.dict(os.environ, {"CAEN_PASSWORD": "do-not-print-this",
                                     "DISCORD_WEBHOOK_URL": "https://example.invalid/private"}):
            detail = p.safe_error(RuntimeError("do-not-print-this https://example.invalid/private"))
        self.assertNotIn("do-not-print-this", detail)
        self.assertNotIn("example.invalid", detail)

    def test_unknown_url_is_also_redacted(self):
        detail = p.safe_error(RuntimeError("failed at https://outside.invalid/sensitive/path"))
        self.assertNotIn("outside.invalid", detail)
        self.assertNotIn("sensitive/path", detail)

    def collect(self, *, devices=False, value="0", age=0):
        with patch("lh2_monitor.credentials.load_environment", return_value=True), \
                patch.dict(os.environ, {"CAEN_USERNAME": "test-user", "CAEN_PASSWORD": "test-pass"}), \
                patch.object(p, "perform_check",
                             side_effect=lambda name, *args: p.Check(name, "PASS", "mocked")) as check, \
                patch.object(p, "run_probe", return_value={"value": value, "age_seconds": age}), \
                patch("lh2_monitor.adapters.DiscordNotifier",
                      side_effect=AssertionError("Discord must never be constructed")), \
                patch("lh2_monitor.hardware.perform",
                      side_effect=AssertionError("Shutdown worker must never run")):
            results = p.collect_checks(self.config, devices=devices)
        return results, check

    def test_local_mode_never_probes_devices_and_skips_discord(self):
        results, check = self.collect()
        self.assertEqual(check.call_count, 1)
        self.assertEqual(check.call_args.args[1], "imports")
        self.assertEqual(next(c.status for c in results if c.name == "Discord"), "SKIP")
        self.assertEqual(next(c.status for c in results if c.name == "Equipment communication"), "SKIP")

    def test_device_mode_probes_all_configured_targets_but_not_discord(self):
        results, check = self.collect(devices=True)
        self.assertEqual(check.call_count, 11)  # Native imports + ten devices.
        self.assertEqual(len([c for c in results if c.status == "SKIP"]), 1)

    def test_existing_alert_and_stale_status_are_warnings(self):
        for value, age in [("1", 0), ("0", 100)]:
            with self.subTest(value=value, age=age):
                results, _ = self.collect(value=value, age=age)
                self.assertEqual(next(c.status for c in results if c.name == "Alert input"), "WARN")

    def test_missing_credentials_prevent_caen_contact(self):
        with patch("lh2_monitor.credentials.load_environment", return_value=True), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(p, "perform_check",
                             side_effect=lambda name, *args: p.Check(name, "PASS", "mocked")) as check, \
                patch.object(p, "run_probe", return_value={"value": "0", "age_seconds": 0}):
            checks = p.collect_checks(self.config, devices=True)
        self.assertFalse(any(call.args[1] == "caen" for call in check.call_args_list))
        self.assertEqual(next(c.status for c in checks if c.name == "CAEN credentials"), "FAIL")

    def test_cli_json_exit_code_exposes_failures(self):
        with patch.object(p, "collect_checks", return_value=[p.Check("test", "FAIL", "mocked")]), \
                redirect_stdout(StringIO()) as output:
            self.assertEqual(p.main(["--json"]), 1)
        self.assertEqual(json.loads(output.getvalue())["exit_code"], 1)

    def test_cli_checks_equipment_by_default(self):
        with patch.object(p, "collect_checks", return_value=[]) as collect, redirect_stdout(StringIO()):
            self.assertEqual(p.main([]), 0)
        self.assertTrue(collect.call_args.kwargs["devices"])

    def test_cli_local_option_skips_equipment(self):
        with patch.object(p, "collect_checks", return_value=[]) as collect, redirect_stdout(StringIO()):
            self.assertEqual(p.main(["--local"]), 0)
        self.assertFalse(collect.call_args.kwargs["devices"])

    def test_invalid_timeout_is_rejected(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    p.main(["--timeout", value])


if __name__ == "__main__":
    unittest.main()
