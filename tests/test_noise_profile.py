import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from video_audio_cleaner.hiss import find_quietest_region

SR = 48000
CLEAN = Path(sys.executable).with_name("clean-audio")


def test_finds_the_real_gap(tmp_path):
    rng = np.random.default_rng(1)
    x = rng.standard_normal(10 * SR).astype(np.float32) * 0.1
    x[6 * SR:int(7.5 * SR)] *= 0.01           # a 1.5 s near-silent gap at 6 s
    path = tmp_path / "a.wav"
    sf.write(str(path), x, SR, subtype="FLOAT")
    sample = find_quietest_region(str(path), 1.0)
    assert 6.0 <= sample.start <= 6.5
    assert abs(sample.excess_db) < 1


def _write(tmp_path, x):
    path = tmp_path / "a.wav"
    sf.write(str(path), x.astype(np.float32), SR, subtype="FLOAT")
    return str(path)


def test_flags_speech_without_a_gap(tmp_path):
    # Syllable-like bursts every 0.3 s: lots of level swing, never a 1 s pause.
    rng = np.random.default_rng(2)
    t = np.arange(10 * SR) / SR
    envelope = 0.02 + 0.3 * (np.sin(2 * np.pi * t / 0.3) > 0)
    x = rng.standard_normal(len(t)) * envelope
    sample = find_quietest_region(_write(tmp_path, x), 1.0)
    assert sample.likely_contains_speech(6.0)


def test_steady_noise_is_not_flagged(tmp_path):
    # A window of nothing but room noise (seen on the owner's file at 1h01m).
    rng = np.random.default_rng(3)
    x = rng.standard_normal(10 * SR) * 0.003
    sample = find_quietest_region(_write(tmp_path, x), 1.0)
    assert not sample.likely_contains_speech(6.0)


def test_real_gap_is_not_flagged(tmp_path):
    rng = np.random.default_rng(4)
    x = rng.standard_normal(10 * SR) * 0.1
    x[6 * SR:int(7.5 * SR)] *= 0.01
    assert not find_quietest_region(_write(tmp_path, x), 1.0).likely_contains_speech(6.0)


def test_noise_profile_at_out_of_range_fails_fast(fx, tmp_path):
    out = subprocess.run(
        [str(CLEAN), str(fx.video), "--output_dir", str(tmp_path / "o"),
         "--skip_first_n_sec", "9s", "--process_n_seconds", "5s",
         "--reduce_hiss", "--noise_profile_at", "4.5s"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 1
    assert "leaves no room" in out.stderr
    assert "between 0 and 4.00s" in out.stderr


def test_levels_stream_long_files_in_blocks(tmp_path, monkeypatch):
    """frame_levels must not read the whole file at once."""
    import soundfile

    from video_audio_cleaner import levels
    x = np.random.default_rng(5).standard_normal(130 * SR).astype(np.float32) * 0.01
    path = _write(tmp_path, x)
    read_whole = []
    monkeypatch.setattr(soundfile, "read", lambda *a, **k: read_whole.append(1))
    lv = levels.frame_levels(path)
    assert not read_whole
    assert len(lv.rms) == 1300
    expected = np.sqrt(np.mean(x[:SR // 10] ** 2))
    assert abs(lv.rms[0] - expected) < 1e-6
