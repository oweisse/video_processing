import io

from video_audio_cleaner.progress import ProgressReporter


def test_non_tty_throttles_to_interval():
    out = io.StringIO()
    rep = ProgressReporter("Work", 100.0, stream=out, non_tty_interval=3600)
    for sec in range(0, 100, 5):
        rep.update(sec)
    rep.finish()
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("Work: 0.0%")
    assert lines[-1] == "Work: 100.0% (100.0s/100.0s)"
    assert len(lines) == 2


def test_does_not_print_completion_twice():
    out = io.StringIO()
    rep = ProgressReporter("Work", 10.0, stream=out, non_tty_interval=0)
    rep.update(10.0)
    rep.finish()
    assert out.getvalue().count("100.0%") == 1


def test_unknown_total():
    out = io.StringIO()
    rep = ProgressReporter("Work", None, stream=out, non_tty_interval=0)
    rep.update(3.0)
    rep.finish()
    assert out.getvalue().splitlines() == ["Work: 3.0s", "Work: done"]
