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
            path.write_text((ROOT / name).read_text().replace('/home/sks/monitor_tools/lh2_leak_monitor', str(self.root)).replace('/home/sks/.venv/bin/python', str(self.bin / 'python')))
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
        for name in ['tmux', 'systemctl', 'flock', 'sudo', 'lslocks', 'grep', 'python']:
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

    def test_simple_detached_launch_has_left_monitor_and_right_shell(self):
        result, calls = self.run_script('run.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('<new-session> <-d>', calls)
        self.assertIn('<split-window> <-h> <-p> <50> <-t> <%1>', calls)
        self.assertIn('app/monitor.py --startup-alert wait; exec /bin/bash', calls)
        self.assertIn('<select-pane> <-t> <%1>', calls)
        self.assertNotIn('<attach-session>', calls)
        self.assertNotIn('<switch-client>', calls)
        self.assertNotIn('<respawn-pane>', calls)
        self.assertNotIn('systemctl', calls)
        self.assertNotIn('sudo', calls)

    def test_existing_session_is_left_untouched(self):
        result, calls = self.run_script('run.sh', FAKE_SESSION_RC='0')
        self.assertEqual(result.returncode, 1)
        self.assertIn('already exists', result.stderr)
        self.assertNotIn('<new-session>', calls)
        self.assertNotIn('<split-window>', calls)
        self.assertNotIn('<attach-session>', calls)

    def test_inside_tmux_still_only_creates_detached_session(self):
        result, calls = self.run_script('run.sh', TMUX='fake')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('<switch-client>', calls)
        self.assertNotIn('<attach-session>', calls)

    def test_unlock_without_options_delegates_to_lh2_reset(self):
        result, calls = self.run_script('unlock.sh')
        self.assertEqual(result.returncode, 0)
        self.assertIn('python <-B> <app/reset_runtime.py>', calls)

    def test_old_unlock_options_are_rejected_without_actions(self):
        result, calls = self.run_script('unlock.sh', '--stop-service')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
