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


def test_eta_appears_once_rate_is_known(monkeypatch):
    import video_audio_cleaner.progress as prog
    clock = [1000.0]
    monkeypatch.setattr(prog.time, "time", lambda: clock[0])
    out = io.StringIO()
    rep = ProgressReporter("Render", 60.0, stream=out, non_tty_interval=0)
    rep.update(0.0)
    clock[0] += 10
    rep.update(20.0)          # 2 sec/sec -> 40 s of audio left = 20 s
    lines = out.getvalue().splitlines()
    assert "ETA" not in lines[0]
    assert lines[1] == "Render: 33.3% (20.0s/60.0s), ETA 0:20"


def test_format_eta():
    from video_audio_cleaner.progress import format_eta
    assert format_eta(0) == "0:00"
    assert format_eta(125) == "2:05"
    assert format_eta(3725) == "1:02:05"


def test_ffmpeg_speed_formatting():
    from video_audio_cleaner.ffmpeg_utils import format_speed
    assert format_speed("31.7x") == "31.7 sec/sec"
    assert format_speed("1.68e+03x") == "1680 sec/sec"
    assert format_speed("N/A") is None
    assert format_speed("  0.98x ") == "1.0 sec/sec"
