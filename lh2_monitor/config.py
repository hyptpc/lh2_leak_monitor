"""Read and validate the equipment inventory and ordered shutdown plan."""

from dataclasses import asdict, dataclass
from ipaddress import IPv4Address
from pathlib import Path
import math
import re
import tomllib


@dataclass(frozen=True)
class RetryPolicy:
    attempt_timeout_seconds: float = 10
    delay_seconds: float = 2
    max_attempts: int = 3

    @property
    def total_seconds(self):
        return (self.attempt_timeout_seconds * self.max_attempts
                + self.delay_seconds * (self.max_attempts - 1))


@dataclass(frozen=True)
class Action:
    id: str
    name: str
    kind: str
    parameters: dict


@dataclass(frozen=True)
class Config:
    status_file: Path
    poll_seconds: float
    wait_seconds: float
    skip_delay_seconds: float
    log_file: Path
    lock_file: Path
    retry: RetryPolicy
    triggers: dict
    actions: tuple


def _positive_number(value, name, *, allow_zero=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not (value >= 0 if allow_zero else value > 0)):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'}")


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")


def load_config(path):
    path = Path(path).resolve()
    with path.open('rb') as stream:
        raw = tomllib.load(stream)
    monitor = raw['monitor']
    retry = RetryPolicy(**raw['retry'])
    _positive_number(retry.attempt_timeout_seconds, 'attempt timeout')
    _positive_number(retry.delay_seconds, 'retry delay', allow_zero=True)
    _integer(retry.max_attempts, 'max_attempts', 1, 100)
    for key in ('poll_seconds', 'wait_seconds'):
        _positive_number(monitor[key], key)
    _positive_number(monitor['skip_delay_seconds'], 'skip delay', allow_zero=True)

    targets = raw['targets']
    for key, target in targets.items():
        kind = target['kind']
        if kind not in {'hv', 'massflow', 'kikusui', 'caen', 'usb'}:
            raise ValueError(f"Unknown target kind: {kind}")
        IPv4Address(target['host'])
        if kind in {'hv', 'massflow', 'kikusui'}:
            _integer(target['port'], f'{key}.port', 1, 65535)
        if kind in {'hv', 'usb'}:
            ports = target['ports']
            if not ports or len(ports) != len(set(ports)):
                raise ValueError(f'{key}.ports must be nonempty and unique')
            for port in ports:
                _integer(port, f'{key}.ports', 0 if kind == 'hv' else 1, 255)
        if kind == 'massflow':
            _integer(target['address'], f'{key}.address', 0, 99)
        if kind == 'caen':
            _integer(target['slot'], f'{key}.slot', 0, 255)
        if kind == 'usb':
            if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*', target['user']):
                raise ValueError(f'Invalid SSH user for {key}')
            if not re.fullmatch(r'[0-9]+(?:[.-][0-9]+)*', target['hub']):
                raise ValueError(f'Invalid hub for {key}')

    actions = []
    for step in raw['sequence']:
        kind = step.get('kind')
        if kind == 'wait':
            actions.append(Action('wait', 'Wait before final power-off', kind, {}))
        elif kind == 'massflow_pair':
            outlet, inlet = step['outlet'], step['inlet']
            if outlet == inlet or any(targets[key]['kind'] != 'massflow'
                                       for key in (outlet, inlet)):
                raise ValueError('Mass-flow pair must reference two distinct mass-flow targets')
            endpoints = [(targets[key]['host'], targets[key]['port'], targets[key]['address'])
                         for key in (outlet, inlet)]
            if endpoints[0] == endpoints[1]:
                raise ValueError('Mass-flow pair must reference two distinct devices')
            actions.append(Action('massflow_pair', 'Outlet then inlet mass-flow valves', kind, {
                'outlet': targets[outlet], 'inlet': targets[inlet], 'retry': asdict(retry),
            }))
        elif kind is None:
            key = step['target']
            target = targets[key]
            kind = target['kind']
            if kind == 'massflow':
                raise ValueError('Mass-flow valves must be scheduled as a pair')
            if kind in {'hv', 'usb'}:
                for port in target['ports']:
                    actions.append(Action(f'{key}.{port}', f"{target['name']} port {port}",
                                          kind, {**target, 'target_port': port}))
            else:
                actions.append(Action(key, target['name'], kind, dict(target)))
        else:
            raise ValueError(f'Unknown sequence kind: {kind}')
    ids = [action.id for action in actions]
    if ids.count('wait') != 1 or len(ids) != len(set(ids)):
        raise ValueError('Plan must have exactly one wait and no repeated actions')
    if len(raw['triggers']) != 3 or set(raw['triggers']) != {'skip', 'cancel', 'extend'}:
        raise ValueError('Define skip, cancel, and extend trigger paths')
    trigger_paths = {key: Path(value) for key, value in raw['triggers'].items()}
    if len(set(trigger_paths.values())) != 3:
        raise ValueError('Trigger paths must be distinct')

    def relative(value):
        return (path.parent / value).resolve()

    return Config(relative(monitor['status_file']), monitor['poll_seconds'],
                  monitor['wait_seconds'], monitor['skip_delay_seconds'],
                  relative(monitor['log_file']), relative(monitor['lock_file']),
                  retry, trigger_paths, tuple(actions))


def format_plan(config):
    lines = ['ORDER  ACTION                      DESTINATION / OPERATION']
    for index, action in enumerate(config.actions, 1):
        p = action.parameters
        if action.kind == 'wait':
            detail = f'{config.wait_seconds / 60:g} minutes after preceding attempts finish'
        elif action.kind == 'massflow_pair':
            detail = ' -> '.join(f"{p[side]['host']}:{p[side]['port']} (addr {p[side]['address']})"
                                 for side in ('outlet', 'inlet'))
            detail += '; check BOTH connections, then close outlet -> inlet'
        else:
            detail = p['host'] + (f":{p['port']}" if 'port' in p else '')
            if action.kind in {'hv', 'usb'}:
                detail += f" / port {p['target_port']} OFF"
            if action.kind == 'usb':
                detail += f" (hub {p['hub']})"
            if action.kind == 'caen':
                detail += f" / slot {p['slot']}, all channels OFF"
        lines.append(f'{index:>5}  {action.name:<28} {detail}')
    lines.append(f'Retry: {config.retry.max_attempts} attempts total, '
                 f'{config.retry.attempt_timeout_seconds:g}s per attempt, '
                 f'{config.retry.delay_seconds:g}s between attempts')
    return '\n'.join(lines)
