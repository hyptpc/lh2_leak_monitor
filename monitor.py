#!/usr/bin/env python3
"""LH2 monitor CLI. Normal startup is live; use --simulate for offline testing."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from lh2_monitor.config import format_plan, load_config
from lh2_monitor.engine import Engine


ROOT = Path(__file__).resolve().parent


def run_simulation(config, scenario):
    # Never construct live adapters, read .env, watch real input/trigger files,
    # start workers, or load the CAEN library in this mode.
    from lh2_monitor.simulation import (
        VirtualClock, VirtualExecutor, VirtualNotifier, VirtualTriggers, status_reader,
    )

    clock = VirtualClock()

    def log(level, message):
        print(f'[SIM {clock.monotonic():8.1f}s] {level}: {message}', flush=True)

    log('WARNING', 'VIRTUAL SIMULATION: no equipment / SSH / Discord / live status or trigger files')
    engine = Engine(config,
                    VirtualExecutor(clock, scenario.get('responses'),
                                    scenario.get('action_duration_seconds', 0),
                                    scenario.get('caen_channels', 2)),
                    VirtualNotifier(log), clock,
                    VirtualTriggers(clock, scenario.get('triggers', [])), log)
    result = engine.monitor(status_reader(scenario.get('status', ['0', '1'])),
                            lambda: scenario.get('startup_choice', 'wait'))
    log('INFO', f'Monitor exited with code {result}')
    return result


def run_live(config, startup_policy):
    from lh2_monitor.adapters import (
        DiscordNotifier, FileTriggers, MonitorLock, ProcessExecutor,
        make_logger, read_status, startup_choice,
    )
    from lh2_monitor.credentials import caen_credentials, load_environment

    log = make_logger(config.log_file)
    notifier = None
    try:
        with MonitorLock(config.lock_file):
            log('WARNING', 'LIVE MODE: equipment commands and configured notifications are enabled')
            log('INFO', '\n' + format_plan(config))
            triggers = FileTriggers(config.triggers, log)
            # Remove stale requests before monitoring, protected by the process lock.
            triggers.clear()
            if not load_environment():
                log('WARNING', 'python-dotenv unavailable; using environment variables only')
            if any(action.kind == 'caen' for action in config.actions):
                # Fail before ANY equipment command if required credentials are absent.
                caen_credentials()
            notifier = DiscordNotifier(os.environ.get('DISCORD_WEBHOOK_URL'),
                                       config.retry.attempt_timeout_seconds, log)
            engine = Engine(config, ProcessExecutor(log), notifier, time, triggers, log)
            choose = startup_choice if startup_policy == 'ask' else lambda: startup_policy
            result = engine.monitor(lambda: read_status(config.status_file), choose)
            triggers.clear()
            return result
    except KeyboardInterrupt:
        log('WARNING', 'Monitor interrupted; equipment state may be partially changed')
        return 130
    except Exception as error:
        log('ERROR', f'Monitor stopped: {type(error).__name__}: {error}')
        return 1
    finally:
        if notifier is not None:
            notifier.finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--simulate', action='store_true', help='Use entirely virtual equipment and time')
    modes.add_argument('--live', action='store_true', help='Explicit live mode (also the default)')
    modes.add_argument('--show-plan', action='store_true', help='Print order and addresses, then exit')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.toml')
    parser.add_argument('--scenario', type=Path, help='Simulation-only JSON scenario')
    parser.add_argument('--startup-alert', choices=['ask', 'wait', 'exit'], default='ask',
                        help='Already-1 policy; ask defaults to wait without a TTY')
    args = parser.parse_args(argv)
    if args.scenario and not args.simulate:
        parser.error('--scenario requires --simulate')
    try:
        config = load_config(args.config)
        if args.show_plan:
            print(format_plan(config))
            return 0
        if not args.simulate:
            return run_live(config, args.startup_alert)
        scenario = json.loads(args.scenario.read_text()) if args.scenario else {}
        return run_simulation(config, scenario)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Configuration/scenario error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
