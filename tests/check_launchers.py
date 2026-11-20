"""Standalone shell checks using fake tmux/systemctl/sudo/flock; never runs monitors."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = set()


def guard(event, args):
    if event.startswith('socket.') or event in {'os.system', 'ctypes.dlopen'}:
        raise RuntimeError('Only offline launcher fixtures are permitted')
    if event == 'subprocess.Popen':
        command = args[1]
        if not isinstance(command, list) or command[:1] != ['/bin/bash'] or command[1] not in ALLOWED:
            raise RuntimeError('Only copied shell scripts may execute')


sys.addaudithook(guard)


class Launchers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lh2-launcher-test-')
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/monitor.lock').write_text('')
        for name in ['run.sh', 'unlock.sh']:
            path = self.root / name
            path.write_text((ROOT / name).read_text().replace('/home/sks/monitor_tools/lh2_leak_monitor', str(self.root)))
            ALLOWED.add(str(path))
        fake = r'''#!/bin/bash
name=${0##*/}
printf '%s' "$name" >> "$FAKE_LOG"
printf ' <%s>' "$@" >> "$FAKE_LOG"
printf '\n' >> "$FAKE_LOG"
case "$name" in
 tmux)
  case "$1" in
   has-session) exit "${FAKE_SESSION_RC:-1}";;
   new-session) echo '%1';;
   split-window) echo '%2';;
  esac;;
 systemctl) echo "${FAKE_STATE:-inactive}";;
 flock) if [[ ${FAKE_BUSY:-0} == 1 && ! -f "$FAKE_STOPPED" ]]; then exit 1; fi;;
 sudo) : > "$FAKE_STOPPED";;
 lslocks) echo '999 python FLOCK WRITE fake-monitor.lock';;
 grep) exit 1;;
esac
'''
        for name in ['tmux', 'systemctl', 'flock', 'sudo', 'lslocks', 'grep']:
            path = self.bin / name
            path.write_text(fake)
            path.chmod(0o755)
        self.env = {'PATH': str(self.bin), 'FAKE_LOG': str(self.root / 'calls'),
                    'FAKE_STOPPED': str(self.root / 'stopped')}

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args, **env):
        result = subprocess.run(['/bin/bash', str(self.root / name), *args],
                                env={**self.env, **env}, capture_output=True, text=True)
        calls = self.root / 'calls'
        return result, calls.read_text() if calls.exists() else ''

    def test_new_session_sets_up_two_panes_before_start_and_attaches(self):
        result, calls = self.run_script('run.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('<split-window> <-h> <-p> <50>', calls)
        self.assertIn('<remain-on-exit> <on>', calls)
        self.assertLess(calls.index('<split-window>'), calls.index('<respawn-pane>'))
        self.assertEqual(calls.count('<respawn-pane>'), 1)
        self.assertIn('--startup-alert wait', calls)
        self.assertIn('<attach-session>', calls)
        self.assertNotIn('sudo', calls)

    def test_reconnect_does_not_start_monitor(self):
        result, calls = self.run_script('run.sh', FAKE_SESSION_RC='0')
        self.assertEqual(result.returncode, 0)
        self.assertIn('<attach-session>', calls)
        self.assertNotIn('<respawn-pane>', calls)
        self.assertNotIn('<new-session>', calls)

    def test_existing_tmux_client_switches_instead_of_nesting(self):
        result, calls = self.run_script('run.sh', FAKE_SESSION_RC='0', TMUX='fake')
        self.assertEqual(result.returncode, 0)
        self.assertIn('<switch-client>', calls)
        self.assertNotIn('<attach-session>', calls)

    def test_active_service_blocks_new_session_without_stopping_it(self):
        result, calls = self.run_script('run.sh', FAKE_STATE='active')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('<new-session>', calls)
        self.assertNotIn('sudo', calls)

    def test_check_busy_lock_does_not_stop_service(self):
        result, calls = self.run_script('unlock.sh', '--check', FAKE_BUSY='1', FAKE_STATE='active')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('sudo', calls)

    def test_explicit_stop_stops_only_the_lh2_service_and_verifies_release(self):
        result, calls = self.run_script('unlock.sh', '--stop-service', FAKE_BUSY='1', FAKE_STATE='active')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('sudo <systemctl> <stop> <lh2_monitor.service>', calls)
        self.assertEqual(calls.count('flock '), 2)
        self.assertIn('Monitor lock is free', result.stdout)
        self.assertTrue((self.root / 'runtime/monitor.lock').exists())

    def test_manual_monitor_is_not_killed(self):
        result, calls = self.run_script('unlock.sh', '--stop-service', FAKE_BUSY='1')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('sudo', calls)
        self.assertIn('manual monitor', result.stderr)

    def test_free_lock_is_not_removed(self):
        result, calls = self.run_script('unlock.sh', '--check')
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.root / 'runtime/monitor.lock').exists())
        self.assertNotIn('sudo', calls)


if __name__ == '__main__':
    unittest.main()
