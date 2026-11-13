"""Entirely in-memory equipment, clock, inputs, triggers and notifications."""

from collections import deque

from .engine import ActionError, CommandResult, Status


class VirtualClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError('Cannot move virtual time backwards')
        self.now += seconds


class VirtualTriggers:
    def __init__(self, clock, events=()):
        self.clock = clock
        self.events = list(events)

    def consume(self, name):
        for index, event in enumerate(self.events):
            if event['name'] == name and event['at'] <= self.clock.monotonic():
                self.events.pop(index)
                return True
        return False


class VirtualNotifier:
    def __init__(self, log):
        self.log = log
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        self.log('INFO', f'[SIMULATED notification; NOT SENT] {message}')


class VirtualExecutor:
    def __init__(self, clock, responses=None, duration_seconds=0, caen_channels=2):
        self.clock = clock
        self.responses = {key: deque(values) for key, values in (responses or {}).items()}
        self.duration_seconds = duration_seconds
        self.caen_channels = caen_channels
        self.calls = []

    def run_once(self, action, policy):
        self.calls.append((self.clock.monotonic(), action.id))
        self.clock.sleep(self.duration_seconds)
        outcomes = self.responses.get(action.id)
        outcome = outcomes.popleft() if outcomes else 'ok'
        if outcome != 'ok':
            if outcome == 'timeout':
                self.clock.sleep(policy.attempt_timeout_seconds)
            raise ActionError(f'SIMULATED {outcome}')
        return CommandResult('SIMULATED command success; no equipment contacted',
                             tuple(range(self.caen_channels)) if action.kind == 'caen_inventory' else ())


def status_reader(values):
    values = iter(values)

    def read():
        try:
            value = next(values)
        except StopIteration:
            raise RuntimeError('Simulation input exhausted before a trigger/exit; add status values') from None
        return Status(value) if value in {'0', '1'} else Status(None, f'SIMULATED invalid input: {value!r}')
    return read
