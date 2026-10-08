"""Deterministic synthetic test material.

Everything is generated locally: speech from macOS `say`, the rest from seeded
numpy noise and ffmpeg's lavfi sources. No network, no real recordings.

Design notes that matter for the tests:
* The music bed is bass + percussive noise + a quiet pad. Pure sine chords sit in
  the vocal range and Demucs files them under "vocals", so they would make the
  separation test meaningless.
* The click train has jittered intervals. The real projector tick is not
  strictly periodic, and a perfectly periodic train would make declicking
  unrealistically easy.
* The video has a keyframe every 2 s (24 fps, GOP 48) so keyframe snapping is
  deterministic: a requested 9 s start must snap to 8 s.
"""
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000
DURATION = 60.0
SENTENCE = ("Good evening everyone, and thank you all so much for being here with "
            "us tonight. We are truly delighted to see so many familiar faces in "
            "the room. Please raise your glasses as we celebrate this occasion.")


@dataclass(frozen=True)
class Fixture:
    root: Path
    clean_speech: Path      # 60 s, speech only (looped sentence with pauses)
    hiss: Path              # 60 s, the hiss alone
    clicks: Path            # 60 s, the click train alone
    music: Path             # 60 s, the music bed alone
    mix_noisy: Path         # speech + hiss + clicks
    mix_music: Path         # speech + music + hiss
    video: Path             # 60 s video, audio = mix_noisy


def have_tools():
    return shutil.which("say") is not None and shutil.which("ffmpeg") is not None


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _write(path, x):
    sf.write(str(path), np.asarray(x, dtype=np.float32), SR, subtype="FLOAT")


def _speech(root):
    aiff = root / "say.aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), SENTENCE], check=True)
    one = root / "say48.wav"
    _ffmpeg("-i", str(aiff), "-ac", "1", "-ar", str(SR), "-acodec", "pcm_f32le", str(one))
    sentence, _ = sf.read(str(one), dtype="float32")
    sentence = sentence / (np.abs(sentence).max() + 1e-9) * 0.6
    gap = np.zeros(int(1.6 * SR), dtype=np.float32)  # >1 s: room for a noise-only sample
    total = int(DURATION * SR)
    parts, length = [], 0
    while length < total:
        parts += [sentence, gap]
        length += len(sentence) + len(gap)
    return np.concatenate(parts)[:total]


def _hiss(rng, n, dbfs=-35.0):
    return rng.standard_normal(n).astype(np.float32) * (10 ** (dbfs / 20))


def _bandpass(x, lo, hi):
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, n=len(x))


def _clicks(rng, n, rate=25.0, jitter=0.08, peak=0.12):
    out = np.zeros(n, dtype=np.float32)
    burst_len = int(0.001 * SR)
    decay = np.exp(-np.linspace(0, 6, burst_len)).astype(np.float32)
    t = 0.0
    period = 1.0 / rate
    while True:
        t += period * (1 + rng.uniform(-jitter, jitter))
        i = int(t * SR)
        if i + burst_len >= n:
            break
        burst = _bandpass(rng.standard_normal(burst_len * 8), 2000, 10000)[:burst_len]
        burst = burst / (np.abs(burst).max() + 1e-9)
        out[i:i + burst_len] += burst.astype(np.float32) * decay * peak
    return out


def _music(rng, n):
    t = np.arange(n) / SR
    bass = np.sin(2 * np.pi * 82 * t) * (1 - 0.9 * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))) * 0.5
    noise = _bandpass(rng.standard_normal(n), 2500, SR / 2)
    noise = noise / (np.sqrt(np.mean(noise ** 2)) + 1e-9) * 0.35 * 0.3
    drums = noise * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))
    pad = np.sin(2 * np.pi * 330 * t) * 0.06
    return (bass + drums + pad).astype(np.float32)


def build(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261008)

    speech = _speech(root)
    n = len(speech)
    hiss = _hiss(rng, n)
    clicks = _clicks(rng, n)
    music = _music(rng, n)

    paths = {
        "clean_speech": root / "clean_speech.wav",
        "hiss": root / "hiss.wav",
        "clicks": root / "clicks.wav",
        "music": root / "music.wav",
        "mix_noisy": root / "mix_noisy.wav",
        "mix_music": root / "mix_music.wav",
    }
    _write(paths["clean_speech"], speech)
    _write(paths["hiss"], hiss)
    _write(paths["clicks"], clicks)
    _write(paths["music"], music)
    _write(paths["mix_noisy"], speech + hiss + clicks)
    _write(paths["mix_music"], speech + music + hiss)

    video = root / "fixture.mp4"
    _ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=640x480:rate=24:duration={DURATION:g}",
            "-i", str(paths["mix_noisy"]),
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-g", "48",
            "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-t", f"{DURATION:g}", str(video))
    return Fixture(root=root, video=video, **paths)
