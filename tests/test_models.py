"""Slow tier: exercises the real DeepFilterNet and Demucs models.

Run with:  ./venv/bin/pytest -m slow
"""
import contextlib
import io
import warnings

import numpy as np
import pytest

from video_audio_cleaner import metrics as M

pytestmark = pytest.mark.slow


def quiet(fn, *a, **kw):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*a, **kw)


@pytest.fixture(scope="module")
def denoised(fx, tmp_path_factory):
    from video_audio_cleaner import denoise
    root = tmp_path_factory.mktemp("denoised")
    out = {}
    for atten in (10.0, 30.0, None):
        path = root / f"dn_{atten}.wav"
        quiet(denoise.denoise_file, str(fx.mix_noisy), str(path), atten_lim_db=atten)
        out[atten] = path
    return out


def test_deepfilternet_loads_on_installed_torchaudio():
    """Guards the torch/torchaudio unpin: DeepFilterNet must load via our shim."""
    import torchaudio

    from video_audio_cleaner import denoise
    assert denoise.HAS_DF_API, f"DeepFilterNet failed to load with torchaudio {torchaudio.__version__}"


def test_denoise_removes_hiss_monotonically(fx, denoised):
    clean, sr = M.load_mono(fx.clean_speech)
    noisy, _ = M.load_mono(fx.mix_noisy)
    sh = {}
    for atten, path in denoised.items():
        x, _ = M.load_mono(path)
        sh[atten] = M.speech_to_hiss_db(x, sr)
        # Against clean ground truth, removing noise must never move us away
        # from the speech.
        assert M.speech_fidelity(clean, x, sr) > M.speech_fidelity(clean, noisy, sr)
    assert M.speech_to_hiss_db(noisy, sr) < sh[10.0] < sh[30.0] < sh[None]


def test_crossfade_not_worse_than_hard_joins(fx, tmp_path):
    from video_audio_cleaner import denoise
    paths = {}
    for name, kw in (("ref", dict(chunk_len_sec=1000.0)),
                     ("xf", dict(crossfade_sec=0.5)),
                     ("hard", dict(crossfade_sec=0.0))):
        paths[name] = tmp_path / f"{name}.wav"
        quiet(denoise.denoise_file, str(fx.mix_noisy), str(paths[name]), atten_lim_db=20.0, **kw)
    ref, _ = M.load_mono(paths["ref"])
    err = {k: float(np.sqrt(np.mean((M.load_mono(paths[k])[0] - ref) ** 2))) for k in ("xf", "hard")}
    assert err["xf"] <= err["hard"]


def test_profile_hiss_removes_more_than_patch(fx, denoised, tmp_path):
    from video_audio_cleaner import hiss
    base = str(denoised[10.0])
    prof, patch = tmp_path / "prof.wav", tmp_path / "patch.wav"
    quiet(hiss.reduce_hiss_profile, base, str(prof), str(tmp_path), 12.0, 0.0, duration=60.0)
    quiet(hiss.reduce_hiss_patch, base, str(patch), 0.0003, 0.0025, duration=60.0)
    p, sr = M.load_mono(prof)
    q, _ = M.load_mono(patch)
    assert M.speech_to_hiss_db(p, sr) > M.speech_to_hiss_db(q, sr) + 5


@pytest.mark.demucs
def test_demucs_separates_speech_from_music(fx, tmp_path):
    from video_audio_cleaner import separation
    vocals, music = tmp_path / "v.wav", tmp_path / "m.wav"
    quiet(separation.separate_to_files, str(fx.mix_music), str(vocals), str(music),
          48000, duration_sec=60.0)
    clean, _ = M.load_mono(fx.clean_speech)
    mix, _ = M.load_mono(fx.mix_music)
    v, _ = M.load_mono(vocals)
    assert M.snr_db(clean, v) >= M.snr_db(clean, mix) + 8


def test_full_pipeline_keeps_audio_in_sync(fx, tmp_path):
    """B6, end to end: every stage together must not shift the audio."""
    import subprocess
    import sys
    from pathlib import Path
    clean_audio = Path(sys.executable).with_name("clean-audio")
    out_dir = tmp_path / "o"
    subprocess.run(
        [str(clean_audio), str(fx.video), "--output_dir", str(out_dir),
         "--skip_first_n_sec", "8s", "--process_n_seconds", "20s",
         "--filter_audio_only", "--normalize", "--audio_gain", "2dB",
         "--reduce_hiss", "--declick"],
        check=True, capture_output=True, text=True)
    produced, sr = M.load_mono(next(out_dir.glob("*.wav")))
    reference, _ = M.load_mono(fx.mix_noisy)
    reference = reference[8 * sr:28 * sr]
    n = min(len(produced), len(reference))
    corr = np.fft.irfft(np.fft.rfft(produced[:n], 2 * n) * np.conj(np.fft.rfft(reference[:n], 2 * n)))
    window = int(0.2 * sr)
    lags = np.concatenate([corr[-window:], corr[:window + 1]])
    lag = int(np.argmax(lags)) - window
    assert abs(lag) <= 1, f"audio shifted by {lag} samples ({lag / sr * 1000:.1f} ms)"
    assert abs(len(produced) - 20 * sr) <= 1
