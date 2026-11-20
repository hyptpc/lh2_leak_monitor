"""Offline reset tests: temporary request files; all services, tmux and signals mocked."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import fcntl
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import reset_runtime as reset


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lock = self.root / 'monitor.lock'
        self.lock.touch()
        self.requests = {name: self.root / (name + '.now') for name in ['skip', 'cancel', 'extend']}
        for path in self.requests.values():
            path.touch()
        self.config = SimpleNamespace(lock_file=self.lock, triggers=self.requests)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, state='inactive', session=False):
        def run(args, **kwargs):
            self.calls.append(args)
            code = 0
            if args[:2] == ['systemctl', 'show']:
                return subprocess.CompletedProcess(args, 0, stdout=state)
            if args[:2] == ['tmux', 'has-session']:
                code = 0 if session else 1
            return subprocess.CompletedProcess(args, code)
        return run

    def test_reset_clears_requests_but_keeps_lock_logs_and_credentials(self):
        kept = [self.root / name for name in ['monitor.log', 'credentials.fixture', 'config.toml']]
        for path in kept:
            path.write_text('keep')
        with patch.object(reset.subprocess, 'run', side_effect=self.runner()), redirect_stdout(StringIO()):
            reset.reset(self.config, self.root)
        self.assertTrue(self.lock.exists())
        self.assertFalse(any(p.exists() for p in self.requests.values()))
        self.assertTrue(all(p.read_text() == 'keep' for p in kept))
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls))

    def test_only_lh2_service_and_session_are_stopped(self):
        with patch.object(reset.subprocess, 'run', side_effect=self.runner('active', True)), redirect_stdout(StringIO()):
            reset.reset(self.config, self.root)
        self.assertIn(['sudo', 'systemctl', 'stop', 'lh2_monitor.service'], self.calls)
        self.assertIn(['tmux', 'kill-session', '-t', '=lh2-monitor'], self.calls)
        self.assertFalse(any('enable' in call or 'disable' in call for call in self.calls))

    def test_failed_lh2_service_record_is_cleared(self):
        with patch.object(reset.subprocess, 'run', side_effect=self.runner('failed')), redirect_stdout(StringIO()):
            reset.reset(self.config, self.root)
        self.assertIn(['sudo', 'systemctl', 'reset-failed', 'lh2_monitor.service'], self.calls)

    def test_unknown_lock_owner_is_not_killed_or_cleaned(self):
        with self.lock.open('a+') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(reset, 'find_lock_owner', return_value=None), \
                    patch.object(reset, 'interrupt_monitor') as interrupt, \
                    patch.object(reset.subprocess, 'run', side_effect=self.runner(session=True)), \
                    redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'could not be identified'):
                    reset.reset(self.config, self.root)
        interrupt.assert_not_called()
        self.assertTrue(all(path.exists() for path in self.requests.values()))
        self.assertFalse(any('kill-session' in call for call in self.calls))

    def test_kernel_lock_owner_lookup_uses_the_exact_inode(self):
        with self.lock.open('a+') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(reset.find_lock_owner(self.lock), os.getpid())

    def test_manual_monitor_gets_sigint_then_lock_is_verified(self):
        with self.lock.open('a+') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(reset, 'find_lock_owner', return_value=123), \
                    patch.object(reset, 'interrupt_monitor', side_effect=lambda *args: fcntl.flock(held, fcntl.LOCK_UN)) as interrupt:
                with reset.exclusive_runtime(self.lock, self.root):
                    interrupt.assert_called_once_with(123, self.root)
        self.assertTrue(self.lock.exists())

    def test_wait_for_lock_is_bounded_without_sigkill(self):
        with self.lock.open('a+') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(reset, 'find_lock_owner', return_value=123), \
                    patch.object(reset, 'interrupt_monitor'), \
                    patch.object(reset.time, 'monotonic', side_effect=[0, 16]):
                with self.assertRaisesRegex(RuntimeError, '15 seconds'):
                    with reset.exclusive_runtime(self.lock, self.root):
                        self.fail('Must not enter reset with a busy lock')

    def test_unrelated_process_is_not_signaled(self):
        with patch.object(reset.os, 'pidfd_open', return_value=123), \
                patch.object(reset.os, 'close'), \
                patch.object(Path, 'read_bytes', return_value=b'/usr/bin/python3\0/other/monitor.py\0'), \
                patch.object(Path, 'resolve', return_value=self.root), \
                patch.object(reset.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'not this LH2'):
                reset.interrupt_monitor(123, self.root)
        send.assert_not_called()

    def test_monitor_path_as_an_argument_does_not_make_an_unrelated_program_a_monitor(self):
        command = f'/usr/bin/python3\0/other/tool.py\0{self.root}/app/monitor.py\0'.encode()
        with patch.object(reset.os, 'pidfd_open', return_value=123), \
                patch.object(reset.os, 'close'), \
                patch.object(Path, 'read_bytes', return_value=command), \
                patch.object(Path, 'resolve', return_value=self.root), \
                patch.object(reset.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'not this LH2'):
                reset.interrupt_monitor(123, self.root)
        send.assert_not_called()

    def test_relative_lh2_command_is_identified_before_signaling(self):
        with patch.object(reset.os, 'pidfd_open', return_value=123), \
                patch.object(reset.os, 'close'), \
                patch.object(Path, 'read_bytes', return_value=b'/usr/bin/python3\0app/monitor.py\0'), \
                patch.object(Path, 'resolve', return_value=self.root), \
                patch.object(reset.signal, 'pidfd_send_signal') as send, redirect_stdout(StringIO()):
            reset.interrupt_monitor(123, self.root)
        send.assert_called_once_with(123, reset.signal.SIGINT)

    def test_sudo_failure_aborts_before_session_and_request_cleanup(self):
        def failure(args, **kwargs):
            if args[0] == 'sudo':
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout='active')
        with patch.object(reset.subprocess, 'run', side_effect=failure), redirect_stdout(StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                reset.reset(self.config, self.root)
        self.assertTrue(all(path.exists() for path in self.requests.values()))


if __name__ == '__main__':
    unittest.main()
