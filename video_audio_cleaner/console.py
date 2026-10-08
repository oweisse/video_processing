"""Terminal output helpers shared by every stage."""
import sys

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
    sys.stdout.write("\n")
    _styled(f"[{step_num}/{total_steps}] {title}", "blue")


def print_success(message):
    _styled(f"✔ {message}", "green")


def print_warning(message):
    _styled(f"⚠ {message}", "yellow")


def print_error(message):
    _styled(f"✘ {message}", "red", stream=sys.stderr)


def print_info(message):
    _styled(message)
