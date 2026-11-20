"""Terminal dashboard and bounded heartbeat output; never performs equipment I/O."""

from collections import deque
from datetime import datetime
import logging
import math
import os
import shutil
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
        self.events = deque(maxlen=8)
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
        phase = PHASES.get(s['phase'], s['phase'])
        alert = s['alert'] if s['alert'] is not None else 'UNKNOWN'
        lines = [
            f'LH2 LEAK MONITOR | LIVE | PID {os.getpid()}',
            f"Updated: {now}  |  Uptime: {duration(self.monotonic() - self.started)}",
            f"State: {phase}",
            f"Alert_H2leak: {alert}  |  Last valid: {s['last_valid'] if s['last_valid'] is not None else '-'}",
            f"Last input read: {self.sample_time}",
            f"Current action: {s['action'] or '-'}",
            f"Remaining: {duration(s['remaining']) if s['remaining'] is not None else '-'}",
        ]
        if s['input_error']:
            lines.append(f"Input error: {s['input_error']}")
        if s['phase'] in {'SHUTDOWN', 'WAIT', 'SKIP_GRACE'}:
            lines.append('Alert is latched; input is NOT re-read during the shutdown sequence.')
        lines += ['Ctrl+C: stop monitor (does not restore equipment power)', '', 'Recent events:']
        lines += list(self.events)
        lines = [clean(line)[:width] for line in lines]
        if self.color:
            code = '31' if s['phase'] in {'INPUT_ERROR', 'SHUTDOWN', 'SKIP_GRACE'} else '32'
            lines[2] = f'\033[{code}m{lines[2]}\033[0m'
        self.stream.write('\033[H\033[2J' + '\n'.join(lines) + '\n')
        self.stream.flush()

    def emit(self, record):
        try:
            with self._render_lock:
                text = self.format(record)
                if self.terminal:
                    self.events.extend(text.splitlines())
                    self.render()
                else:
                    self.stream.write(text + '\n')
                    self.stream.flush()
        except Exception:
            # Match StreamHandler: terminal presentation errors do not abort controls.
            self.handleError(record)
