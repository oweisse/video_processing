"""Background hiss reduction.

Two methods:

* ``patch`` — ffmpeg's ``anlmdn``, a time-domain non-local-means smoother. It
  needs no noise sample, but averaging similar patches across time smears the
  audio slightly (heard as echo) and leaves some warbling.
* ``profile`` — learn this recording's own noise fingerprint from a speech-free
  stretch and subtract that specific shape, the way Audacity's noise-profile
  reduction works. This is what avoids the "musical noise" warble, because the
  filter is no longer guessing what the noise looks like.

The filters themselves run inside the single render graph (graph.py); this
module only decides where profile mode takes its noise sample.
"""
from dataclasses import dataclass

import numpy as np

from .console import print_info, print_warning
from .levels import frame_levels


@dataclass
class NoiseSample:
    start: float       # seconds into the window
    excess_db: float   # sample level above the window's quietest frames (10th pct)

    def likely_contains_speech(self, max_excess_db):
        """True when the sample sits clearly above the window's noise floor.

        A noise-only sample averages out at the floor (measured within 0.5 dB on
        real and synthetic material); a 1 s stretch containing any speech sits
        well above it (18 dB for continuous syllables). Comparing against the
        median instead fails on quiet recordings, where true noise is only a few
        dB below the median level.
        """
        return self.excess_db > max_excess_db


def find_quietest_region(path, length_sec):
    """The quietest `length_sec` stretch — our best guess at pure noise.

    Streams the file (levels.frame_levels), so long windows are never loaded
    whole.
    """
    lv = frame_levels(path)
    rms = lv.rms
    if len(rms) < 2:
        return NoiseSample(0.0, 0.0)
    floor_db = lv.floor_db
    span = max(1, int(round(length_sec / lv.frame_sec)))
    if span >= len(rms):
        return NoiseSample(0.0, float(20 * np.log10(np.mean(rms))) - floor_db)
    smoothed = np.convolve(rms, np.ones(span) / span, mode="valid")
    best = int(np.argmin(smoothed))
    return NoiseSample(best * lv.frame_sec, float(20 * np.log10(smoothed[best])) - floor_db)


def choose_noise_sample(denoised_wav, noise_at, noise_len, max_excess_db):
    """Where profile mode samples its noise: the user's time, or the quietest stretch."""
    if noise_at is not None:
        print_info(f"Noise profile sampled at {noise_at:.2f}s (as requested).")
        return float(noise_at)
    sample = find_quietest_region(denoised_wav, noise_len)
    print_info(f"Noise profile sampled from the quietest {noise_len:g}s at "
               f"{sample.start:.2f}s into the window "
               f"({sample.excess_db:+.1f} dB vs the noise floor).")
    if sample.likely_contains_speech(max_excess_db):
        print_warning(
            f"No clear pause found: the quietest {noise_len:g}s sits "
            f"{sample.excess_db:.1f} dB above the window's noise floor, so the 'noise' "
            f"sample probably contains speech and profile mode will remove some of it. "
            f"Point --noise_profile_at at a pause, or use --hiss_method patch.")
    return sample.start
