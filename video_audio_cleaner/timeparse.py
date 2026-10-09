"""Parsing and formatting of the '1h20m30s' style time arguments."""
import argparse
import math
import re

_NUM = r"(\d+(?:\.\d+)?)"
# Units must appear in h, m, s order. A bare number is allowed as the last
# component only directly after minutes ('5m30' = 5m30s); after hours it would
# be ambiguous, so '1h30' is rejected.
_PATTERN = re.compile(
    rf"""^\s*
    (?:{_NUM}\s*h\s*)?
    (?:{_NUM}\s*m\s*)?
    (?:{_NUM}\s*(s)?\s*)?
    $""",
    re.VERBOSE | re.IGNORECASE,
)

ACCEPTED = "seconds ('90', '1.5') or h/m/s in that order ('1h20m', '8m50s', '5m30', '45s')"


def _invalid(val, why=""):
    detail = f" ({why})" if why else ""
    return argparse.ArgumentTypeError(
        f"Invalid time '{val}'{detail}. Expected {ACCEPTED}.")


def parse_time_string(val):
    """Seconds from a time string, or None for an empty value."""
    if val is None:
        return None
    text = str(val).strip()
    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if not math.isfinite(number):
            raise _invalid(val, "not a finite number")
        if number < 0:
            raise _invalid(val, "negative")
        return number

    match = _PATTERN.match(text)
    if not match:
        raise _invalid(val)
    hours, minutes, seconds, s_unit = match.groups()
    if hours is None and minutes is None and seconds is None:
        raise _invalid(val)
    if seconds is not None and s_unit is None and minutes is None:
        raise _invalid(val, "a bare number may only follow minutes")
    return (float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0))


def positive_time(val):
    """argparse type: a time that must be greater than zero."""
    seconds = parse_time_string(val)
    if seconds is not None and seconds <= 0:
        raise _invalid(val, "must be greater than zero")
    return seconds


def format_time_filename(seconds):
    if seconds is None:
        return "end"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}h{mins:02d}m{secs:02d}s"
    return f"{mins:02d}m{secs:02d}s"
