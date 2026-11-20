#!/usr/bin/env python3
"""Reset only this LH2 monitor's launch/runtime state. Never operates equipment."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from lh2_monitor.config import load_config

ROOT = Path('/home/sks/monitor_tools/lh2_leak_monitor')
SESSION = 'lh2-monitor'


def find_lock_owner(path):
    info = path.stat()
    device = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
    for line in Path('/proc/locks').read_text().splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[1] != 'FLOCK' or fields[3] != 'WRITE':
            continue
        major, minor, inode = fields[5].split(':')
        if (int(major, 16), int(minor, 16), int(inode)) == device:
            return int(fields[4])
    return None


def interrupt_monitor(pid, root):
    # pidfd avoids signaling an unrelated process if a PID is recycled.
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        args = [os.fsdecode(arg) for arg in args if arg]
        allowed = {str(root / 'app/monitor.py'), str(root / 'monitor.py')}
        if not args or not Path(args[0]).name.startswith('python'):
            raise RuntimeError(f'Lock owner PID {pid} is not the LH2 Python monitor; left untouched')
        # Relative invocations are resolved against the actual process working directory.
        cwd = Path(f'/proc/{pid}/cwd').resolve()
        entry = next((arg for arg in args[1:] if not arg.startswith('-')), '')
        script = str(Path(entry) if Path(entry).is_absolute() else cwd / entry)
        if script not in allowed:
            raise RuntimeError(f'Lock owner PID {pid} is not this LH2 monitor; left untouched')
        print(f'Stopping LH2 monitor PID {pid} with SIGINT...', flush=True)
        signal.pidfd_send_signal(descriptor, signal.SIGINT)
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        os.close(descriptor)


@contextmanager
def exclusive_runtime(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep this inode: unlinking an active lock would permit simultaneous monitors.
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = find_lock_owner(path)
            if pid is None:
                raise RuntimeError('Lock is busy but its owner could not be identified; no files/session removed')
            interrupt_monitor(pid, root)
            deadline = time.monotonic() + 15
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Monitor has not released its lock after 15 seconds; no forced kill performed')
                    time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def reset(config, root=ROOT):
    state = subprocess.run(['systemctl', 'show', 'lh2_monitor.service', '-p', 'ActiveState', '--value'],
                           check=True, capture_output=True, text=True).stdout.strip()
    if state not in {'inactive', 'failed'}:
        print('Stopping LH2 systemd service (sudo may ask for your password)...', flush=True)
        subprocess.run(['sudo', 'systemctl', 'stop', 'lh2_monitor.service'], check=True)
    if state == 'failed':
        subprocess.run(['sudo', 'systemctl', 'reset-failed', 'lh2_monitor.service'], check=True)
    with exclusive_runtime(config.lock_file, root):
        exists = subprocess.run(['tmux', 'has-session', '-t', '=' + SESSION],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if exists.returncode == 0:
            subprocess.run(['tmux', 'kill-session', '-t', '=' + SESSION], check=True)
            print(f'Closed tmux session: {SESSION}', flush=True)
        for name, path in config.triggers.items():
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                # Only the explicitly configured request file; never follow links or recurse.
                subprocess.run(['sudo', 'rm', '-f', '--', str(path)], check=True)
            print(f'Cleared {name} request: {path}', flush=True)
    print('Reset complete. LH2 monitor stopped; lock free. Run ./run.sh when ready.', flush=True)


def main():
    try:
        reset(load_config(ROOT / 'config/config.toml'))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Reset incomplete: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
