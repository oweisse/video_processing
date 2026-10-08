"""Child-process and temp-path tracking so Ctrl+C never leaves debris behind.

Every subprocess is started in its own process group. On interrupt we signal the
whole group, otherwise a killed parent leaves ffmpeg running and still writing
into the temporary directory we are about to delete.
"""
import atexit
import os
import shutil
import signal
import subprocess
import threading

_lock = threading.Lock()
_active = []
_paths = []


def popen(cmd, **kwargs):
    kwargs.setdefault("start_new_session", True)
    proc = subprocess.Popen(cmd, **kwargs)
    with _lock:
        _active.append(proc)
    return proc


def untrack(proc):
    with _lock:
        if proc in _active:
            _active.remove(proc)


def terminate(proc, timeout=3.0):
    if proc.poll() is not None:
        untrack(proc)
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = None

    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        pass

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
    untrack(proc)


def terminate_all():
    with _lock:
        procs = list(_active)
    for proc in procs:
        terminate(proc)


def register_path(path):
    """Remember a temp dir or partial output file to delete on any exit path."""
    with _lock:
        if path not in _paths:
            _paths.append(path)


def unregister_path(path):
    with _lock:
        if path in _paths:
            _paths.remove(path)


def cleanup_paths():
    with _lock:
        paths = list(_paths)
        _paths.clear()
    for path in paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def cleanup_all():
    terminate_all()
    cleanup_paths()


atexit.register(cleanup_all)


def install_signal_handlers():
    """Turn SIGINT/SIGTERM/SIGHUP into KeyboardInterrupt so `finally` blocks still run.

    SIGINT is installed explicitly rather than left to Python's default: a shell
    that starts us as a background job sets SIGINT to SIG_IGN, and Python
    inherits that, which would make Ctrl+C silently do nothing.
    """
    def _handler(signum, frame):
        raise KeyboardInterrupt()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass
