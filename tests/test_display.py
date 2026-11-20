"""Offline checks for terminal rendering, heartbeat timing and control isolation."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from io import StringIO
import logging
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from lh2_monitor.display import MonitorDisplay
from lh2_monitor.config import load_config
from lh2_monitor.engine import Engine
from lh2_monitor.simulation import VirtualClock, VirtualExecutor, VirtualNotifier, VirtualTriggers, status_reader


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.output = StringIO()
        self.base = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def display(self, terminal=False):
        self.output.isatty = lambda: terminal
        with patch.dict(os.environ, {'TERM': 'xterm'}):
            return MonitorDisplay(self.output, monotonic=self.clock.monotonic,
                                  wall_now=lambda: self.base + timedelta(seconds=self.clock.now))

    def test_terminal_refreshes_time_uptime_state_and_countdown(self):
        display = self.display(True)
        display.update(phase='ARMED', alert='0', last_valid='0', sample=True)
        self.clock.sleep(2)
        display.update(phase='WAIT', action='Waiting', remaining=65)
        text = self.output.getvalue()
        self.assertIn('12:00:02', text)
        self.assertIn('Uptime: 00:00:02', text)
        self.assertIn('Waiting... 00:01:05 remaining', text)
        self.assertIn('Status (Alert_H2leak): 0 (Normal)', text)
        self.assertIn('input is NOT re-read', text)
        self.assertEqual(text.count('\033[H\033[2J'), 2)

    def test_heartbeat_every_sixty_seconds_or_state_change(self):
        display = self.display()
        first = display.update(phase='ARMED', alert='0', sample=True)
        self.assertIn('HEARTBEAT', first)
        for _ in range(59):
            self.clock.sleep(1)
            self.assertIsNone(display.update(sample=True))
        self.clock.sleep(1)
        self.assertIn('uptime=00:01:00', display.update(sample=True))
        self.assertIn('phase=INPUT_ERROR', display.update(phase='INPUT_ERROR', alert=None))

    def test_file_or_journal_console_does_not_contain_terminal_codes(self):
        display = self.display()
        display.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        message = display.update(phase='ARMED', alert='0', sample=True)
        display.emit(logging.LogRecord('test', logging.INFO, '', 0, message, (), None))
        self.assertIn('INFO HEARTBEAT', self.output.getvalue())
        self.assertNotIn('\033', self.output.getvalue())

    def test_event_history_is_not_shown_but_current_errors_are_visible(self):
        display = self.display(True)
        display.emit(logging.LogRecord('test', logging.INFO, '', 0, 'old event noise', (), None))
        self.assertNotIn('Recent events', self.output.getvalue())
        self.assertNotIn('old event noise', self.output.getvalue())
        display.emit(logging.LogRecord('test', logging.ERROR, '', 0, 'Failure \033[31m', (), None))
        self.assertIn('Failure', self.output.getvalue())
        self.assertNotIn('Failure \033', self.output.getvalue())

    def test_historical_normal_label_only_for_valid_zero_in_armed_state(self):
        display = self.display(True)
        display.update(phase='ARMED', alert='0', input_error=None)
        self.assertIn('0 (Normal)', self.output.getvalue())
        self.assertNotIn('OOOOO', self.output.getvalue())
        for phase, alert in [('INPUT_ERROR', None), ('WAITING_FOR_ZERO', '1'), ('STOPPED', '0'), ('WAIT', '1')]:
            self.output.seek(0)
            self.output.truncate()
            display.update(phase=phase, alert=alert)
            self.assertNotIn('0 (Normal)', self.output.getvalue())

    def test_historical_fields_and_scheduled_shutdown_time(self):
        display = self.display(True)
        path = '/home/sks/share/monitor-tmp/H2tgtPresentStatus.txt'
        display.update(phase='ARMED', alert='0', monitoring_file=path, sample=True)
        self.assertIn('LH2 MONITOR', self.output.getvalue())
        self.assertIn('Monitoring file:', self.output.getvalue())
        self.assertIn(path, self.output.getvalue())
        self.assertIn('Last check: 2026-11-20 12:00:00', self.output.getvalue())
        self.output.seek(0)
        self.output.truncate()
        display.update(phase='WAIT', remaining=3600)
        self.assertIn('WAITING FOR FINAL SHUTDOWN', self.output.getvalue())
        self.assertIn('Final shutdown scheduled for:', self.output.getvalue())
        self.assertIn('2026-11-20 13:00:00', self.output.getvalue())
        self.assertNotIn('Recent events', self.output.getvalue())

    def test_wait_displays_real_config_commands_and_grace_only_cancel(self):
        display = self.display(True)
        controls = {'extend': '/tmp/test-extend', 'skip': '/tmp/test-skip', 'cancel': '/tmp/test-cancel'}
        with patch('lh2_monitor.display.shutil.get_terminal_size', return_value=os.terminal_size((50, 24))):
            display.update(phase='WAIT', alert='1', controls=controls, wait_minutes=60, skip_delay=5, remaining=120)
        text = self.output.getvalue()
        for path in controls.values():
            self.assertIn('touch ' + path, text)
        self.assertIn('restart full 60-min', text)
        self.assertIn('final power-off in 5s', text)
        self.assertIn('Cancel: stop remaining steps', text)
        self.assertLessEqual(len(text.splitlines()), 24)
        self.output.seek(0)
        self.output.truncate()
        display.update(phase='SKIP_GRACE', remaining=4)
        self.assertIn('touch /tmp/test-cancel', self.output.getvalue())
        self.assertNotIn('touch /tmp/test-extend', self.output.getvalue())
        self.assertNotIn('touch /tmp/test-skip', self.output.getvalue())

    def test_no_color_disables_color_without_disabling_refresh(self):
        with patch.dict(os.environ, {'NO_COLOR': ''}):
            display = self.display(True)
        display.update(phase='ARMED')
        self.assertIn('\033[H\033[2J', self.output.getvalue())
        self.assertNotIn('\033[32m', self.output.getvalue())

    def test_heartbeats_are_persisted_with_timestamp(self):
        from lh2_monitor.adapters import make_logger
        display = self.display()
        with tempfile.TemporaryDirectory() as folder, \
                patch('lh2_monitor.display.MonitorDisplay', return_value=display):
            path = Path(folder) / 'monitor.log'
            try:
                log = make_logger(path)
                log.progress(phase='ARMED', alert='0', sample=True)
                self.clock.sleep(60)
                log.progress(sample=True)
                log.progress(phase='STOPPED')
                text = path.read_text()
                self.assertEqual(text.count('HEARTBEAT:'), 3)
                self.assertIn('phase=STOPPED', text)
                self.assertIn('uptime=00:01:00', text)
                self.assertRegex(text, r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.*INFO HEARTBEAT')
                self.assertNotIn('\033', text)
            finally:
                logger = logging.getLogger('lh2_monitor')
                for handler in logger.handlers:
                    handler.close()
                logger.handlers.clear()

    def engine(self, progress):
        config = replace(load_config(Path(__file__).resolve().parent.parent / 'config/config.toml'),
                         wait_seconds=3)
        self.events = []
        self.executor = VirtualExecutor(self.clock)
        log = lambda level, message: self.events.append((level, message))
        return Engine(config, self.executor, VirtualNotifier(log), self.clock,
                      VirtualTriggers(self.clock), log, progress=progress)

    def test_display_failure_does_not_prevent_shutdown(self):
        def broken(**state):
            raise OSError('broken console')
        engine = self.engine(broken)
        self.assertEqual(engine.monitor(status_reader(['0', '1']), lambda: 'wait'), 0)
        self.assertEqual(self.executor.calls[-1][1], 'kikusui42')
        self.assertEqual(sum('display unavailable' in message for _, message in self.events), 1)

    def test_progress_reports_bad_input_recovery_and_latched_countdown(self):
        reports = []
        engine = self.engine(lambda **state: reports.append(state))
        self.assertEqual(engine.monitor(status_reader(['1', None, '0', '1']), lambda: 'wait'), 0)
        phases = [s.get('phase') for s in reports]
        for name in ['WAITING_FOR_ZERO', 'INPUT_ERROR', 'ARMED', 'SHUTDOWN', 'WAIT']:
            self.assertIn(name, phases)
        invalid = next(s for s in reports if s.get('phase') == 'INPUT_ERROR')
        self.assertIsNone(invalid['alert'])
        self.assertEqual(invalid['last_valid'], '1')
        wait = [s['remaining'] for s in reports if s.get('phase') == 'WAIT']
        self.assertEqual(wait, [3, 2, 1, 0])
        self.assertFalse(any(s.get('sample') for s in reports[phases.index('WAIT'):]))


if __name__ == '__main__':
    unittest.main()
