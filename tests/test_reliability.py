"""Regression checks for control-file errors and bounded worker cleanup."""
from dataclasses import replace
import errno
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from lh2_monitor.adapters import FileTriggers, ProcessExecutor
from lh2_monitor.config import Action, RetryPolicy, load_config
from lh2_monitor.engine import ActionError, Engine
from lh2_monitor import preflight, processes
from lh2_monitor.simulation import VirtualClock, VirtualExecutor, VirtualNotifier

ROOT = Path(__file__).resolve().parent.parent


def root_info(*, generation=1):
    return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0,
                                 st_dev=1, st_ino=123, st_mtime_ns=generation,
                                 st_ctime_ns=generation)


class TriggerReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.log = MagicMock()

    def test_root_request_unlink_denied_is_honored_once(self):
        path = Path("/unused-audit-control/skip")
        triggers = FileTriggers({"skip": path}, self.log)
        with patch.object(Path, "lstat", return_value=root_info()), \
                patch.object(Path, "unlink", side_effect=PermissionError(errno.EPERM, "sticky tmp")) as unlink:
            self.assertTrue(triggers.consume("skip"))
            self.assertFalse(triggers.consume("skip"))
            self.assertFalse(triggers.consume("skip"))
        unlink.assert_called_once()
        self.log.assert_called_once()

    def test_touching_retained_request_creates_a_new_event(self):
        triggers = FileTriggers({"extend": Path("/unused-audit-control/extend")}, self.log)
        with patch.object(Path, "lstat", side_effect=[root_info(), root_info(), root_info(generation=2)]), \
                patch.object(Path, "unlink", side_effect=PermissionError(errno.EPERM, "sticky tmp")) as unlink:
            self.assertTrue(triggers.consume("extend"))
            self.assertFalse(triggers.consume("extend"))
            self.assertTrue(triggers.consume("extend"))
        self.assertEqual(unlink.call_count, 2)

    def test_startup_clear_does_not_repeat_undeletable_request_later(self):
        triggers = FileTriggers({"skip": Path("/unused-audit-control/skip")}, self.log)
        with patch.object(Path, "lstat", return_value=root_info()), \
                patch.object(Path, "unlink", side_effect=PermissionError(errno.EPERM, "sticky tmp")):
            triggers.clear()
            self.assertFalse(triggers.consume("skip"))

    def test_disappearance_clears_retained_identity(self):
        triggers = FileTriggers({"skip": Path("/unused-audit-control/skip")}, self.log)
        with patch.object(Path, "lstat", side_effect=[root_info(), FileNotFoundError(), root_info()]), \
                patch.object(Path, "unlink", side_effect=PermissionError(errno.EPERM, "sticky tmp")):
            self.assertTrue(triggers.consume("skip"))
            self.assertFalse(triggers.consume("skip"))
            self.assertTrue(triggers.consume("skip"))

    def test_inspection_error_is_warned_once_and_ignored(self):
        triggers = FileTriggers({"skip": Path("/unused-audit-control/skip")}, self.log)
        with patch.object(Path, "lstat", side_effect=PermissionError(errno.EACCES, "cannot inspect")), \
                patch.object(Path, "unlink") as unlink:
            self.assertFalse(triggers.consume("skip"))
            triggers.clear()
        unlink.assert_not_called()
        self.log.assert_called_once()

    def test_readonly_directory_does_not_repeat_trusted_request(self):
        triggers = FileTriggers({"skip": Path("/unused-audit-control/skip")}, self.log)
        info = root_info()
        info.st_uid = 1000
        with patch("lh2_monitor.adapters.os.geteuid", return_value=1000), \
                patch.object(Path, "lstat", return_value=info), \
                patch.object(Path, "unlink", side_effect=OSError(errno.EROFS, "readonly")):
            self.assertTrue(triggers.consume("skip"))
            self.assertFalse(triggers.consume("skip"))

    def run_sequence_with_request(self, name, *, inspect_error=False):
        config = replace(load_config(ROOT / "config.toml"), wait_seconds=3)
        clock = VirtualClock()
        executor = VirtualExecutor(clock)
        notifier = VirtualNotifier(self.log)
        paths = {key: Path("/unused-audit-control") / key for key in ("skip", "cancel", "extend")}
        triggers = FileTriggers(paths, self.log)
        def inspect(path):
            if path == paths[name]:
                if inspect_error:
                    raise PermissionError(errno.EACCES, "cannot inspect")
                return root_info()
            raise FileNotFoundError()
        with patch.object(Path, "lstat", inspect), \
                patch.object(Path, "unlink", side_effect=PermissionError(errno.EPERM, "sticky tmp")):
            outcome = Engine(config, executor, notifier, clock, triggers, self.log).run_sequence()
        return outcome, clock, executor, notifier

    def test_undeletable_skip_reaches_final_shutdown_steps(self):
        outcome, clock, executor, notifier = self.run_sequence_with_request("skip")
        self.assertFalse(outcome.canceled)
        self.assertFalse(outcome.errors)
        self.assertEqual(clock.now, 5)
        self.assertEqual(executor.calls[-1][1], "kikusui42")
        self.assertEqual(len(notifier.messages), 2)

    def test_undeletable_extend_does_not_reset_timer_forever(self):
        outcome, clock, executor, _ = self.run_sequence_with_request("extend")
        self.assertFalse(outcome.canceled)
        self.assertEqual(clock.now, 3)
        self.assertEqual(executor.calls[-1][1], "kikusui42")

    def test_undeletable_cancel_produces_normal_cancel_summary(self):
        outcome, _, executor, notifier = self.run_sequence_with_request("cancel")
        self.assertTrue(outcome.canceled)
        self.assertFalse(any(key.startswith("usb") or key == "kikusui42" for _, key in executor.calls))
        self.assertIn("CANCELED", notifier.messages[-1])

    def test_uninspectable_request_does_not_abort_deadline_actions(self):
        outcome, clock, executor, _ = self.run_sequence_with_request("skip", inspect_error=True)
        self.assertFalse(outcome.canceled)
        self.assertEqual(clock.now, 3)
        self.assertEqual(executor.calls[-1][1], "kikusui42")


