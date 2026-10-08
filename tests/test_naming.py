import pytest

from video_audio_cleaner.cli import _validate_output_name, derive_output_name

B4 = pytest.mark.xfail(strict=True, reason="B4: accepted, fixed in Phase 2")


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
    assert name(requested_duration=None) == "04-chat_07m00s-89m41s.mp4"


@pytest.mark.parametrize("bad", [
    "a/b.mp4",
    pytest.param("..", marks=B4),
    pytest.param(".", marks=B4),
    pytest.param("   ", marks=B4),
])
def test_rejects_non_filenames(bad):
    with pytest.raises(RuntimeError):
        _validate_output_name(bad)


def test_accepts_plain_filename():
    assert _validate_output_name("out.mp4") == "out.mp4"
