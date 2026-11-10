"""Machine-readable progress for the GUI: --progress json.

In json mode stdout carries only JSON lines, one event per line, and every other
write (stray prints, library chatter, child processes) goes to stderr. The
console helpers and the progress bar turn into events instead of text. Schema
(docs/gui-design.md §4.1); every event has "v" and "event":

  start     tool, argv, pid, ts
  step      index, total, title
  progress  step, prefix, processed_sec, total_sec, speed, eta_sec
  message   level (info|success|warning|error), text
  candidate window, index, total, candidate          (clean-audio-tune)
  window    window, picked, runner_up, plan          (clean-audio-tune)
  result    output, sidecar, replaced, results       (clean-audio)
  done      returncode, elapsed_sec                  always last on success
  error     text, returncode                         always last on failure
"""
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone

VERSION = 1
PROGRESS_INTERVAL = 0.25    # at most 4 progress events per second per reporter

emitter = None
# What muted() silences: the inner passes of a bigger unit of work.
MUTABLE = {"step", "progress", "message"}


class JsonEmitter:
    def __init__(self, stream):
        self.stream = stream
        self.step = None
        self.last_error = None
        self.muted = 0

    def emit(self, event, **fields):
        if self.muted and event in MUTABLE:
            return
        record = {"v": VERSION, "event": event, **fields}
        self.stream.write(json.dumps(record, default=_default, ensure_ascii=False) + "\n")
        self.stream.flush()


def _default(value):
    if isinstance(value, float) and value != value:     # NaN is not JSON
        return None
    return str(value)


def clean(value):
    """NaN/inf → None, recursively: json.dumps would write invalid NaN tokens."""
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def enabled():
    return emitter is not None


def emit(event, **fields):
    if emitter is not None:
        emitter.emit(event, **clean(fields))


@contextlib.contextmanager
def muted():
    """Silence step/progress/message events, e.g. the renders inside one tuning candidate."""
    if emitter is None:
        yield
        return
    emitter.muted += 1
    try:
        yield
    finally:
        emitter.muted -= 1


def add_argument(parser):
    parser.add_argument("--progress", choices=("text", "json"), default="text",
                        help="text: progress bars for a terminal (default). json: one "
                             "JSON event per line on stdout, for the GUI; everything "
                             "else goes to stderr.")


def _install():
    """Keep the real stdout for events; point fd 1 (and sys.stdout) at stderr."""
    global emitter
    sys.stdout.flush()
    events_fd = os.dup(1)
    os.dup2(2, 1)
    stream = os.fdopen(events_fd, "w", encoding="utf-8", buffering=1)
    emitter = JsonEmitter(stream)
    return stream


@contextlib.contextmanager
def session(tool, argv, mode):
    """Wrap a CLI main(): start/done/error events around it in json mode."""
    global emitter
    if mode != "json":
        yield
        return
    stream = _install()
    began = time.time()
    emit("start", tool=tool, argv=list(argv), pid=os.getpid(),
         ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    code = 0
    text = None
    try:
        yield
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            code = exc.code or 0
        else:
            code, text = 1, str(exc.code)
        raise
    except KeyboardInterrupt:
        code = 130
        raise
    except BaseException as exc:
        code, text = 1, f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if code == 0:
            emit("done", returncode=0, elapsed_sec=round(time.time() - began, 3))
        else:
            if text is None:
                text = emitter.last_error or ("Interrupted" if code == 130 else
                                              f"Exited with code {code}")
            emit("error", text=text, returncode=code)
        stream.flush()
        emitter = None
