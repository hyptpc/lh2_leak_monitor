"""Terminal dashboard and bounded heartbeat output; never performs equipment I/O."""

from datetime import datetime, timedelta
import logging
import math
import os
import shutil
import shlex
import threading
import textwrap
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
        # Match the compact Nov 2025 interface, without ASCII art or event history.
        width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
        s = self.state
        now = self.wall_now()
        stamp = now.strftime('%Y-%m-%d %H:%M:%S %Z')
        phase = s['phase']
        normal = phase == 'ARMED' and s['alert'] == '0' and not s['input_error'] and not self.problem
        alert = s['alert'] if s['alert'] is not None else 'UNKNOWN'
        highlighted = {}
        if phase == 'WAIT':
            lines = ['WAITING FOR FINAL SHUTDOWN', '']
            highlighted[0] = '1;36'
            if s['remaining'] is not None:
                scheduled = (now + timedelta(seconds=s['remaining'])).strftime('%Y-%m-%d %H:%M:%S %Z')
                lines += ['Final shutdown scheduled for:', scheduled]
            lines += [f'Last check: {stamp}', f'Uptime: {duration(self.monotonic() - self.started)}', '']
            highlighted[len(lines)] = '1;33'
            lines.append(f"Waiting... {duration(s['remaining'] or 0)} remaining")
            lines += ['', 'Trigger commands (right pane; no sudo):']
            controls = s.get('controls', {})
            for name, effect in [('skip', f"Skip: final power-off in {s.get('skip_delay', 5):g}s"),
                                 ('cancel', 'Cancel: stop remaining steps'),
                                 ('extend', f"Extend: restart full {s.get('wait_minutes', 60):g}-min wait")]:
                if name in controls:
                    lines += [effect, '  touch ' + shlex.quote(controls[name])]
            lines += ['', 'No action -> final power-off at zero.', 'Alert is latched; input is NOT re-read.']
        else:
            lines = ['LH2 MONITOR', '']
            highlighted[0] = '1;35'
            if normal:
                highlighted[2] = '1;32'
                lines.append('Status (Alert_H2leak): 0 (Normal)')
            else:
                text = {'STARTING': 'Starting monitor', 'WAITING_FOR_ZERO': 'ALERT DETECTED - waiting for 0',
                        'STARTUP_ALERT': 'ALERT DETECTED - startup choice', 'INPUT_ERROR': 'INPUT ERROR',
                        'SHUTDOWN': 'SHUTDOWN IN PROGRESS', 'SKIP_GRACE': 'FINAL POWER-OFF SOON',
                        'STOPPED': 'Monitoring stopped.'}.get(phase, PHASES.get(phase, phase))
                if self.problem:
                    text = 'STOPPED WITH ERROR' if phase == 'STOPPED' else 'COMMAND / MONITOR ERROR'
                highlighted[2] = '1;31' if self.problem or phase in {'INPUT_ERROR', 'SHUTDOWN', 'SKIP_GRACE'} else '1;33'
                lines.append(text)
                lines.append(f'Status (Alert_H2leak): {alert}')
            if phase not in {'SHUTDOWN', 'SKIP_GRACE'}:
                if s.get('monitoring_file'):
                    lines += ['Monitoring file:', s['monitoring_file']]
                lines.append(f'Last check: {stamp}')
                lines.append(f'Last input read: {self.sample_time}')
            else:
                lines += [f'Last check: {stamp}', 'Alert is latched; input is NOT re-read.']
            lines.append(f'Uptime: {duration(self.monotonic() - self.started)}')
            if s['input_error']:
                lines += [f"Last valid: {s['last_valid'] if s['last_valid'] is not None else '-'}",
                          f"Input error: {s['input_error']}"]
            if s['remaining'] is not None:
                lines.append(f"Remaining: {duration(s['remaining'])}")
            if phase == 'SKIP_GRACE' and 'cancel' in s.get('controls', {}):
                lines += ['', 'Cancel remaining steps (right pane):',
                          '  touch ' + shlex.quote(s['controls']['cancel'])]
            elif s['action']:
                lines.append(f"Current action: {s['action']}")
            if phase != 'STOPPED':
                lines += ['', '(Monitoring... Ctrl+C to stop)']
        if self.problem:
            lines.append('Error: ' + self.problem)
        # Wrap long paths/details instead of silently clipping the operator commands.
        rendered = []
        for index, line in enumerate(lines):
            chunks = textwrap.wrap(clean(line), width=width, replace_whitespace=False,
                                   drop_whitespace=False) or ['']
            if self.color and index in highlighted:
                chunks = [f'\033[{highlighted[index]}m{chunk}\033[0m' for chunk in chunks]
            rendered.extend(chunks)
        self.stream.write('\033[H\033[2J' + '\n'.join(rendered) + '\n')
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
