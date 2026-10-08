"""No processing stage may shift the audio in time.

The merge copies video unchanged, so any delay a filter adds to the audio becomes
lip-sync error in the output. afftdn, anlmdn and alimiter all have look-ahead
latency that ffmpeg does not compensate (measured: 1200 / p+r / 239 samples).
"""
import subprocess

import numpy as np
import pytest
import soundfile as sf

from video_audio_cleaner import hiss

SR = 48000
B6 = pytest.mark.xfail(strict=True, reason="B6: uncompensated filter latency, fixed in Phase 2")


def impulse(path, at=1.0, total=3.0):
    x = np.zeros(int(total * SR), np.float32)
    x[int(at * SR)] = 0.5
    sf.write(str(path), x, SR, subtype="FLOAT")
    return int(at * SR)


def peak_index(path):
    y, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return int(np.argmax(np.abs(y.mean(axis=1))))


@B6
def test_patch_hiss_adds_no_delay(tmp_path):
    src, out = tmp_path / "i.wav", tmp_path / "o.wav"
    expected = impulse(src)
    hiss.reduce_hiss_patch(str(src), str(out), 0.0003, 0.0025, duration=3.0)
    assert peak_index(out) == expected


@B6
def test_profile_hiss_adds_no_delay(tmp_path, fx):
    # The real speech fixture: profile mode needs a believable noise sample.
    src = fx.mix_noisy
    out = tmp_path / "o.wav"
    hiss.reduce_hiss_profile(str(src), str(out), str(tmp_path), 12.0, 0.0, duration=60.0)
    a, _ = sf.read(str(src), dtype="float32", always_2d=True)
    b, _ = sf.read(str(out), dtype="float32", always_2d=True)
    a, b = a.mean(axis=1), b.mean(axis=1)
    n = min(len(a), len(b))
    corr = np.fft.irfft(np.fft.rfft(b[:n], 2 * n) * np.conj(np.fft.rfft(a[:n], 2 * n)))
    window = int(0.1 * SR)
    lags = np.concatenate([corr[-window:], corr[:window + 1]])
    assert int(np.argmax(lags)) - window == 0


def test_loudnorm_adds_no_delay(tmp_path):
    src, out = tmp_path / "i.wav", tmp_path / "o.wav"
    expected = impulse(src)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-af", "loudnorm=I=-16,aresample=48000", "-acodec", "pcm_f32le", str(out)],
                   check=True)
    assert peak_index(out) == expected