class CleanupReliabilityTests(unittest.TestCase):
    def stuck_process(self):
        process = MagicMock(pid=4321)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("operation", 10),
            subprocess.TimeoutExpired("cleanup", 2, output=b"partial", stderr=b"diagnostic"),
        ]
        return process

    def test_cleanup_timeout_closes_pipes_and_returns_unknown(self):
        process = MagicMock(pid=4321)
        process.communicate.side_effect = subprocess.TimeoutExpired("cleanup", 2, output=b"partial")
        with patch.object(processes.os, "killpg") as kill, patch.object(processes, "_reap_later") as reap:
            result = processes.terminate_group(process)
        self.assertFalse(result.reaped)
        self.assertEqual(result.output, "partial")
        self.assertIn("UNKNOWN", result.note)
        self.assertIn("2s cleanup limit", result.note)
        process.communicate.assert_called_once_with(timeout=2.0)
        kill.assert_called_once_with(4321, signal.SIGKILL)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once()
        reap.assert_called_once_with(process)

    def test_cleanup_io_error_does_not_replace_original_failure(self):
        process = MagicMock(pid=4321)
        process.communicate.side_effect = OSError(errno.EIO, "pipe error")
        with patch.object(processes.os, "killpg"), patch.object(processes, "_reap_later"):
            result = processes.terminate_group(process)
        self.assertFalse(result.reaped)
        self.assertIn("UNKNOWN", result.note)

    def test_group_signal_error_uses_direct_kill_and_remains_bounded(self):
        process = MagicMock(pid=4321)
        process.communicate.side_effect = subprocess.TimeoutExpired("cleanup", 2)
        process.kill.side_effect = PermissionError(errno.EPERM, "cannot kill")
        with patch.object(processes.os, "killpg", side_effect=PermissionError(errno.EPERM, "cannot kill")), \
                patch.object(processes, "_reap_later"):
            result = processes.terminate_group(process)
        self.assertFalse(result.reaped)
        self.assertIn("SIGKILL failed", result.note)
        process.kill.assert_called_once()

    def test_background_reaper_does_not_block_caller(self):
        process = MagicMock(pid=4321)
        started, release = threading.Event(), threading.Event()
        def wait():
            started.set()
            release.wait(2)
        process.wait.side_effect = wait
        try:
            processes._reap_later(process)
            self.assertTrue(started.wait(1))
            self.assertFalse(release.is_set())
        finally:
            release.set()

    def test_shutdown_client_reports_unknown_after_failed_cleanup(self):
        process = self.stuck_process()
        with patch("lh2_monitor.adapters.subprocess.Popen", return_value=process), \
                patch.object(processes.os, "killpg"), patch.object(processes, "_reap_later"):
            with self.assertRaisesRegex(ActionError, "UNKNOWN.*cleanup limit"):
                ProcessExecutor(MagicMock()).run_once(Action("test", "test", "hv", {}), RetryPolicy())
        self.assertEqual(process.communicate.call_args_list[-1].kwargs["timeout"], 2.0)

    def test_other_equipment_is_attempted_after_stuck_worker(self):
        config = load_config(ROOT / "config.toml")
        config = replace(config, retry=replace(config.retry, max_attempts=1), wait_seconds=1)
        clock = VirtualClock()
        virtual = VirtualExecutor(clock)
        calls = []
        class Executor:
            def run_once(self, action, policy):
                calls.append(action.id)
                if len(calls) == 1:
                    return ProcessExecutor(MagicMock()).run_once(action, policy)
                return virtual.run_once(action, policy)
        process = self.stuck_process()
        with patch("lh2_monitor.adapters.subprocess.Popen", return_value=process), \
                patch.object(processes.os, "killpg"), patch.object(processes, "_reap_later"):
            outcome = Engine(config, Executor(), VirtualNotifier(lambda *args: None),
                             clock, types.SimpleNamespace(consume=lambda name: False),
                             lambda *args: None).run_sequence()
        self.assertTrue(outcome.errors)
        self.assertIn("UNKNOWN", outcome.errors[0])
        self.assertEqual(calls[-1], "kikusui42")

    def test_preflight_reports_stuck_probe_then_checks_next_target(self):
        stuck = self.stuck_process()
        good = MagicMock(returncode=0)
        good.communicate.return_value = ('LH2_PREFLIGHT_JSON:{"ok":true,"detail":"mock success"}', "")
        with patch.object(preflight.subprocess, "Popen", side_effect=[stuck, good]), \
                patch.object(processes.os, "killpg"), patch.object(processes, "_reap_later"):
            failed = preflight.perform_check("first", "hv", {}, 1)
            succeeded = preflight.perform_check("second", "hv", {}, 1)
        self.assertEqual(failed.status, "FAIL")
        self.assertIn("UNKNOWN", failed.detail)
        self.assertEqual(succeeded.status, "PASS")


if __name__ == "__main__":
    unittest.main()
