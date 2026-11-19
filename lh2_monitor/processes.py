"""Bounded cleanup for the monitor's isolated worker process groups."""

from dataclasses import dataclass
import os
import signal
import subprocess
import threading


CLEANUP_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class CleanupResult:
    output: str = ""
    errors: str = ""
    reaped: bool = True
    note: str = ""


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _reap_later(process):
    # A task stuck inside the kernel cannot be forcibly reaped before it exits.
    # This daemon is only a reaper: it never sends commands or blocks monitor exit.
    def reap():
        try:
            process.wait()
        except (OSError, ValueError):
            pass
    try:
        threading.Thread(target=reap, name=f"lh2-reap-{process.pid}", daemon=True).start()
    except RuntimeError:
        # Popen still performs its normal nonblocking destructor/poll bookkeeping.
        pass


def terminate_group(process, *, timeout=CLEANUP_TIMEOUT_SECONDS):
    """Send SIGKILL, wait at most timeout seconds, then return unknown state.

    The caller must not interpret cleanup completion as equipment OFF.
    A deferred daemon reaper collects a worker which the kernel releases later.
    """
    signal_note = ""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        signal_note = f"process-group SIGKILL failed ({type(error).__name__}); "
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
    try:
        output, errors = process.communicate(timeout=timeout)
        return CleanupResult(_text(output), _text(errors), True, signal_note)
    except subprocess.TimeoutExpired as error:
        output, errors = _text(error.output), _text(error.stderr)
        note = (signal_note + f"worker {process.pid} not reaped within {timeout:g}s cleanup limit; "
                "equipment result UNKNOWN; continuing without waiting for it")
    except (OSError, ValueError) as error:
        output, errors = "", ""
        note = (signal_note + f"worker {process.pid} cleanup failed ({type(error).__name__}); "
                "equipment result UNKNOWN; continuing without waiting for it")
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    _reap_later(process)
    return CleanupResult(output, errors, False, note)
