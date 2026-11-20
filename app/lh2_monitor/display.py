"""Terminal dashboard and bounded heartbeat output; never performs equipment I/O."""

from datetime import datetime
import logging
import math
import os
import shutil
import shlex
import threading
import time


PHASES = {
    'STARTING': 'STARTING', 'ARMED': 'MONITORING / waiting for 0 -> 1',
    'WAITING_FOR_ZERO': 'WAITING FOR 0 / startup alert already 1',
    'STARTUP_ALERT': 'STARTUP ALERT / operator choice',
    'INPUT_ERROR': 'INPUT ERROR / waiting for valid input',
    'SHUTDOWN': 'SHUTDOWN / sequence latched',
    'WAIT': 'WAIT / before final power-off', 'SKIP_GRACE': 'SKIP GRACE',
    'STOPPED': 'STOPPED',
}


def clean(value):
    return ''.join(char if char.isprintable() else ' ' for char in str(value))


def duration(seconds):
    seconds = max(0, math.ceil(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


class MonitorDisplay(logging.Handler):
    def __init__(self, stream, *, monotonic=time.monotonic, wall_now=None):
        super().__init__()
        self.stream = stream
        self.monotonic = monotonic
        self.wall_now = wall_now or (lambda: datetime.now().astimezone())
        self.started = monotonic()
        self.terminal = stream.isatty() and os.environ.get('TERM') != 'dumb'
        self.color = self.terminal and 'NO_COLOR' not in os.environ
        self.state = {'phase': 'STARTING', 'alert': None, 'last_valid': None,
                      'action': '', 'remaining': None, 'input_error': None}
        self.sample_time = 'not yet read'
        self.problem = None
        self._next_heartbeat = self.started
        self._previous_key = None
        self._render_lock = threading.RLock()

    def update(self, **changes):
        with self._render_lock:
            if changes.pop('sample', False):
                self.sample_time = self.wall_now().strftime('%Y-%m-%d %H:%M:%S %Z')
            self.state.update(changes)
            now = self.monotonic()
            state = self.state
            key = tuple(state.get(name) for name in ('phase', 'alert', 'input_error', 'action'))
            message = None
            if key != self._previous_key or now >= self._next_heartbeat:
                message = (f"HEARTBEAT: phase={state['phase']} Alert_H2leak={state['alert'] if state['alert'] is not None else 'UNKNOWN'}; "
                           f"uptime={duration(now - self.started)}; last_input_read={self.sample_time}; "
                           f"action={clean(state['action']) or '-'}")
                if state['remaining'] is not None:
                    message += f"; remaining={duration(state['remaining'])}"
                if state['input_error']:
                    message += f"; input_error={clean(state['input_error'])}"
                self._previous_key = key
                self._next_heartbeat = now + 60
            if self.terminal:
                self.render()
            return message

    def render(self):
        # Called by the control loop, never by a timer that could mask a stuck loop.
        width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
        s = self.state
        now = self.wall_now().strftime('%Y-%m-%d %H:%M:%S %Z')
        normal = s['phase'] == 'ARMED' and s['alert'] == '0' and not s['input_error'] and not self.problem
        if normal:
            banner = [
                '   OOOOO    KK   KK',
                '  OO   OO   KK KK',
                '  OO   OO   KKK',
                '  OO   OO   KK KK',
                '   OOOOO    KK   KK',
                '  NORMAL / MONITORING',
            ]
            code = '1;32'
        else:
            title = {'WAIT': 'SHUTDOWN WAIT', 'SKIP_GRACE': 'FINAL POWER-OFF SOON',
                     'INPUT_ERROR': 'INPUT ERROR', 'SHUTDOWN': 'SHUTDOWN IN PROGRESS',
                     'WAITING_FOR_ZERO': 'WAITING FOR ALERT = 0', 'STARTUP_ALERT': 'ALERT ALREADY 1',
                     'STARTING': 'STARTING', 'STOPPED': 'STOPPED'}.get(s['phase'], 'CHECK STATUS')
            if self.problem:
                title = 'COMMAND / MONITOR ERROR' if s['phase'] != 'STOPPED' else 'STOPPED WITH ERROR'
            border = '=' * min(width, max(24, len(title) + 4))
            banner = [border, title, border]
            code = '1;31' if s['phase'] in {'INPUT_ERROR', 'SHUTDOWN', 'SKIP_GRACE'} or self.problem else '1;33'
        lines = [f'LH2 MONITOR | LIVE | PID {os.getpid()}',
                 f'Updated: {now}', f'Uptime: {duration(self.monotonic() - self.started)}', '']
        start_banner = len(lines)
        lines.extend(banner)
        end_banner = len(lines)
        lines.append('')
        alert = s['alert'] if s['alert'] is not None else 'UNKNOWN'
        if s['phase'] in {'SHUTDOWN', 'WAIT', 'SKIP_GRACE'}:
            lines.append('Alert latched; input is NOT re-read.')
        else:
            lines.append(f'Alert_H2leak: {alert}')
            if s['input_error']:
                lines.append(f"Last valid: {s['last_valid'] if s['last_valid'] is not None else '-'}")
            lines.append(f'Last input read: {self.sample_time}')
        if s['input_error']:
            lines.append(f"Input error: {s['input_error']}")
        if s['remaining'] is not None:
            lines.append(f"Remaining: {duration(s['remaining'])}")
        controls = s.get('controls', {})
        if s['phase'] == 'WAIT':
            lines += ['', 'Run in right pane (no sudo):']
            for name, effect in [('extend', f"Extend: restart full {s.get('wait_minutes', 60):g}-min wait"),
                                 ('skip', f"Skip: final power-off in {s.get('skip_delay', 5):g}s"),
                                 ('cancel', 'Cancel: stop remaining steps')]:
                if name in controls:
                    lines += [effect, '  touch ' + shlex.quote(controls[name])]
            lines.append('No action -> final power-off at zero.')
        elif s['phase'] == 'SKIP_GRACE':
            lines.append('Cancel remaining steps (right pane):')
            if 'cancel' in controls:
                lines.append('  touch ' + shlex.quote(controls['cancel']))
        elif s['action']:
            lines.append(f"Current action: {s['action']}")
        if self.problem:
            lines.append('Error: ' + self.problem)
        lines += ['', 'Ctrl+C: stop monitor; no power restored.']
        lines = [clean(line)[:width] for line in lines]
        if self.color:
            for index in range(start_banner, end_banner):
                lines[index] = f'\033[{code}m{lines[index]}\033[0m'
        self.stream.write('\033[H\033[2J' + '\n'.join(lines) + '\n')
        self.stream.flush()

    def emit(self, record):
        try:
            with self._render_lock:
                text = self.format(record)
                if self.terminal:
                    if record.levelno >= logging.ERROR:
                        self.problem = clean(record.getMessage())
                    self.render()
                else:
                    self.stream.write(text + '\n')
                    self.stream.flush()
        except Exception:
            # Match StreamHandler: terminal presentation errors do not abort controls.
            self.handleError(record)
