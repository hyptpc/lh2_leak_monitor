from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

import monitor
import turn_off_hv
from lh2_monitor.adapters import (
    DiscordNotifier, FileTriggers, MonitorLock, ProcessExecutor, read_status, startup_choice,
)
from lh2_monitor.config import Action, RetryPolicy, load_config
from lh2_monitor.engine import ActionError, Engine
from lh2_monitor.hardware import caen_operation, hv_off, kikusui_off
from lh2_monitor.simulation import VirtualClock, VirtualExecutor, VirtualTriggers
from lh2_monitor import worker


ROOT = Path(__file__).resolve().parent.parent


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.log = lambda *args: None

    def test_reader_rejects_missing_invalid_and_duplicate_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.txt'
            self.assertIsNotNone(read_status(path).error)
            for content in ('Time: 0\n', 'Alert_H2leak: \n', 'Alert_H2leak: 2\n',
                            'Alert_H2leak: 0\nAlert_H2leak: 1\n'):
                path.write_text(content)
                self.assertIsNotNone(read_status(path).error)
            path.write_text('Time: old\nAlert_H2leak: 0\n')
            os.utime(path, (1, 1))
            self.assertEqual(read_status(path).value, '0')

    def test_noninteractive_startup_defaults_to_wait_without_reading_input(self):
        stream = MagicMock()
        stream.isatty.return_value = False
        self.assertEqual(startup_choice(stream, StringIO()), 'wait')
        stream.readline.assert_not_called()

    def test_interactive_startup_can_exit(self):
        stream = MagicMock()
        stream.isatty.return_value = True
        stream.readline.return_value = 'q\n'
        self.assertEqual(startup_choice(stream, StringIO()), 'exit')

    def test_consuming_a_trigger_is_one_shot_and_startup_clears_old_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {key: Path(directory) / key for key in ('cancel', 'skip', 'extend')}
            triggers = FileTriggers(paths, self.log)
            paths['skip'].touch()
            self.assertTrue(triggers.consume('skip'))
            self.assertFalse(triggers.consume('skip'))
            for path in paths.values():
                path.touch()
            triggers.clear()
            self.assertFalse(any(path.exists() for path in paths.values()))

    def test_duplicate_monitor_cannot_acquire_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'monitor.lock'
            with MonitorLock(path):
                with self.assertRaisesRegex(RuntimeError, 'Another live monitor'):
                    with MonitorLock(path):
                        self.fail('Second lock acquired')
            with MonitorLock(path):
                pass

    def test_worker_timeout_kills_entire_process_group_and_reports_unknown_result(self):
        process = MagicMock(pid=4321)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', 10), ('', '')]
        action = Action('test', 'Test HV', 'hv', {})
        with patch('lh2_monitor.adapters.subprocess.Popen', return_value=process) as spawn:
            with patch('lh2_monitor.adapters.os.killpg') as kill:
                with self.assertRaisesRegex(ActionError, 'result UNKNOWN'):
                    ProcessExecutor(self.log).run_once(action, RetryPolicy())
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.args[0][0], sys.executable)
        self.assertEqual(process.communicate.call_args_list[0].kwargs['timeout'], 10)
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0], 4321)

    def test_worker_success_requires_structured_result(self):
        process = MagicMock(returncode=0)
        process.communicate.return_value = ('looks successful but has no result', '')
        with patch('lh2_monitor.adapters.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(ActionError, 'structured result'):
                ProcessExecutor(self.log).run_once(Action('test', 'test', 'hv', {}), RetryPolicy())

    def test_caen_inventory_result_is_validated(self):
        process = MagicMock(returncode=0)
        action = Action('inventory', 'Test', 'caen_inventory', {})
        for channels in ([0, 1], [], [0, 0], [-1], ['1']):
            process.communicate.return_value = (
                'LH2_RESULT_JSON:' + json.dumps({'detail': 'test', 'channels': channels}), '')
            with patch('lh2_monitor.adapters.subprocess.Popen', return_value=process):
                if channels == [0, 1]:
                    result = ProcessExecutor(self.log).run_once(action, RetryPolicy())
                    self.assertEqual(result.channels, (0, 1))
                else:
                    with self.assertRaises(ActionError):
                        ProcessExecutor(self.log).run_once(action, RetryPolicy())

    def test_missing_webhook_does_not_disable_monitoring(self):
        notifier = DiscordNotifier(None, 10, self.log)
        notifier.send('must not send')
        notifier.finish()
        self.assertEqual(notifier.threads, [])

    def test_stalled_notification_does_not_delay_equipment_actions(self):
        config = load_config(ROOT / 'config.toml')
        clock = VirtualClock()
        executor = VirtualExecutor(clock)
        notifier = DiscordNotifier('https://example.invalid/not-used', 0.01, self.log)
        release = threading.Event()
        with patch.object(notifier, '_deliver', side_effect=lambda message: release.wait(1)):
            try:
                engine = Engine(config, executor, notifier, clock, VirtualTriggers(clock), self.log)
                self.assertEqual(engine.run_sequence().exit_code, 0)
                self.assertEqual(executor.calls[-1][1], 'kikusui42')
                self.assertFalse(release.is_set())
            finally:
                release.set()
                for thread in notifier.threads:
                    thread.join(1)

    def test_notification_queue_failure_does_not_stop_shutdown(self):
        config = load_config(ROOT / 'config.toml')
        clock = VirtualClock()
        executor = VirtualExecutor(clock)
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError('Cannot start notification thread')
        engine = Engine(config, executor, notifier, clock, VirtualTriggers(clock), self.log)
        self.assertEqual(engine.run_sequence().exit_code, 0)
        self.assertEqual(executor.calls[-1][1], 'kikusui42')

    def test_failed_thread_start_does_not_leave_an_unjoinable_notification(self):
        notifier = DiscordNotifier('https://example.invalid/not-used', 0.01, self.log)
        thread = MagicMock()
        thread.start.side_effect = RuntimeError('Cannot start thread')
        with patch('lh2_monitor.adapters.threading.Thread', return_value=thread):
            with self.assertRaises(RuntimeError):
                notifier.send('test')
        notifier.finish()
        self.assertEqual(notifier.threads, [])
        thread.join.assert_not_called()

    def test_exit_wait_for_notification_is_bounded(self):
        events = []
        notifier = DiscordNotifier('https://example.invalid/not-used', 0.001,
                                   lambda *entry: events.append(entry))
        release = threading.Event()
        with patch.object(notifier, '_deliver', side_effect=lambda message: release.wait(1)):
            try:
                notifier.send('not actually delivered')
                notifier.finish()
                self.assertTrue(any(thread.is_alive() for thread in notifier.threads))
                self.assertTrue(any('unfinished Discord' in entry[1] for entry in events))
            finally:
                release.set()
                for thread in notifier.threads:
                    thread.join(1)

    def test_explicit_simulation_is_virtual_even_with_webhook_environment(self):
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://example.invalid/not-used'}):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(monitor.main(['--simulate']), 0)
        self.assertIn('VIRTUAL SIMULATION', output.getvalue())
        self.assertIn('NOT SENT', output.getvalue())

    def test_normal_startup_dispatches_to_live_mode_without_running_it(self):
        with patch.object(monitor, 'run_live', return_value=0) as live:
            self.assertEqual(monitor.main([]), 0)
        live.assert_called_once()
        self.assertEqual(live.call_args.args[1], 'ask')

    def test_scenario_requires_explicit_simulation_and_never_dispatches_live(self):
        with patch.object(monitor, 'run_live') as live, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                monitor.main(['--scenario', str(ROOT / 'debug/scenarios/cancel.json')])
        self.assertEqual(error.exception.code, 2)
        live.assert_not_called()

    def test_plan_display_does_not_touch_equipment(self):
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(monitor.main(['--show-plan']), 0)
        self.assertIn('192.168.20.91', output.getvalue())
        self.assertIn('60 minutes', output.getvalue())

    def test_all_documented_scenarios_run_under_the_offline_guard(self):
        for name, expected in [('failure', 1), ('cancel', 2), ('startup-alert', 0)]:
            with self.subTest(name=name), redirect_stdout(StringIO()):
                self.assertEqual(monitor.main([
                    '--simulate', '--scenario', str(ROOT / 'debug/scenarios' / f'{name}.json'),
                ]), expected)

    def test_compatibility_hv_tool_has_explicit_virtual_mode(self):
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(turn_off_hv.main(['--ip_last', '12', '--port', '0', '--simulate']), 0)
        self.assertIn('no command sent', output.getvalue())

    def test_worker_propagates_exception_as_nonzero_exit(self):
        request = json.dumps({'kind': 'caen', 'parameters': {}, 'timeout': 10})
        with patch.object(sys, 'argv', ['worker', '--live-worker']):
            with patch.object(sys, 'stdin', StringIO(request)):
                with patch('lh2_monitor.hardware.perform', side_effect=RuntimeError('simulated CAEN error')):
                    with redirect_stderr(StringIO()) as errors:
                        self.assertEqual(worker.main(), 1)
        self.assertIn('simulated CAEN error', errors.getvalue())

    def test_worker_without_explicit_live_flag_does_nothing(self):
        with patch.object(sys, 'argv', ['worker']):
            with redirect_stderr(StringIO()):
                self.assertEqual(worker.main(), 2)


class HardwareAdapterTests(unittest.TestCase):
    def test_http_failure_does_not_count_as_hv_success(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 503
        with patch('lh2_monitor.hardware.http.client.HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
                hv_off({'host': '192.0.2.1', 'port': 8000, 'target_port': 0}, 10)
        connection.close.assert_called_once()

    def test_kikusui_requires_off_acknowledgment(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = [b'1\n']
        with patch('lh2_monitor.hardware.socket.create_connection', return_value=sock):
            with self.assertRaisesRegex(RuntimeError, 'did not confirm OFF'):
                kikusui_off({'host': '192.0.2.1', 'port': 5025}, 10)
        self.assertEqual([call.args[0] for call in sock.sendall.call_args_list],
                         [b'OUTP OFF\n', b'OUTP?\n'])

    def test_caen_inventory_sends_no_shutdown_command_and_errors_propagate(self):
        device = MagicMock()
        device.get_crate_map.return_value = [types.SimpleNamespace(slot=4, n_channel=2)]
        context = MagicMock()
        context.__enter__.return_value = device
        hv = types.SimpleNamespace(
            Device=types.SimpleNamespace(open=MagicMock(return_value=context)),
            SystemType=types.SimpleNamespace(SY1527='SY1527'),
            LinkType=types.SimpleNamespace(TCPIP='TCPIP'))
        module = types.ModuleType('caen_libs')
        module.caenhvwrapper = hv
        with patch.dict(sys.modules, {'caen_libs': module}):
            result = caen_operation({'host': '192.0.2.1', 'slot': 4}, 10, inventory=True)
            self.assertEqual(result['channels'], [0, 1])
            device.set_ch_param.assert_not_called()
            device.set_ch_param.side_effect = RuntimeError('simulated write failure')
            with self.assertRaisesRegex(RuntimeError, 'simulated write failure'):
                caen_operation({'host': '192.0.2.1', 'slot': 4, 'channel': 0}, 10)


if __name__ == '__main__':
    unittest.main()
