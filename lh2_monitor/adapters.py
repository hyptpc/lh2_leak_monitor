"""Live process, input, trigger, notification and logging adapters."""

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .engine import ActionError, CommandResult, Status


ROOT = Path(__file__).resolve().parent.parent


def read_status(path):
    try:
        with Path(path).open(encoding='utf-8') as stream:
            values = [line.split(':', 1)[1].strip() for line in stream
                      if line.strip().startswith('Alert_H2leak:')]
        if len(values) != 1 or values[0] not in {'0', '1'}:
            return Status(None, 'Expected exactly one Alert_H2leak value of 0 or 1')
        return Status(values[0])
    except (OSError, UnicodeError) as error:
        return Status(None, f'{type(error).__name__}: {error}')


class FileTriggers:
    def __init__(self, paths, log):
        self.paths = paths
        self.log = log

    def consume(self, name):
        try:
            self.paths[name].unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            # Fail visibly instead of repeatedly extending the timer forever.
            raise RuntimeError(f'Cannot consume {name} trigger: {error}') from error

    def clear(self):
        for name in self.paths:
            if self.consume(name):
                self.log('WARNING', f'Removed old trigger file: {self.paths[name]}')


class ProcessExecutor:
    def __init__(self, log):
        self.log = log

    def run_once(self, action, policy):
        timeout = policy.attempt_timeout_seconds
        if action.kind == 'massflow_pair':
            # Two access checks + two close operations, each with its own retry
            # budget. The worker itself enforces each communication deadline.
            timeout = 4 * policy.total_seconds + 5
        request = {'kind': action.kind, 'parameters': action.parameters,
                   'timeout': policy.attempt_timeout_seconds}
        try:
            process = subprocess.Popen(
                [sys.executable, '-m', 'lh2_monitor.worker', '--live-worker'],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError as error:
            raise ActionError(f'Cannot start worker: {error}') from error
        try:
            output, errors = process.communicate(json.dumps(request), timeout=timeout)
        except BaseException as error:
            # Include SSH descendants, not only the Python worker. A timeout
            # cannot establish whether a remote command took effect.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, errors = process.communicate()
            if output.strip():
                self.log('INFO', f'{action.id}: {output.strip()}')
            if isinstance(error, subprocess.TimeoutExpired):
                raise ActionError(f'Timeout after {timeout:g}s; equipment result UNKNOWN') from error
            raise
        for line in output.splitlines():
            if line and not line.startswith('LH2_RESULT_JSON:'):
                self.log('INFO', f'{action.id}: {line}')
        if process.returncode:
            raise ActionError(errors.strip() or f'Worker exited {process.returncode}')
        records = [line.removeprefix('LH2_RESULT_JSON:') for line in output.splitlines()
                   if line.startswith('LH2_RESULT_JSON:')]
        if len(records) != 1:
            raise ActionError('Worker did not return exactly one structured result')
        try:
            result = json.loads(records[0])
            detail = result['detail']
            channels = result.get('channels', [])
            if not isinstance(detail, str) or not isinstance(channels, list):
                raise ValueError('Invalid worker result types')
            if action.kind == 'caen_inventory':
                if (not channels or any(type(ch) is not int or not 0 <= ch <= 255 for ch in channels)
                        or len(channels) != len(set(channels))):
                    raise ValueError('Invalid CAEN channel inventory')
            return CommandResult(detail, tuple(channels))
        except (ValueError, KeyError, TypeError) as error:
            raise ActionError(f'Invalid worker result: {error}') from error


class DiscordNotifier:
    """Best-effort delivery never blocks monitoring or shutdown.

    Only a daemon delivery thread imports/uses networking. Missing configuration
    is a warning rather than a reason to disable equipment monitoring.
    """

    def __init__(self, url, timeout, log):
        self.url = url
        self.timeout = timeout
        self.log = log
        self.threads = []
        if not url:
            log('WARNING', 'Discord webhook is not configured; notifications disabled')

    def send(self, message):
        if not self.url:
            return
        thread = threading.Thread(target=self._deliver, args=(message,), daemon=True)
        thread.start()
        self.threads.append(thread)

    def finish(self):
        # Only after equipment work finishes. Bound even final delivery waiting.
        deadline = time.monotonic() + self.timeout
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self.threads):
            self.log('WARNING', 'Exiting with an unfinished Discord delivery')

    def _deliver(self, message):
        from urllib.request import Request, urlopen

        try:
            content = f'[{time.ctime()}] {message}'
            if len(content) > 2000:
                content = content[:1950] + ' ... (full details in monitor log)'
            request = Request(self.url, data=json.dumps({
                'content': content, 'username': 'LH2 Monitor Bot',
            }).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 204:
                    raise RuntimeError(f'HTTP {response.status}')
            self.log('INFO', 'Discord notification delivered')
        except Exception as error:
            # Do not log a webhook URL/token from exception text.
            self.log('WARNING', f'Discord delivery failed ({type(error).__name__})')


def startup_choice(input_stream=sys.stdin, output=sys.stdout):
    if not input_stream.isatty():
        return 'wait'
    print('Alert is already 1. [q] Exit monitor without commands / '
          '[Enter] Wait for a new 0 -> 1: ', end='', file=output, flush=True)
    answer = input_stream.readline().strip().lower()
    return 'exit' if answer in {'q', 'quit', 'exit'} else 'wait'


def make_logger(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('lh2_monitor')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(path)):
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    def log(level, message):
        logger.log(getattr(logging, level), message)
    return log


class MonitorLock:
    """Prevent two live monitor processes from operating the same equipment."""

    def __init__(self, path):
        self.path = path
        self.stream = None

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+')
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError('Another live monitor holds the monitor lock') from None
        return self

    def __exit__(self, *args):
        self.stream.close()
