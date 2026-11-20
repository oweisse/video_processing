import numpy as np

from video_audio_cleaner import musicgate


def _stem(sr=48000):
    """20 s of ambience (noise), then 10 s of a chord over the same ambience."""
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.02, 30 * sr).astype(np.float32)
    t = np.arange(10 * sr) / sr
    chord = sum(0.05 * np.sin(2 * np.pi * f * t) for f in (220, 277.2, 329.6, 440)).astype(np.float32)
    x = noise.copy()
    x[20 * sr:] += chord
    return np.stack([x, x]), sr


def test_music_kept_and_ambience_turned_down():
    stem, sr = _stem()
    gain, info = musicgate.gate_gain(stem, sr)
    db = lambda a, b: 20 * np.log10(gain[a * sr:b * sr].mean())
    assert db(2, 17) < -19                     # ambience only: down by the range
    assert db(22, 29) > -0.5                   # music: untouched
    assert 0.2 < info["music_fraction"] < 0.5


def test_gate_keeps_shape_and_works_on_tensors():
    import torch
    stem, sr = _stem()
    out, _ = musicgate.gate(torch.from_numpy(stem), sr)
    assert isinstance(out, torch.Tensor) and tuple(out.shape) == stem.shape
