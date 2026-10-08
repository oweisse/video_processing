"""Parsing and formatting of the '1m30s' style time arguments."""
import argparse
import re


def parse_time_string(val):
    if val is None:
        return None
    val = str(val).strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        pass

    val_lower = val.lower()
    minutes = 0.0
    seconds = 0.0

    m_match = re.search(r"(\d+(?:\.\d+)?)m", val_lower)
    s_match = re.search(r"(\d+(?:\.\d+)?)s", val_lower)

    if not m_match and not s_match:
        raise argparse.ArgumentTypeError(
            f"Invalid time format: '{val}'. Expected a number of seconds, "
            f"or format like '1m30s', '2m', '45s'."
        )

    if m_match:
        minutes = float(m_match.group(1))
    if s_match:
        seconds = float(s_match.group(1))

    return minutes * 60.0 + seconds


def format_time_filename(seconds):
    if seconds is None:
        return "end"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}m{secs:02d}s"
