"""Install reviewed systemd definitions, without starting or enabling monitors."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile


UNITS = {'lh2': 'lh2_monitor.service', 'blc': 'blc_monitor.service',
         'ftof': 'ftof_monitor.service', 'trip': 'trip_monitor.service'}
ALIASES = {'lh2_leak_monitor': 'lh2', 'BLC_hv_monitoring': 'blc',
           'CVC_hv_monitoring': 'ftof', 'trip_monitor': 'trip'}
TEMPLATES = Path(__file__).resolve().parent / 'units'


def select_targets(target):
    if target == 'all':
        return list(UNITS)
    target = ALIASES.get(target, target)
    if target not in UNITS:
        raise ValueError('Select lh2, blc, ftof, trip, or all')
    return [target]


class SystemTools:
    def verify(self, paths):
        subprocess.run(['/usr/bin/systemd-analyze', 'verify', *map(str, paths)], check=True)

    def state(self, unit):
        return subprocess.check_output(['/usr/bin/systemctl', 'show', unit,
                                        '--property=ActiveState', '--value'], text=True).strip()

    def reload(self):
        subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)


def template_paths(targets, source=TEMPLATES):
    # The fixed allowlist prevents arbitrary unit names and paths.
    paths = []
    for target in targets:
        path = source / UNITS[target]
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f'Missing or symlinked unit template: {path.name}')
        paths.append(path)
    return paths


def atomic_write(path, content, mode=0o644):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install(targets, *, source=TEMPLATES, destination=Path('/etc/systemd/system'),
            backup_root=Path('/var/backups/monitor-services'), tools=None):
    if os.geteuid() != 0:
        raise PermissionError('Run with sudo: sudo /usr/bin/python3 daemon/configure.py TARGET')
    tools = tools or SystemTools()
    paths = template_paths(targets, source)
    tools.verify(paths)
    states = {target: tools.state(UNITS[target]) for target in targets}
    busy = [target for target, state in states.items() if state not in {'inactive', 'failed'}]
    if busy:
        commands = '; '.join('./daemon/stop.sh ' + target for target in busy)
        raise RuntimeError('Stop the existing services before installing: ' + commands)

    previous = {}
    for path in paths:
        current = destination / path.name
        if current.is_symlink() or (current.exists() and not current.is_file()):
            raise RuntimeError(f'Non-regular existing unit; not replacing: {current}')
        previous[path.name] = ((current.read_bytes(), current.stat().st_mode & 0o777)
                               if current.exists() else None)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    backup = Path(tempfile.mkdtemp(prefix=stamp, dir=backup_root))
    for name, saved in previous.items():
        if saved is not None:
            atomic_write(backup / name, saved[0], 0o600)

    try:
        for path in paths:
            atomic_write(destination / path.name, path.read_bytes())
        # Reload definitions only. Never start, stop, enable, or restart a unit.
        tools.reload()
    except BaseException:
        for name, saved in previous.items():
            current = destination / name
            if saved is None:
                current.unlink(missing_ok=True)
            else:
                atomic_write(current, saved[0], saved[1])
        try:
            tools.reload()
        except Exception:
            pass
        raise
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', nargs='?', help='lh2, blc, ftof, trip, or all')
    parser.add_argument('--check', action='store_true', help='Validate templates only; no install or service operations')
    args = parser.parse_args(argv)
    inferred = Path(__file__).resolve().parent.parent.name
    try:
        targets = select_targets(args.target or inferred)
        if args.check:
            SystemTools().verify(template_paths(targets))
            print('Unit templates verified. Nothing installed or started.')
            return 0
        backup = install(targets)
        print('Updated definitions for: ' + ', '.join(targets))
        print(f'Previous unit files backed up in: {backup}')
        print('No monitors started or enabled. Run daemon/start.sh TARGET only when ready.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Configuration not applied: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
