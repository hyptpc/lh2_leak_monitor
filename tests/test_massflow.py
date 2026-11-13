from pathlib import Path
import unittest

from lh2_monitor.config import load_config
from lh2_monitor.hardware import MassFlowValve, close_massflow_pair
from lh2_monitor.simulation import VirtualClock


class PairTests(unittest.TestCase):
    def setUp(self):
        config = load_config(Path(__file__).resolve().parent.parent / 'config.toml')
        self.parameters = next(a.parameters for a in config.actions if a.kind == 'massflow_pair')
        self.clock = VirtualClock()
        self.calls = []
        self.failures = {}

    def factory(self, parameters):
        side = 'outlet' if parameters['host'].endswith('.17') else 'inlet'
        calls, failures = self.calls, self.failures

        class Valve:
            def operate(self, operation, timeout):
                calls.append((side, operation, timeout))
                key = (side, operation)
                if failures.get(key, 0):
                    failures[key] -= 1
                    raise TimeoutError('simulated timeout')

            def preflight(self, timeout):
                self.operate('check', timeout)

            def close_valve(self, timeout):
                self.operate('close', timeout)

            def close_connection(self):
                calls.append((side, 'disconnect', None))
        return Valve()

    def close_pair(self):
        return close_massflow_pair(self.parameters, valve_factory=self.factory,
                                   clock=self.clock, report=lambda *args, **kwargs: None)

    def test_both_checks_precede_outlet_then_inlet_close(self):
        self.close_pair()
        self.assertEqual(self.calls[:4], [('outlet', 'check', 10), ('inlet', 'check', 10),
                                         ('outlet', 'close', 10), ('inlet', 'close', 10)])
        self.assertEqual(sum(op == 'disconnect' for _, op, _ in self.calls), 2)

    def test_failure_of_either_access_check_withholds_both_close_commands(self):
        for side in ('outlet', 'inlet'):
            with self.subTest(side=side):
                self.calls.clear()
                self.failures = {(side, 'check'): 3}
                with self.assertRaisesRegex(RuntimeError, 'BOTH valve-close commands withheld'):
                    self.close_pair()
                self.assertFalse(any(op == 'close' for _, op, _ in self.calls))
                self.assertEqual(sum(s == side and op == 'check' for s, op, _ in self.calls), 3)

    def test_transient_access_failure_recovers_before_either_close(self):
        self.failures = {('inlet', 'check'): 1}
        self.close_pair()
        self.assertEqual([call[:2] for call in self.calls[:5]],
                         [('outlet', 'check'), ('inlet', 'check'), ('inlet', 'check'),
                          ('outlet', 'close'), ('inlet', 'close')])
        self.assertEqual(self.clock.now, 2)

    def test_partial_closure_retries_failed_side_only_and_reports_critical_error(self):
        self.failures = {('inlet', 'close'): 3}
        with self.assertRaisesRegex(RuntimeError, 'CRITICAL: mass-flow pair NOT confirmed complete'):
            self.close_pair()
        self.assertEqual(sum(s == 'outlet' and op == 'close' for s, op, _ in self.calls), 1)
        self.assertEqual(sum(s == 'inlet' and op == 'close' for s, op, _ in self.calls), 3)
        self.assertEqual(self.clock.now, 4)

    def test_transient_close_failure_does_not_repeat_successful_outlet(self):
        self.failures = {('inlet', 'close'): 1}
        self.close_pair()
        self.assertEqual(sum(s == 'outlet' and op == 'close' for s, op, _ in self.calls), 1)
        self.assertEqual(sum(s == 'inlet' and op == 'close' for s, op, _ in self.calls), 2)


class MemorySocket:
    def __init__(self, response):
        self.response = iter(bytes([byte]) for byte in response)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        return next(self.response, b'')

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    # Fixed protocol vectors, including the independently calculated CPL sums.
    ACK = b'\x020100X00,\x0356\r\n'
    STATUS = b'\x020100X00,1\x0325\r\n'
    COMMAND = b'\x020100XWS,1204W,0\x0392\r\n'
    QUERY = b'\x020100XRS,1204W,1\x0396\r\n'

    def valve(self, response):
        sock = MemorySocket(response)
        valve = MassFlowValve({'host': '192.0.2.1', 'port': 4001, 'address': 1},
                              connect=lambda address, timeout: sock, clock=VirtualClock())
        return valve, sock

    def test_valid_acknowledgment_uses_preflight_socket(self):
        valve, sock = self.valve(self.STATUS + self.ACK)
        valve.preflight(10)
        self.assertEqual(sock.sent, [self.QUERY])
        valve.close_valve(10)
        self.assertEqual(sock.sent, [self.QUERY, self.COMMAND])

    def test_tcp_access_without_valid_device_status_does_not_allow_close(self):
        valve, sock = self.valve(self.ACK)
        with self.assertRaises(ValueError):
            valve.preflight(10)
        self.assertEqual(sock.sent, [self.QUERY])
        self.assertTrue(sock.closed)

    def test_eof_fails_without_spinning_forever(self):
        valve, sock = self.valve(b'')
        with self.assertRaisesRegex(RuntimeError, 'closed before acknowledgment'):
            valve.close_valve(10)
        self.assertTrue(sock.closed)
        self.assertIsNone(valve.sock)

    def test_bad_checksum_is_failure(self):
        valve, sock = self.valve(self.ACK.replace(b'56', b'00'))
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            valve.close_valve(10)
        self.assertTrue(sock.closed)

    def test_device_error_code_is_failure_even_with_valid_checksum(self):
        # 01, error response has checksum 55 instead of 56.
        valve, _ = self.valve(b'\x020100X01,\x0355\r\n')
        with self.assertRaisesRegex(RuntimeError, 'command rejected'):
            valve.close_valve(10)

    def test_trickling_response_is_bounded_by_total_communication_deadline(self):
        clock = VirtualClock()
        sock = MemorySocket(self.ACK)
        original_recv = sock.recv

        def slow_recv(size):
            clock.sleep(1)
            return original_recv(size)

        sock.recv = slow_recv
        valve = MassFlowValve({'host': '192.0.2.1', 'port': 4001, 'address': 1},
                              connect=lambda address, timeout: sock, clock=clock)
        with self.assertRaisesRegex(TimeoutError, 'deadline exceeded'):
            valve.close_valve(10)
        self.assertEqual(clock.now, 10)
        self.assertTrue(sock.closed)


if __name__ == '__main__':
    unittest.main()
