"""Background hiss reduction.

Two methods:

* ``patch`` — ffmpeg's ``anlmdn``, a time-domain non-local-means smoother. It
  needs no noise sample, but averaging similar patches across time smears the
  audio slightly (heard as echo) and leaves some warbling.
* ``profile`` — learn this recording's own noise fingerprint from a speech-free
  stretch and subtract that specific shape, the way Audacity's noise-profile
  reduction works. This is what avoids the "musical noise" warble, because the
  filter is no longer guessing what the noise looks like.

The profile method prepends the noise sample to the audio and uses ``asendcmd``
to tell ``afftdn`` to measure during it, then trims the sample back off. ffmpeg
processes linearly, so the sample has to come first.
"""
import os

import numpy as np
import soundfile as sf

from . import ffmpeg_utils
from .console import print_info, print_warning

NOISE_LEN_SEC = 1.0


def find_quietest_region(path, length_sec=NOISE_LEN_SEC):
    """Start time (seconds) of the quietest stretch — our best guess at pure noise."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    win = max(1, int(0.1 * sr))
    count = len(mono) // win
    if count < 2:
        return 0.0
    rms = np.array([
        np.sqrt(np.mean(mono[i * win:(i + 1) * win] ** 2)) + 1e-12
        for i in range(count)
    ])
    span = max(1, int(length_sec / 0.1))
    if span >= count:
        return 0.0
    smoothed = np.convolve(rms, np.ones(span) / span, mode="valid")
    return float(np.argmin(smoothed) * 0.1)


def reduce_hiss_patch(input_wav, output_wav, strength, window, duration=None,
                      prefix="Reducing hiss"):
    cmd = ["ffmpeg", "-y", "-i", input_wav,
           "-filter:a", f"anlmdn=s={strength:g}:p=0.002:r={window:g}",
           "-acodec", "pcm_f32le", output_wav]
    ffmpeg_utils.run_ffmpeg(cmd, duration, prefix)


def reduce_hiss_profile(input_wav, output_wav, temp_dir, noise_reduction,
                        gain_smooth, noise_at=None, duration=None,
                        prefix="Reducing hiss"):
    """Sample a speech-free stretch, then subtract that measured noise shape."""
    if noise_at is None:
        noise_at = find_quietest_region(input_wav)
        print_info(f"Noise profile sampled from the quietest {NOISE_LEN_SEC:g}s "
                   f"at {noise_at:.2f}s into the window.")
    else:
        print_info(f"Noise profile sampled at {noise_at:.2f}s (as requested).")

    noise_wav = os.path.join(temp_dir, "profile_noise.wav")
    joined_wav = os.path.join(temp_dir, "profile_joined.wav")

    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-ss", f"{noise_at:.3f}", "-t", f"{NOISE_LEN_SEC:g}",
         "-i", input_wav, "-acodec", "pcm_f32le", noise_wav],
        NOISE_LEN_SEC, "Sampling noise", quiet=True)

    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", noise_wav, "-i", input_wav,
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[o]", "-map", "[o]",
         "-acodec", "pcm_f32le", joined_wav],
        (duration + NOISE_LEN_SEC) if duration else None, "Preparing", quiet=True)

    # Measure across the prepended sample, stopping just short of its end.
    sample_end = max(0.05, NOISE_LEN_SEC - 0.1)
    commands = f"0.0 afftdn sn start; {sample_end:g} afftdn sn stop"
    filtered_wav = os.path.join(temp_dir, "profile_filtered.wav")
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", joined_wav,
         "-filter:a",
         f"asendcmd=c='{commands}',"
         f"afftdn=nr={noise_reduction:g}:nf=-40:gs={gain_smooth:g}",
         "-acodec", "pcm_f32le", filtered_wav],
        (duration + NOISE_LEN_SEC) if duration else None, prefix)

    # Drop the sample we prepended.
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-ss", f"{NOISE_LEN_SEC:g}", "-i", filtered_wav,
         "-acodec", "pcm_f32le", output_wav],
        duration, "Trimming", quiet=True)

    if duration:
        try:
            produced = ffmpeg_utils.probe_duration(output_wav)
            if produced and abs(produced - duration) > 0.25:
                print_warning(f"Hiss stage produced {produced:.2f}s where "
                              f"{duration:.2f}s was expected.")
        except OSError:
            pass
