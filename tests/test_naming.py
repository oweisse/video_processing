import pytest

from video_audio_cleaner.cli import _validate_output_name, derive_output_name

def name(**kw):
    base = dict(input_path="/x/04-chat.mp4", requested_start=420.0,
                requested_duration=60.0, file_duration=5381.0, has_window=True,
                compare_mode=False, audio_only=False)
    base.update(kw)
    return derive_output_name(**base)


def test_video_name_is_input_plus_window():
    assert name() == "04-chat_07m00s-08m00s.mp4"


def test_audio_only_is_wav():
    assert name(audio_only=True) == "04-chat_07m00s-08m00s.wav"


def test_compare_keeps_suffix():
    assert name(compare_mode=True) == "04-chat_compare_07m00s-08m00s.wav"


def test_no_window_is_bare_name():
    assert name(has_window=False) == "04-chat.mp4"


def test_open_ended_window_uses_file_end():
    assert name(requested_duration=None) == "04-chat_07m00s-01h29m41s.mp4"


@pytest.mark.parametrize("bad", [
    "a/b.mp4",
    "..",
    ".",
    "   ",
    "",
])
def test_rejects_non_filenames(bad):
    with pytest.raises(RuntimeError):
        _validate_output_name(bad if bad else " ")


def test_accepts_plain_filename():
    assert _validate_output_name("out.mp4") == "out.mp4"


def test_hour_long_windows_name_in_hours():
    assert name(requested_start=3600.0, requested_duration=1200.0) == "04-chat_01h00m00s-01h20m00s.mp4"


# B3: the merge must always get a -t, including when processing to the end.
from video_audio_cleaner.cli import plan_window  # noqa: E402


def test_window_with_requested_duration_includes_snap():
    assert plan_window(9.0, 8.0, 10.0, 60.0) == (1.0, 11.0, 11.0)


def test_window_to_end_of_file_is_explicit():
    snap, actual, work = plan_window(9.0, 8.0, None, 60.0)
    assert (snap, actual, work) == (1.0, 52.0, 52.0)


def test_window_unknown_file_length():
    assert plan_window(0.0, 0.0, None, None) == (0.0, None, None)


# ---------------------------------------------------------------- --tag and the -o guard
from video_audio_cleaner.cli import check_name_window  # noqa: E402


def test_tag_follows_the_window():
    assert name(tag="no-music") == "04-chat_07m00s-08m00s_no-music.mp4"
    assert name(tag="x", compare_mode=True) == "04-chat_compare_07m00s-08m00s_x.wav"


def test_tag_flag_parses():
    from video_audio_cleaner.cli import build_parser
    args = build_parser().parse_args(["in.mp4", "--output_dir", "o", "--tag", "no-music"])
    assert args.tag == "no-music"


@pytest.mark.parametrize("given, start, end", [
    ("02-wedding_09m10s-09m20s_no-music.mp4", 550, 560),
    ("mine.mp4", 550, 560),                      # no window in the name: anything goes
    ("x_01h05m00s-01h05m25s.mp4", 3900, 3925),
    ("x_07m00s-end.mp4", 420, None),
])
def test_name_window_matching_the_run_is_accepted(given, start, end):
    check_name_window(given, start, end)


@pytest.mark.parametrize("given, start, end", [
    ("02-wedding_09m10s-12m20s_no-music.mp4", 550, 560),  # the stale-end-time case
    ("x_09m00s-09m20s.mp4", 550, 560),
    ("x_07m00s-end.mp4", 420, 480),
])
def test_name_window_not_matching_the_run_is_refused(given, start, end):
    with pytest.raises(RuntimeError, match="--tag"):
        check_name_window(given, start, end)
