import argparse

import pytest

from video_audio_cleaner.timeparse import format_time_filename, parse_time_string

B1 = pytest.mark.xfail(strict=True, reason="B1: misparse, fixed in Phase 2")


@pytest.mark.parametrize("text, seconds", [
    ("90", 90.0),
    ("1.5", 1.5),
    ("45s", 45.0),
    ("2m", 120.0),
    ("1m30s", 90.0),
    ("2m 30s", 150.0),
    ("8m50s", 530.0),
    pytest.param("1h20m", 4800.0, marks=B1),
    pytest.param("1h", 3600.0, marks=B1),
    pytest.param("1h20m5s", 4805.0, marks=B1),
    pytest.param("5m30", 330.0, marks=B1),
])
def test_valid(text, seconds):
    assert parse_time_string(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", [
    "abc",
    pytest.param("-5", marks=B1),
    "5x",
    pytest.param("30s2m", marks=B1),
    pytest.param("1m30s5", marks=B1),
])
def test_invalid(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_string(text)


def test_empty_is_none():
    # An empty value means "not given" when used as an argparse default.
    assert parse_time_string("") is None
    assert parse_time_string(None) is None


@pytest.mark.parametrize("seconds, text", [
    (0, "00m00s"),
    (530, "08m50s"),
    (None, "end"),
    pytest.param(4800, "01h20m00s", marks=B1),
])
def test_format_time_filename(seconds, text):
    assert format_time_filename(seconds) == text
