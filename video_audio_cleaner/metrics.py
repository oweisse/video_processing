"""Objective measures of what the cleaning did to the audio.

These are the measurements behind every number quoted in the README. They are
deliberately simple and level-independent, so comparing two renders reflects
the processing rather than a gain change.

All functions take mono float32 audio as a numpy array plus its sample rate.

Usage from the shell:

    ./venv/bin/python -m video_audio_cleaner.metrics processed.wav
    ./venv/bin/python -m video_audio_cleaner.metrics original.wav processed.wav
"""
import sys

import numpy as np
import soundfile as sf

FIDELITY_BANDS_HZ = (300, 500, 800, 1200, 1800, 2600, 3400, 4500, 5800, 7000)
_EPS = 1e-12


def load_mono(path):
    """Read an audio file as (mono float32 array, sample rate)."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def _rms_normalise(x):
    return x / (np.sqrt(np.mean(x ** 2)) + _EPS)


def _stft_frames(x, sr, frame_sec=0.025):
    """Magnitude spectra of 50 %-overlapping Hann frames, plus frame RMS."""
    n = max(16, int(frame_sec * sr))
    hop = n // 2
    count = max(0, (len(x) - n) // hop)
    if count == 0:
        return np.zeros((0, n // 2 + 1)), np.zeros(0), np.fft.rfftfreq(n, 1 / sr)
    win = np.hanning(n)
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    frames = x[idx] * win
    spectra = np.abs(np.fft.rfft(frames, axis=1))
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return spectra, rms, np.fft.rfftfreq(n, 1 / sr)


def _band_envelopes_db(x, sr):
    spectra, rms, freqs = _stft_frames(_rms_normalise(x), sr)
    edges = FIDELITY_BANDS_HZ
    env = np.empty((len(edges) - 1, spectra.shape[0]))
    for b in range(len(edges) - 1):
        mask = (freqs >= edges[b]) & (freqs < edges[b + 1])
        env[b] = np.sqrt(np.sum(spectra[:, mask] ** 2, axis=1))
    return 20 * np.log10(env + _EPS), 20 * np.log10(rms + _EPS)


def speech_fidelity(ref, test, sr):
    """How well the speech structure of `ref` survives in `test` (1.0 = fully).

    Correlates per-band log envelopes over the frames where `ref` is speaking
    (above its 55th-percentile level). Gaps are excluded so that legitimately
    removing noise between words is not counted as damage.
    """
    ref_env, ref_level = _band_envelopes_db(ref, sr)
    test_env, _ = _band_envelopes_db(test, sr)
    n = min(ref_env.shape[1], test_env.shape[1])
    if n < 4:
        return float("nan")
    active = ref_level[:n] > np.percentile(ref_level[:n], 55)
    scores = []
    for b in range(ref_env.shape[0]):
        a = ref_env[b, :n][active]
        c = test_env[b, :n][active]
        a = (a - a.mean()) / (a.std() + _EPS)
        c = (c - c.mean()) / (c.std() + _EPS)
        scores.append(float(np.mean(a * c)))
    return float(np.mean(scores))


def _frame_rms(x, sr, frame_sec):
    n = max(1, int(frame_sec * sr))
    count = len(x) // n
    if count == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(x[:count * n].reshape(count, n) ** 2, axis=1)) + _EPS


def speech_to_hiss_db(x, sr):
    """Level of the loudest quarter over the quietest fifth, in dB. Higher = cleaner gaps."""
    rms = np.sort(_frame_rms(x, sr, 0.05))
    if len(rms) < 5:
        return float("nan")
    loud = np.median(rms[-max(1, len(rms) // 4):])
    quiet = np.median(rms[:max(1, len(rms) // 5)])
    return float(20 * np.log10(loud / quiet))


def warble(x, sr):
    """Musical-noise index: how much the gap spectrum flickers, in dB.

    Steady hiss scores low; isolated frequency bins popping in and out (the
    "pets pets pets" artifact of spectral denoisers) scores high.
    """
    spectra, rms, freqs = _stft_frames(_rms_normalise(x), sr)
    if spectra.shape[0] < 4:
        return float("nan")
    quiet = np.argsort(rms)[:max(4, spectra.shape[0] // 5)]
    band = (freqs >= 1000) & (freqs < 8000)
    gaps_db = 20 * np.log10(spectra[quiet][:, band] + _EPS)
    return float(np.mean(np.std(gaps_db, axis=0)))


def clicks_per_sec(x, sr, lo=2000, hi=10000):
    """Rate of sharp onsets in the click band — a proxy for mechanical tick."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    spec[(freqs < lo) | (freqs >= hi)] = 0
    band = np.fft.irfft(spec, n=len(x))
    env = _frame_rms(band, sr, 0.001)
    flux = np.diff(env)
    flux[flux < 0] = 0
    if len(flux) < 3:
        return 0.0
    thr = flux.mean() + 3 * flux.std()
    mid = flux[1:-1]
    peaks = (mid > thr) & (mid >= flux[:-2]) & (mid >= flux[2:])
    return float(np.count_nonzero(peaks) / (len(x) / sr))


def snr_db(ref, test):
    """SNR of `test` against `ref` after the best single gain on `test`."""
    n = min(len(ref), len(test))
    r, t = ref[:n].astype(np.float64), test[:n].astype(np.float64)
    gain = np.dot(t, r) / (np.dot(t, t) + _EPS)
    err = gain * t - r
    return float(10 * np.log10(np.sum(r ** 2) / (np.sum(err ** 2) + _EPS)))


def summarise(path, ref_path=None):
    x, sr = load_mono(path)
    out = {
        "speech_to_hiss_db": speech_to_hiss_db(x, sr),
        "warble": warble(x, sr),
        "clicks_per_sec": clicks_per_sec(x, sr),
    }
    if ref_path is not None:
        ref, ref_sr = load_mono(ref_path)
        if ref_sr != sr:
            raise ValueError(f"Sample rates differ: {ref_sr} vs {sr}")
        out["speech_fidelity"] = speech_fidelity(ref, x, sr)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (1, 2):
        sys.stderr.write("usage: python -m video_audio_cleaner.metrics "
                         "[REFERENCE.wav] AUDIO.wav\n")
        return 2
    ref_path, path = (None, argv[0]) if len(argv) == 1 else (argv[0], argv[1])
    for key, value in summarise(path, ref_path).items():
        print(f"{key:18s} {value:8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
