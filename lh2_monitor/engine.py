"""Control flow only; equipment, notification, filesystem and clock I/O are injected."""

from dataclasses import dataclass, replace

from .config import Action


class ActionError(Exception):
    """An action failed or its result is unknown (for example, after timeout)."""


@dataclass(frozen=True)
class CommandResult:
    detail: str
    channels: tuple = ()


@dataclass(frozen=True)
class Status:
    value: str | None
    error: str | None = None


@dataclass(frozen=True)
class Outcome:
    canceled: bool
    errors: tuple

    @property
    def exit_code(self):
        return 2 if self.canceled else (1 if self.errors else 0)


class Engine:
    def __init__(self, config, executor, notifier, clock, triggers, log):
        self.config = config
        self.executor = executor
        self.notifier = notifier
        self.clock = clock
        self.triggers = triggers
        self.log = log

    def execute(self, action):
        # The pair worker owns per-connection / per-valve retries. Never retry the
        # whole pair: doing so would repeat the already-successful side as well.
        attempts = 1 if action.kind == 'massflow_pair' else self.config.retry.max_attempts
        last_error = ''
        for attempt in range(1, attempts + 1):
            self.log('INFO', f'{action.id}: {action.name}; attempt {attempt}/{attempts}')
            try:
                result = self.executor.run_once(action, self.config.retry)
                self.log('INFO', f'{action.id}: {result.detail}')
                return result, None
            except ActionError as error:
                last_error = str(error)
                self.log('ERROR', f'{action.id}: {error}')
                if attempt < attempts:
                    self.clock.sleep(self.config.retry.delay_seconds)
        return None, f'{action.id}: {last_error}'

    def execute_caen(self, action):
        # Discover the slot once, then give EACH channel its own deadline and
        # retry budget. A blocking channel must not prevent other channels.
        inventory = replace(action, id=f'{action.id}.inventory', kind='caen_inventory')
        result, error = self.execute(inventory)
        if error:
            return [error]
        if not result.channels:
            return [f'{action.id}: CAEN returned no channels; shutdown not attempted']
        errors = []
        for channel in result.channels:
            command = Action(f'{action.id}.{channel}', f'{action.name} channel {channel}',
                             'caen', {**action.parameters, 'channel': channel})
            _, error = self.execute(command)
            if error:
                errors.append(error)
        return errors

    def notify(self, message):
        try:
            self.notifier.send(message)
        except Exception as error:
            self.log('WARNING', f'Notification could not be queued ({type(error).__name__}); continuing')

    def wait(self):
        deadline = self.clock.monotonic() + self.config.wait_seconds
        self.log('INFO', f'WAIT: {self.config.wait_seconds / 60:g} minutes; '
                 'cancel/skip/extend accepted only during this wait')
        next_progress = self.clock.monotonic()
        while True:
            # Cancel has priority, including at the deadline boundary.
            if self.triggers.consume('cancel'):
                self.log('WARNING', 'CANCELED by operator; final power-off will not run')
                return False
            if self.triggers.consume('skip'):
                self.log('WARNING', f'Wait skipped; final steps in {self.config.skip_delay_seconds:g}s')
                # This is still part of the wait: allow cancellation during the grace period.
                deadline = self.clock.monotonic() + self.config.skip_delay_seconds
                while self.clock.monotonic() < deadline:
                    if self.triggers.consume('cancel'):
                        self.log('WARNING', 'CANCELED during skip grace period')
                        return False
                    self.clock.sleep(min(1, deadline - self.clock.monotonic()))
                if self.triggers.consume('cancel'):
                    self.log('WARNING', 'CANCELED at end of skip grace period')
                    return False
                return True
            if self.triggers.consume('extend'):
                deadline = self.clock.monotonic() + self.config.wait_seconds
                self.log('WARNING', f'Wait reset to {self.config.wait_seconds / 60:g} minutes')
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                self.log('INFO', 'Wait finished; proceeding to final power-off')
                return True
            if self.clock.monotonic() >= next_progress:
                self.log('INFO', f'WAIT: {remaining:.0f}s remaining')
                next_progress = self.clock.monotonic() + 60
            self.clock.sleep(min(self.config.poll_seconds, remaining))

    def run_sequence(self):
        self.log('WARNING', 'ALERT 0 -> 1: sequence latched; recovery does not cancel it')
        self.notify('LH2 alert 0 -> 1: shutdown sequence started.')
        errors = []
        canceled = False
        for action in self.config.actions:
            if action.kind == 'wait':
                if not self.wait():
                    canceled = True
                    break
                continue
            if action.kind == 'caen':
                errors.extend(self.execute_caen(action))
                continue
            _, error = self.execute(action)
            if error:
                errors.append(error)
        outcome = Outcome(canceled, tuple(errors))
        if canceled:
            summary = 'CANCELED by operator; monitor exiting. Final shutdown steps not executed.'
        elif errors:
            summary = 'Shutdown attempts completed WITH ERRORS; monitor exiting.'
        else:
            summary = ('All shutdown commands completed without reported errors; monitor exiting. '
                       'This is not a physical verification that all outputs are off.')
        if errors:
            summary += ' Errors: ' + '; '.join(errors)
        self.log('WARNING' if canceled else ('ERROR' if errors else 'INFO'), summary)
        self.notify(summary)
        return outcome

    def monitor(self, read_status, startup_choice):
        """Run one latched sequence, then exit. Keep the last VALID input state."""
        last = None
        last_error = None
        while True:
            status = read_status()
            if status.error or status.value not in {'0', '1'}:
                error = status.error or f'Invalid alert value: {status.value!r}'
                if error != last_error:
                    self.log('WARNING', f'Input unavailable: {error}; waiting for valid input')
                last_error = error
            else:
                if last_error:
                    self.log('INFO', 'Valid input restored')
                    last_error = None
                if last is None:
                    self.log('INFO', f'Initial Alert_H2leak = {status.value}')
                    if status.value == '1':
                        self.log('WARNING', 'Alert is ALREADY 1. No shutdown is triggered at startup.')
                        if startup_choice() == 'exit':
                            self.log('WARNING', 'Operator chose to exit without equipment commands')
                            return 0
                        self.log('WARNING', 'Waiting for 0, then a new 0 -> 1 transition')
                elif last == '0' and status.value == '1':
                    return self.run_sequence().exit_code
                last = status.value
            self.clock.sleep(self.config.poll_seconds)
