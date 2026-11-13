from pathlib import Path
import unittest

from lh2_monitor.config import load_config
from lh2_monitor.engine import Engine
from lh2_monitor.simulation import (
    VirtualClock, VirtualExecutor, VirtualNotifier, VirtualTriggers, status_reader,
)


ROOT = Path(__file__).resolve().parent.parent


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / 'config.toml')
        self.clock = VirtualClock()
        self.events = []
        self.log = lambda level, message: self.events.append((level, message))
        self.executor = VirtualExecutor(self.clock)
        self.notifier = VirtualNotifier(self.log)
        self.triggers = VirtualTriggers(self.clock)

    def engine(self):
        return Engine(self.config, self.executor, self.notifier,
                      self.clock, self.triggers, self.log)

    def ids(self):
        return [key for _, key in self.executor.calls]

    def test_inventory_and_final_order_match_agreed_deployment(self):
        actions = self.config.actions
        self.assertEqual(actions[-1].id, 'kikusui42')
        self.assertEqual(self.config.wait_seconds, 3600)
        self.assertEqual([a.id for a in actions[:12]],
                         [f'hv{host}.{port}' for host in (12, 13, 91) for port in range(4)])
        self.assertEqual([a.id for a in actions[12:16]],
                         ['massflow_pair', 'kikusui45', 'chamber', 'wait'])
        self.assertEqual([a.id for a in actions[16:-1]],
                         [f'usb{host}.{port}' for host in (12, 13) for port in range(1, 5)])
        self.assertFalse(any('T0' in a.name for a in actions))
        self.assertFalse((ROOT / 'shutdown_caenhv1_T0.py').exists())

    def test_normal_transition_runs_once_and_exits(self):
        self.assertEqual(self.engine().monitor(status_reader(['0', '1']), lambda: 'wait'), 0)
        self.assertEqual(self.ids()[-1], 'kikusui42')
        self.assertEqual(self.clock.now, 3601)
        self.assertEqual(len(self.notifier.messages), 2)

    def test_startup_one_waits_for_zero_then_one(self):
        choices = []
        self.engine().monitor(status_reader(['1', '1', '0', '1']),
                              lambda: choices.append(True) or 'wait')
        self.assertEqual(len(choices), 1)
        self.assertEqual(self.executor.calls[0][0], 3)

    def test_startup_one_can_exit_without_commands(self):
        code = self.engine().monitor(status_reader(['1']), lambda: 'exit')
        self.assertEqual(code, 0)
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.notifier.messages, [])

    def test_first_valid_input_after_errors_uses_startup_policy(self):
        code = self.engine().monitor(status_reader([None, '', '1']), lambda: 'exit')
        self.assertEqual(code, 0)
        self.assertEqual(self.executor.calls, [])

    def test_bad_input_preserves_last_valid_zero(self):
        self.assertEqual(self.engine().monitor(status_reader(['0', None, '', 'bad', '1']),
                                               lambda: 'wait'), 0)
        self.assertEqual(self.executor.calls[0][0], 4)

    def test_unchanged_input_error_is_not_logged_every_second(self):
        self.engine().monitor(status_reader(['0', None, None, None, '1']), lambda: 'wait')
        warnings = [message for _, message in self.events if 'Input unavailable' in message]
        self.assertEqual(len(warnings), 1)

    def test_input_is_not_polled_again_after_latching(self):
        values = iter(['0', '1', '0'])
        self.engine().monitor(status_reader(values), lambda: 'wait')
        self.assertEqual(next(values), '0')

    def test_failure_retries_three_times_and_continues_to_other_targets(self):
        self.executor = VirtualExecutor(self.clock, {'hv12.0': ['error'] * 3})
        outcome = self.engine().run_sequence()
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(self.ids()[:4], ['hv12.0', 'hv12.0', 'hv12.0', 'hv12.1'])
        self.assertEqual(self.executor.calls[1][0], 2)
        self.assertEqual(self.executor.calls[2][0], 4)
        self.assertEqual(self.ids()[-1], 'kikusui42')
        self.assertIn('WITH ERRORS', self.notifier.messages[-1])

    def test_single_blocking_caen_channel_does_not_skip_next_channel(self):
        self.executor = VirtualExecutor(self.clock, {'chamber.0': ['timeout'] * 3})
        outcome = self.engine().run_sequence()
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(self.ids().count('chamber.0'), 3)
        self.assertIn('chamber.1', self.ids())
        self.assertEqual(self.ids()[-1], 'kikusui42')

    def test_caen_inventory_failure_does_not_claim_channels_were_stopped(self):
        self.executor = VirtualExecutor(self.clock, {'chamber.inventory': ['error'] * 3})
        outcome = self.engine().run_sequence()
        self.assertEqual(outcome.exit_code, 1)
        self.assertNotIn('chamber.0', self.ids())
        self.assertEqual(self.ids()[-1], 'kikusui42')

    def test_massflow_pair_is_not_retried_wholesale(self):
        self.executor = VirtualExecutor(self.clock, {'massflow_pair': ['partial closure']})
        outcome = self.engine().run_sequence()
        self.assertEqual(self.ids().count('massflow_pair'), 1)
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(self.ids()[-1], 'kikusui42')

    def test_full_wait_starts_after_last_pre_wait_operation_finishes(self):
        self.executor = VirtualExecutor(self.clock, duration_seconds=7)
        self.engine().run_sequence()
        calls = {key: at for at, key in self.executor.calls}
        self.assertEqual(calls['usb12.1'] - (calls['chamber.1'] + 7), 3600)

    def test_cancel_during_wait_exits_without_any_post_wait_commands(self):
        self.triggers.events = [{'at': 100, 'name': 'cancel'}]
        outcome = self.engine().run_sequence()
        self.assertTrue(outcome.canceled)
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(any(key.startswith('usb') for key in self.ids()))
        self.assertNotIn('kikusui42', self.ids())
        self.assertEqual(self.clock.now, 100)

    def test_cancel_after_errors_does_not_hide_preceding_failures(self):
        self.executor = VirtualExecutor(self.clock, {'hv12.0': ['error'] * 3})
        self.triggers.events = [{'at': 20, 'name': 'cancel'}]
        outcome = self.engine().run_sequence()
        self.assertTrue(outcome.canceled)
        self.assertTrue(outcome.errors)
        self.assertEqual(outcome.exit_code, 1)
        self.assertIn('Errors:', self.notifier.messages[-1])

    def test_skip_advances_to_final_steps_after_five_seconds(self):
        self.triggers.events = [{'at': 10, 'name': 'skip'}]
        self.engine().run_sequence()
        self.assertEqual({key: at for at, key in self.executor.calls}['usb12.1'], 15)
        self.assertEqual(self.ids()[-1], 'kikusui42')

    def test_cancel_works_during_skip_grace_period(self):
        self.triggers.events = [{'at': 10, 'name': 'skip'}, {'at': 12, 'name': 'cancel'}]
        self.assertTrue(self.engine().run_sequence().canceled)
        self.assertNotIn('kikusui42', self.ids())

    def test_extend_resets_full_hour_from_operator_request(self):
        self.triggers.events = [{'at': 100, 'name': 'extend'}]
        self.engine().run_sequence()
        self.assertEqual({key: at for at, key in self.executor.calls}['usb12.1'], 3700)

    def test_cancel_has_priority_at_wait_deadline(self):
        self.triggers.events = [{'at': 3600, 'name': 'cancel'}]
        self.assertTrue(self.engine().run_sequence().canceled)
        self.assertNotIn('kikusui42', self.ids())

    def test_cancel_is_not_consumed_after_wait(self):
        self.executor = VirtualExecutor(self.clock, duration_seconds=1)
        self.triggers.events = [{'at': 3620, 'name': 'cancel'}]
        # Pre-wait: 12 HV + pair + Kikusui + inventory + two CAEN channels = 17s.
        outcome = self.engine().run_sequence()
        self.assertFalse(outcome.canceled)
        self.assertEqual(self.ids()[-1], 'kikusui42')
        self.assertEqual(len(self.triggers.events), 1)


if __name__ == '__main__':
    unittest.main()
