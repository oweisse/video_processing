"""No processing stage may shift the audio in time.

The merge copies video unchanged, so any delay a filter adds to the audio becomes
lip-sync error. afftdn, anlmdn and alimiter all look ahead and ffmpeg does not
compensate (measured: 1200 / patch+search / 239 samples); graph.py removes it.
"""
import numpy as np
import pytest
import soundfile as sf

from tests.render_helper import render
from video_audio_cleaner import ffmpeg_utils

SR = 48000


def impulse(path, at=1.0, total=3.0):
    x = np.zeros(int(total * SR), np.float32)
    x[int(at * SR)] = 0.5
    sf.write(str(path), x, SR, subtype="FLOAT")
    return int(at * SR), len(x)


def load(path):
    y, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return y.mean(axis=1)


def lag_samples(produced, reference, window=0.1):
    n = min(len(produced), len(reference))
    corr = np.fft.irfft(np.fft.rfft(produced[:n], 2 * n) * np.conj(np.fft.rfft(reference[:n], 2 * n)))
    w = int(window * SR)
    return int(np.argmax(np.concatenate([corr[-w:], corr[:w + 1]]))) - w


@pytest.mark.parametrize("chain, expected", [
    ("afftdn=nr=12:nf=-28", 1200),
    ("anlmdn=s=0.0003:p=0.002:r=0.0025", 216),
    ("alimiter=limit=0.97:level=false", 239),
    ("volume=6dB", 0),
])
def test_filter_latency_is_measured(chain, expected):
    assert ffmpeg_utils.filter_latency(chain, SR) == expected


@pytest.mark.parametrize("options", [
    dict(hiss_method="patch"),
    dict(audio_gain="3dB"),
    dict(declick=True),
    dict(hiss_method="patch", audio_gain="3dB", declick=True),
])
def test_render_does_not_move_an_impulse(tmp_path, options):
    src, out = tmp_path / "i.wav", tmp_path / "o.wav"
    at, length = impulse(src)
    render(src, out, 3.0, **options)
    y = load(out)
    assert len(y) == length
    assert int(np.argmax(np.abs(y))) == at


def test_profile_hiss_on_speech_keeps_sync_and_length(tmp_path, fx):
    out = tmp_path / "o.wav"
    render(fx.mix_noisy, out, 60.0, hiss_method="profile", declick=True,
           normalize=True, audio_gain="1dB")
    produced, reference = load(out), load(fx.mix_noisy)
    assert len(produced) == len(reference)
    assert lag_samples(produced, reference) == 0
