"""Test daemon shell helpers with fake sudo/systemctl only; no service access."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent
ALLOWED_SCRIPTS = set()


def audit(event, args):
    if event.startswith('socket.') or event in {'urllib.Request', 'ctypes.dlopen', 'os.system'}:
        raise RuntimeError(f'Offline shell test forbids {event}')
    if event == 'subprocess.Popen':
        command = args[1]
        if (not isinstance(command, list) or len(command) < 2
                or command[0] != '/usr/bin/bash' or command[1] not in ALLOWED_SCRIPTS):
            raise RuntimeError('Only the copied shell fixtures may be launched')


sys.addaudithook(audit)


class DaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='monitor-daemon-test-', dir='/tmp')
        cls.root = Path(cls.temp.name)
        cls.bin = cls.root / 'bin'
        cls.bin.mkdir()
        (cls.bin / 'bash').symlink_to('/usr/bin/bash')
        (cls.bin / 'dirname').symlink_to('/usr/bin/dirname')
        # PATH has only these fakes and two harmless shell utilities. No real
        # sudo/systemctl, SSH, Python, or equipment executable is reachable.
        (cls.bin / 'sudo').write_text('''#!/usr/bin/bash
printf '%s\\t' sudo "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
[[ "$1" == -- && "$2" == systemctl ]] || exit 97
shift
exec "$@"
''')
        (cls.bin / 'systemctl').write_text('''#!/usr/bin/bash
printf '%s\\t' systemctl "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
exit "${FAKE_SYSTEMCTL_RC:-0}"
''')
        for name in ('sudo', 'systemctl'):
            (cls.bin / name).chmod(0o755)
        cls.projects = {}
        for name in ('lh2_leak_monitor', 'BLC_hv_monitoring', 'CVC_hv_monitoring',
                     'trip_monitor', 'unknown tool'):
            daemon = cls.root / name / 'daemon'
            daemon.mkdir(parents=True)
            for source in SOURCE.glob('*.sh'):
                destination = daemon / source.name
                text = source.read_text()
                if '/systemctl' in text or '/sudo' in text or 'sudo -n' in text:
                    raise RuntimeError('A shell helper bypasses the fake command lookup')
                shutil.copy2(source, destination)
                ALLOWED_SCRIPTS.add(str(destination))
            cls.projects[name] = daemon
        cls.log = cls.root / 'calls.tsv'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_helper(self, project, name, arguments=(), returncode=0):
        self.log.write_text('')
        environment = {'PATH': str(self.bin), 'FAKE_LOG': str(self.log),
                       'FAKE_SYSTEMCTL_RC': str(returncode), 'LANG': 'C'}
        result = subprocess.run(['/usr/bin/bash', str(self.projects[project] / name), *arguments],
                                env=environment, text=True, capture_output=True, timeout=5)
        calls = [line.rstrip('\t').split('\t') for line in self.log.read_text().splitlines()]
        return result, calls

    def test_default_targets_and_all_actions(self):
        for project, unit in [('lh2_leak_monitor', 'lh2_monitor.service'),
                              ('BLC_hv_monitoring', 'blc_monitor.service'),
                              ('CVC_hv_monitoring', 'ftof_monitor.service'),
                              ('trip_monitor', 'trip_monitor.service')]:
            for action in ('start', 'stop', 'status'):
                with self.subTest(project=project, action=action):
                    result, calls = self.run_helper(project, ('monitor.sh' if action == 'status' else action + '.sh'))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if action == 'status':
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(calls[0][:3], ['systemctl', 'show', unit])
                    else:
                        args = ['enable' if action == 'start' else 'disable', '--now', unit]
                        expected = [['systemctl', *args]]
                        if os.geteuid() != 0:
                            expected.insert(0, ['sudo', '--', 'systemctl', *args])
                        self.assertEqual(calls, expected)

    def test_explicit_target_works_in_parent_or_renamed_directory(self):
        for target, unit in [('lh2', 'lh2_monitor.service'), ('blc', 'blc_monitor.service'),
                             ('ftof', 'ftof_monitor.service'), ('trip', 'trip_monitor.service')]:
            with self.subTest(target=target):
                result, calls = self.run_helper('unknown tool', 'monitor.sh', [target])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[0][:3], ['systemctl', 'show', unit])

    def test_default_action_is_read_only_status(self):
        result, calls = self.run_helper('lh2_leak_monitor', 'monitor.sh')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0][:3], ['systemctl', 'show', 'lh2_monitor.service'])

    def test_invalid_actions_targets_and_extra_arguments_do_nothing(self):
        for arguments in (['destroy', 'lh2'], ['stop', 'other.service'],
                          ['stop', 'lh2; touch unexpected'], ['stop', 'lh2', 'extra']):
            with self.subTest(arguments=arguments):
                result, calls = self.run_helper('unknown tool', 'monitor.sh', arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_unknown_default_directory_does_nothing(self):
        result, calls = self.run_helper('unknown tool', 'stop.sh')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_help_does_not_access_systemd(self):
        result, calls = self.run_helper('unknown tool', 'monitor.sh', ['--help'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_backend_errors_are_not_hidden(self):
        for action in ('start', 'stop', 'status'):
            with self.subTest(action=action):
                result, _ = self.run_helper('lh2_leak_monitor', ('monitor.sh' if action == 'status' else action + '.sh'), returncode=7)
                self.assertEqual(result.returncode, 7)


if __name__ == '__main__':
    unittest.main(verbosity=2)
