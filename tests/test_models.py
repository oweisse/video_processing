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
    ref, sr = M.load_mono(paths["ref"])
    out = {k: M.load_mono(paths[k])[0][:len(ref)] for k in ("xf", "hard")}
    # The two are near-identical away from the joins; which is closer to the
    # unchunked reference is a coin toss (macOS's `say` voice decides), so allow
    # a 10% margin rather than demanding a strict win.
    err = {k: float(np.sqrt(np.mean((v - ref) ** 2))) for k, v in out.items()}
    assert err["xf"] <= 1.1 * err["hard"]

    # What crossfading is for: no click at a join. High-frequency energy in the
    # 20 ms around each 10 s join stays within 1.5 dB of the unchunked reference.
    def above_4k(x):
        spec = np.fft.rfft(x)
        spec[np.fft.rfftfreq(len(x), 1 / sr) < 4000] = 0
        return np.fft.irfft(spec, n=len(x))
    hf_ref, hf_xf = above_4k(ref), above_4k(out["xf"])
    w = int(0.01 * sr)
    for t in range(10, int(len(ref) / sr), 10):
        j = t * sr
        excess = 10 * np.log10((np.sum(hf_xf[j - w:j + w] ** 2) + 1e-12)
                               / (np.sum(hf_ref[j - w:j + w] ** 2) + 1e-12))
        assert excess < 1.5, f"click at the {t} s join: {excess:+.1f} dB"


def test_profile_hiss_removes_more_than_patch(fx, denoised, tmp_path):
    from tests.render_helper import render
    base = denoised[10.0]
    prof, patch = tmp_path / "prof.wav", tmp_path / "patch.wav"
    render(base, prof, 60.0, hiss_method="profile")
    render(base, patch, 60.0, hiss_method="patch")
    clean, sr = M.load_mono(fx.clean_speech)
    p, _ = M.load_mono(prof)
    q, _ = M.load_mono(patch)
    assert M.speech_to_hiss_db(p, sr) > M.speech_to_hiss_db(q, sr) + 5
    # With latency compensated, profile mode must not damage the speech.
    assert M.speech_fidelity(clean, p, sr) > 0.85


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


def test_tune_then_batch_end_to_end(fx, tmp_path):
    """Tune one window on a small grid, then render it from the plan."""
    import json

    from video_audio_cleaner import batch, tune
    out = tmp_path / "tuning"
    with contextlib.redirect_stdout(io.StringIO()):
        tune.main([str(fx.video), "00m10s-00m30s", "--out_dir", str(out), "--source", "tape",
                   "--atten", "20", "--hiss", "off,profile:12", "--declick", "off,on"])
    plan = json.loads((out / "plan.json").read_text())
    window = plan["windows"][0]
    assert window["label"] == "00m10s-00m30s"
    assert len(window["candidates"]) == 4
    assert (out / "listen" / "00m10s-00m30s.wav").exists()
    assert "★" in (out / "report.md").read_text()
    assert window["met_criteria"] is True
    # The fixture's hiss is strong and the yardstick is the window's gentlest
    # candidate, so a hiss-reducing candidate should win.
    assert "--reduce_hiss" in window["flags"]
    assert window["flags"][0] == "--normalize"     # added for the output, not measured with

    rendered = tmp_path / "rendered"
    with contextlib.redirect_stdout(io.StringIO()):
        assert batch.main([str(out / "plan.json"), "--output_dir", str(rendered)]) == 0
    video = rendered / "fixture_00m10s-00m30s.mp4"
    assert video.exists()
    record = json.loads(video.with_suffix(".params.json").read_text())
    # The render followed the plan's flags exactly.
    assert record["parameters"]["reduce_hiss"] is True
    assert record["parameters"]["atten_lim_db"] == 20.0
    assert record["parameters"]["normalize"] is True


def test_autotune_end_to_end(fx, tmp_path):
    """First call tunes and caches; second reuses; the sidecar reproduces the pick."""
    import json
    import shlex
    import subprocess
    import sys
    from pathlib import Path

    from video_audio_cleaner.cli import build_parser
    clean_audio = Path(sys.executable).with_name("clean-audio")
    out = tmp_path / "o"
    argv = [str(clean_audio), str(fx.video), "--output_dir", str(out), "--skip_first_n_sec",
            "10s", "--process_n_seconds", "12s", "--filter_audio_only", "--normalize",
            "--autotune", "--source", "tape"]
    first = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    assert "Autotune picked:" in first and "tuned 36 candidates" in first
    cache = out / "_tuning" / "fixture" / "plan.json"
    assert cache.exists()

    second = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    assert "reused earlier tuning" in second

    record = json.loads(next(out.glob("*.params.json")).read_text())
    params, auto = record["parameters"], record["results"]["autotune"]
    assert params["autotune"] is False                 # resolved: the pick, not the request
    assert auto["reused"] is True and auto["cache"] == str(cache)
    assert params["declick"] == bool(auto["picked"]["declick"])
    if params["declick"]:
        assert params["declick_nr"] == auto["picked"]["declick"]
    assert params["atten_lim_db"] == auto["picked"]["atten"]
    rebuilt = vars(build_parser().parse_args(shlex.split(record["reproduce"])[1:]))
    assert rebuilt == params and "--autotune" not in record["reproduce"]


def test_auto_loudness_target_keeps_dynamics(fx, tmp_path):
    """--target_lufs auto must end in a plain volume change, recorded reproducibly."""
    import json
    import shlex
    import subprocess
    import sys
    from pathlib import Path

    from video_audio_cleaner.cli import build_parser
    clean_audio = Path(sys.executable).with_name("clean-audio")
    out = tmp_path / "o"
    log = subprocess.run([str(clean_audio), str(fx.video), "--output_dir", str(out),
                          "--skip_first_n_sec", "10s", "--process_n_seconds", "15s",
                          "--filter_audio_only", "--normalize", "--target_lufs", "auto",
                          "--reduce_hiss"], capture_output=True, text=True, check=True).stdout
    assert "Loudness target chosen" in log
    assert "(two-pass, linear," in log
    record = json.loads(next(out.glob("*.params.json")).read_text())
    chosen = record["results"]["loudness_target"]["chosen_lufs"]
    assert record["parameters"]["target_lufs"] == chosen          # a number, not "auto"
    assert record["results"]["loudnorm_mode"] == "linear"
    assert abs(record["results"]["final_lufs"] - chosen) < 1.0
    rebuilt = vars(build_parser().parse_args(shlex.split(record["reproduce"])[1:]))
    assert rebuilt == record["parameters"]
