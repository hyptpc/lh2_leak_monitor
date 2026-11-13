#!/usr/bin/env python3
"""Compatibility HV tool. Simulates by default; --live is required for hardware."""

import argparse
from pathlib import Path
import sys
import time

from lh2_monitor.config import Action, load_config
from lh2_monitor.engine import Engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip_last', type=int, required=True, choices=range(1, 255), metavar='1..254')
    parser.add_argument('--port', type=int, required=True, choices=range(4))
    parser.add_argument('--live', action='store_true', help='Send REAL HV shutdown commands')
    args = parser.parse_args(argv)
    action = Action(f'hv{args.ip_last}.{args.port}', 'HV controller', 'hv', {
        'host': f'192.168.20.{args.ip_last}', 'port': 8000, 'target_port': args.port,
    })
    if not args.live:
        print(f'SIMULATED ONLY: TURN_OFF {action.parameters}; no command sent')
        return 0

    from lh2_monitor.adapters import ProcessExecutor

    config = load_config(Path(__file__).resolve().parent / 'config.toml')

    def log(level, message):
        print(f'{level}: {message}', flush=True)

    engine = Engine(config, ProcessExecutor(log), None, time, None, log)
    _, error = engine.execute(action)
    return 1 if error else 0


if __name__ == '__main__':
    sys.exit(main())
