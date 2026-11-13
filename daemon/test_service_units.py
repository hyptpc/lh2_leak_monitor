"""Offline service-definition/installer tests. Never contact systemd or hardware."""

import configparser
from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent


def guard(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'ctypes.dlopen'}:
        raise RuntimeError(f'Offline unit test forbids {event}')
    if event in {'open', 'os.remove', 'os.mkdir', 'os.rename'} and args:
        for arg in args[:2]:
            if isinstance(arg, (str, bytes, os.PathLike)):
                path = os.fsdecode(arg)
                if (path.startswith(('/etc/systemd/system', '/var/backups/monitor-services',
                                     '/home/sks/share/monitor-tmp/', '/misc/share/monitor-tmp/'))
                        or Path(path).name == '.env'):
                    raise RuntimeError(f'Production path forbidden: {path}')


sys.addaudithook(guard)
spec = importlib.util.spec_from_file_location('daemon_configure', HERE / 'configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class FakeTools:
    def __init__(self, states=None, verify_error=False, reload_error=False):
        self.states = states or {}
        self.verify_error = verify_error
        self.reload_error = reload_error
        self.calls = []

    def verify(self, paths):
        self.calls.append(('verify', [path.name for path in paths]))
        if self.verify_error:
            raise RuntimeError('simulated verify failure')

    def state(self, unit):
        self.calls.append(('state', unit))
        return self.states.get(unit, 'inactive')

    def reload(self):
        self.calls.append(('reload',))
        if self.reload_error:
            self.reload_error = False
            raise RuntimeError('simulated reload failure')


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monitor-unit-test-', dir='/tmp')
        root = Path(self.temp.name)
        self.destination = root / 'system'
        self.destination.mkdir()
        self.backups = root / 'backups'
        self.tools = FakeTools()

    def tearDown(self):
        self.temp.cleanup()

    def install(self, targets):
        with patch.object(configure.os, 'geteuid', return_value=0):
            return configure.install(targets, destination=self.destination,
                                     backup_root=self.backups, tools=self.tools)

    def test_all_units_track_python_and_wait_for_shared_mount(self):
        for name in configure.UNITS.values():
            text = (HERE / 'units' / name).read_text()
            unit = configparser.ConfigParser(interpolation=None, strict=False)
            unit.read_string(text)
            with self.subTest(unit=name):
                self.assertEqual(unit['Service']['Type'], 'exec')
                self.assertTrue(unit['Service']['ExecStart'].startswith('/home/sks/.venv/bin/python -u '))
                self.assertIn('/misc/share', unit['Unit']['RequiresMountsFor'])
                self.assertNotIn('tmux', text)
                self.assertNotIn('RemainAfterExit', text)
                self.assertNotIn('ExecStop=', text)
                self.assertEqual(unit['Service']['KillMode'], 'control-group')
                self.assertEqual(unit['Service']['KillSignal'], 'SIGINT')

    def test_lh2_never_restarts_and_operator_tmp_triggers_are_visible(self):
        text = (HERE / 'units/lh2_monitor.service').read_text()
        self.assertIn('Restart=no\n', text)
        self.assertIn('--startup-alert wait', text)
        self.assertIn('PrivateTmp=no', text)
        self.assertNotIn('SuccessExitStatus=2', text)
        self.assertNotIn('StartLimitBurst=', text)

    def test_continuous_monitors_have_finite_restart_budget(self):
        for target in ('blc', 'ftof', 'trip'):
            text = (HERE / 'units' / configure.UNITS[target]).read_text()
            with self.subTest(target=target):
                self.assertIn('Restart=always\n', text)
                self.assertIn('RestartSec=30\n', text)
                self.assertIn('StartLimitIntervalSec=1h\n', text)
                self.assertIn('StartLimitBurst=5\n', text)

    def test_existing_blc_ftof_trip_config_paths_are_preserved(self):
        expected = {'blc': '/home/sks/monitor_tools/BLC_hv_monitoring',
                    'ftof': '/home/sks/share/monitor-tools/CVC_hv_monitoring',
                    'trip': '/home/sks/monitor_tools/trip_monitor'}
        for target, directory in expected.items():
            text = (HERE / 'units' / configure.UNITS[target]).read_text()
            self.assertIn('WorkingDirectory=' + directory, text)
            self.assertIn(' param/conf.yml\n', text)

    def test_requires_operator_root_authentication(self):
        with patch.object(configure.os, 'geteuid', return_value=1000):
            with self.assertRaises(PermissionError):
                configure.install(['lh2'], tools=self.tools)
        self.assertEqual(self.tools.calls, [])

    def test_active_units_block_all_writes(self):
        old = self.destination / 'lh2_monitor.service'
        old.write_text('previous unit')
        self.tools.states['lh2_monitor.service'] = 'active'
        with self.assertRaisesRegex(RuntimeError, 'Stop the existing services'):
            self.install(['lh2', 'blc'])
        self.assertEqual(old.read_text(), 'previous unit')
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.destination / 'blc_monitor.service').exists())

    def test_install_backs_up_and_reloads_without_starting_or_enabling(self):
        old = self.destination / 'lh2_monitor.service'
        old.write_text('previous unit')
        self.tools.states['lh2_monitor.service'] = 'failed'
        backup = self.install(['lh2', 'blc'])
        self.assertEqual((backup / old.name).read_text(), 'previous unit')
        self.assertEqual((backup / old.name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(old.read_bytes(), (HERE / 'units' / old.name).read_bytes())
        self.assertEqual(self.tools.calls[-1], ('reload',))
        self.assertEqual(len([call for call in self.tools.calls if call[0] == 'reload']), 1)
        self.assertEqual({call[0] for call in self.tools.calls}, {'verify', 'state', 'reload'})

    def test_invalid_templates_prevent_writes(self):
        self.tools.verify_error = True
        with self.assertRaisesRegex(RuntimeError, 'verify failure'):
            self.install(['lh2'])
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(self.backups.exists())

    def test_reload_failure_restores_previous_units_and_removes_new_ones(self):
        old = self.destination / 'lh2_monitor.service'
        old.write_text('previous unit')
        old.chmod(0o640)
        self.tools.reload_error = True
        with self.assertRaisesRegex(RuntimeError, 'reload failure'):
            self.install(['lh2', 'blc'])
        self.assertEqual(old.read_text(), 'previous unit')
        self.assertEqual(old.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.destination / 'blc_monitor.service').exists())
        self.assertEqual(len([call for call in self.tools.calls if call[0] == 'reload']), 2)

    def test_symlinked_units_are_not_overwritten(self):
        original = self.destination / 'original'
        original.write_text('preserve me')
        (self.destination / 'lh2_monitor.service').symlink_to(original)
        with self.assertRaisesRegex(RuntimeError, 'Non-regular existing unit'):
            self.install(['lh2'])
        self.assertEqual(original.read_text(), 'preserve me')
        self.assertFalse(self.backups.exists())

    def test_target_selection_is_fixed_and_supports_tool_directories(self):
        self.assertEqual(configure.select_targets('CVC_hv_monitoring'), ['ftof'])
        self.assertEqual(configure.select_targets('all'), ['lh2', 'blc', 'ftof', 'trip'])
        for target in ('../../other.service', 'ssh', 'lh2; other'):
            with self.assertRaises(ValueError):
                configure.select_targets(target)

    def test_check_mode_only_validates(self):
        with patch.object(configure, 'SystemTools', return_value=self.tools), redirect_stdout(StringIO()):
            self.assertEqual(configure.main(['--check', 'all']), 0)
        self.assertEqual([call[0] for call in self.tools.calls], ['verify'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
