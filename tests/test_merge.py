"""The merged video must start in sync and end where the audio ends."""
import subprocess
from types import SimpleNamespace

import pytest

from video_audio_cleaner.cli import _merge_video

FRAME = 1 / 24 + 0.004


def streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    found = {}
    for line in out.strip().splitlines():
        kind, start, dur = line.split(",")
        found[kind] = (float(start), float(dur))
    return found


def extract(src, start, duration, dst):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start}", "-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration}"]
    cmd += ["-vn", "-acodec", "pcm_f32le", "-ar", "48000", str(dst)]
    subprocess.run(cmd, check=True)


ARGS = SimpleNamespace(precise_cut=False, video_crf=18, audio_bitrate="192k")


@pytest.mark.parametrize("start, duration", [(8.0, 10.0), (0.0, 10.0)])
def test_merge_window_in_sync(fx, tmp_path, start, duration):
    wav = tmp_path / "a.wav"
    extract(fx.video, start, duration, wav)
    out = tmp_path / "out.mp4"
    _merge_video(str(fx.video), str(wav), str(out), start, duration, duration, ARGS)
    s = streams(out)
    assert s["video"][0] == pytest.approx(0.0, abs=0.001)
    assert s["audio"][0] == pytest.approx(0.0, abs=0.001)
    assert abs(s["video"][1] - s["audio"][1]) <= FRAME
    assert s["audio"][1] == pytest.approx(duration, abs=FRAME)


def test_no_partial_file_left(fx, tmp_path):
    wav = tmp_path / "a.wav"
    extract(fx.video, 8.0, 4.0, wav)
    out = tmp_path / "out.mp4"
    _merge_video(str(fx.video), str(wav), str(out), 8.0, 4.0, 4.0, ARGS)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav", "out.mp4"]
