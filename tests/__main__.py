"""Reject all real network connections, subprocesses, and production file I/O."""

import os
from pathlib import Path
import sys
import unittest


FORBIDDEN_EVENTS = {
    'socket.__new__', 'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
    'socket.bind', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork',
    'os.exec', 'os.kill', 'os.killpg', 'urllib.Request', 'ctypes.dlopen',
}
PRODUCTION_FILES = {'/tmp/skip.now', '/tmp/cancel.now', '/tmp/extend.now'}


def guard(event, args):
    if event in FORBIDDEN_EVENTS:
        raise RuntimeError(f'OFFLINE TEST GUARD: forbidden operation {event}')
    if event == 'import' and (args[0] == 'caen_libs' or args[0].startswith('caen_libs.')):
        raise RuntimeError('OFFLINE TEST GUARD: real CAEN library import forbidden')
    if event in {'open', 'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir'} and args:
        for value in args[:2]:
            if isinstance(value, (str, bytes, os.PathLike)):
                path = os.fsdecode(value)
                if (path in PRODUCTION_FILES or path.startswith('/home/sks/')
                        or path.startswith('/misc/share/')):
                    raise RuntimeError(f'OFFLINE TEST GUARD: production path {path}')


sys.addaudithook(guard)
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
