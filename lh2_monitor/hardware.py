"""Live equipment I/O. Called only inside a bounded worker process.

No connections or vendor-library imports happen at module import time.
"""

from contextlib import ExitStack
import http.client
import json
import socket
import subprocess
import time

from .config import RetryPolicy


def hv_off(parameters, timeout):
    connection = http.client.HTTPConnection(parameters['host'], parameters['port'], timeout=timeout)
    try:
        connection.request('POST', '/serial/command',
                           body=json.dumps({'port_id': parameters['target_port'],
                                            'command_type': 'TURN_OFF'}),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError(f'HV controller returned HTTP {response.status}')
        return 'HV TURN_OFF request accepted (physical output not verified)'
    finally:
        connection.close()


def kikusui_off(parameters, timeout):
    with socket.create_connection((parameters['host'], parameters['port']), timeout) as sock:
        sock.sendall(b'OUTP OFF\n')
        # Query the output state; sending OUTP OFF alone is not confirmation.
        sock.sendall(b'OUTP?\n')
        response = bytearray()
        while b'\n' not in response:
            block = sock.recv(256)
            if not block:
                raise RuntimeError('Kikusui connection closed before output-state response')
            response.extend(block)
            if len(response) > 1024:
                raise RuntimeError('Invalid Kikusui output-state response')
        if response.decode('ascii').strip() not in {'0', 'OFF'}:
            raise RuntimeError(f'Kikusui did not confirm OFF: {response!r}')
    return 'Kikusui reports output OFF'


def caen_operation(parameters, timeout, *, inventory=False):
    # The C library can block. The parent process enforces the timeout.
    from caen_libs import caenhvwrapper as hv

    with hv.Device.open(hv.SystemType.SY1527, hv.LinkType.TCPIP,
                        parameters['host'], 'admin', 'admin') as device:
        board = next((board for board in device.get_crate_map()
                      if board is not None and board.slot == parameters['slot']), None)
        if board is None or board.n_channel <= 0:
            raise RuntimeError(f"CAEN slot {parameters['slot']} is missing or has no channels")
        if inventory:
            return {'detail': f'CAEN slot {board.slot}: {board.n_channel} channels',
                    'channels': list(range(board.n_channel))}
        channel = parameters['channel']
        if channel not in range(board.n_channel):
            raise RuntimeError(f'CAEN channel {channel} is not present')
        device.set_ch_param(board.slot, [channel], 'Pw', 0)
    return 'CAEN OFF command accepted (physical output not verified)'


def usb_off(parameters, timeout):
    # All remote-command arguments are validated by load_config(). Use fixed
    # command structure, noninteractive authentication, and noninteractive sudo.
    command = [
        'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectionAttempts=1', '-o', f'ConnectTimeout={max(1, int(timeout))}',
        '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=2',
        f"{parameters['user']}@{parameters['host']}",
        f"sudo -n /usr/sbin/uhubctl -l {parameters['hub']} -p {parameters['target_port']} -a 0",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip() or 'uhubctl completed (physical USB power not verified)'


def _checksum(data):
    return f'{(-sum(data)) & 0xff:02X}'.encode('ascii')


class MassFlowValve:
    """Minimal CPL protocol used by the existing MQV0002 scripts.

    Verify a read-only valve-status reply, and keep that socket for the close
    command. Even a valid reply cannot guarantee that later operations succeed.
    """

    def __init__(self, parameters, connect=socket.create_connection, clock=time):
        self.parameters = parameters
        self.connect = connect
        self.clock = clock
        self.sock = None

    def close_connection(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def preflight(self, timeout):
        deadline = self.clock.monotonic() + timeout
        self.close_connection()
        try:
            self.sock = self.connect((self.parameters['host'], self.parameters['port']), timeout)
            data = self._exchange(b'RS,1204W,1', deadline)
            # This is the valve-status register already read as part of the
            # existing MQV0002 info command (RS,1201W,8).
            if len(data) != 1:
                raise RuntimeError('Mass-flow access check did not return one status value')
            int(data[0])
        except Exception:
            self.close_connection()
            raise

    def close_valve(self, timeout):
        deadline = self.clock.monotonic() + timeout
        try:
            if self.sock is None:
                self.preflight(timeout)
            # Existing __write() expects return code 00 and an empty data field.
            if self._exchange(b'WS,1204W,0', deadline) != ['']:
                raise RuntimeError('Unexpected data in mass-flow close acknowledgment')
        except Exception:
            # A late/partial response must not be reused as the next attempt's ACK.
            self.close_connection()
            raise

    def _exchange(self, command, deadline):
        header = b'\x02' + f"{self.parameters['address']:02d}".encode('ascii') + b'00X'
        packet = header + command + b'\x03'
        self._set_remaining(deadline)
        self.sock.sendall(packet + _checksum(packet) + b'\r\n')
        response = bytearray()
        while not response.endswith(b'\r\n'):
            self._set_remaining(deadline)
            block = self.sock.recv(1)
            if not block:
                raise RuntimeError('Mass-flow connection closed before acknowledgment')
            response.extend(block)
            if len(response) > 4096:
                raise RuntimeError('Mass-flow response too long')
        response = bytes(response)
        if (not response.startswith(header) or len(response) < 13
                or response[-5:-4] != b'\x03'):
            raise RuntimeError(f'Invalid mass-flow response framing: {response!r}')
        if response[-4:-2] != _checksum(response[:-4]):
            raise RuntimeError('Mass-flow acknowledgment checksum mismatch')
        body = response[6:-5].decode('ascii')
        if not body.startswith('00,'):
            raise RuntimeError(f'Mass-flow command rejected: {body!r}')
        return body[3:].split(',')

    def _set_remaining(self, deadline):
        remaining = deadline - self.clock.monotonic()
        if remaining <= 0:
            raise TimeoutError('Mass-flow communication deadline exceeded')
        self.sock.settimeout(remaining)


def close_massflow_pair(parameters, *, valve_factory=MassFlowValve, clock=time, report=print):
    policy = RetryPolicy(**parameters['retry'])

    def attempt(label, operation):
        for index in range(1, policy.max_attempts + 1):
            try:
                operation(policy.attempt_timeout_seconds)
                report(f'{label}: succeeded on attempt {index}/{policy.max_attempts}', flush=True)
                return None
            except Exception as error:
                detail = f'{label}: attempt {index}/{policy.max_attempts}: {error}'
                report(detail, flush=True)
                if index < policy.max_attempts:
                    clock.sleep(policy.delay_seconds)
        return detail

    # Both read-only status checks must succeed before ANY valve-close command.
    with ExitStack() as stack:
        valves = {}
        access_errors = []
        for side in ('outlet', 'inlet'):
            valve = valve_factory(parameters[side])
            stack.callback(valve.close_connection)
            valves[side] = valve
            error = attempt(f'{side} access check', valve.preflight)
            if error:
                access_errors.append(error)
        if access_errors:
            raise RuntimeError('BOTH valve-close commands withheld: ' + '; '.join(access_errors))

        close_errors = []
        for side in ('outlet', 'inlet'):
            error = attempt(f'{side} close', valves[side].close_valve)
            if error:
                close_errors.append(error)
        if close_errors:
            raise RuntimeError('CRITICAL: mass-flow pair NOT confirmed complete; '
                               'one valve may have closed and the other may not. '
                               + '; '.join(close_errors))
    return 'Both mass-flow close commands acknowledged (physical flow not verified)'


def perform(kind, parameters, timeout):
    operations = {'hv': hv_off, 'kikusui': kikusui_off, 'usb': usb_off}
    if kind == 'massflow_pair':
        return close_massflow_pair(parameters)
    if kind in {'caen', 'caen_inventory'}:
        return caen_operation(parameters, timeout, inventory=kind == 'caen_inventory')
    return operations[kind](parameters, timeout)
