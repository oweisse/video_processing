"""Terminal output helpers shared by every stage."""
import sys

from . import events

_COLORS = {
    "blue": "\033[94m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
}
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _styled(message, color=None, stream=None):
    stream = stream or sys.stdout
    if color and stream.isatty():
        stream.write(f"{_BOLD}{_COLORS[color]}{message}{_RESET}\n")
    else:
        stream.write(f"{message}\n")
    stream.flush()


def print_step(step_num, total_steps, title):
    if events.enabled():
        events.emitter.step = step_num
        events.emit("step", index=step_num, total=total_steps, title=title)
        return
    sys.stdout.write("\n")
    _styled(f"[{step_num}/{total_steps}] {title}", "blue")


def print_success(message):
    if events.enabled():
        events.emit("message", level="success", text=message)
        return
    _styled(f"✔ {message}", "green")


def print_warning(message):
    if events.enabled():
        events.emit("message", level="warning", text=message)
        return
    _styled(f"⚠ {message}", "yellow")


def print_error(message):
    if events.enabled():
        events.emitter.last_error = message
        events.emit("message", level="error", text=message)
        return
    _styled(f"✘ {message}", "red", stream=sys.stderr)


def print_info(message):
    if events.enabled():
        events.emit("message", level="info", text=message)
        return
    _styled(message)
