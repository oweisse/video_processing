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

Both filters delay their output (afftdn by half its analysis window, anlmdn by
patch + search); ``ffmpeg_utils.compensated`` removes that shift so the audio
stays in sync with the untouched video.
"""
import os
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from . import ffmpeg_utils
from .console import print_info, print_warning


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
    """The quietest `length_sec` stretch — our best guess at pure noise."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    win = max(1, int(0.1 * sr))
    count = len(mono) // win
    if count < 2:
        return NoiseSample(0.0, 0.0)
    rms = np.sqrt(np.mean(mono[:count * win].reshape(count, win) ** 2, axis=1)) + 1e-12
    floor_db = float(np.percentile(20 * np.log10(rms), 10))
    span = max(1, int(round(length_sec / 0.1)))
    if span >= count:
        return NoiseSample(0.0, float(20 * np.log10(np.mean(rms))) - floor_db)
    smoothed = np.convolve(rms, np.ones(span) / span, mode="valid")
    best = int(np.argmin(smoothed))
    return NoiseSample(best * 0.1, float(20 * np.log10(smoothed[best])) - floor_db)


def reduce_hiss_patch(input_wav, output_wav, strength, window, duration=None,
                      sample_rate=48000, prefix="Reducing hiss"):
    chain = ffmpeg_utils.compensated(
        f"anlmdn=s={strength:g}:p=0.002:r={window:g}", sample_rate)
    cmd = ["ffmpeg", "-y", "-i", input_wav, "-filter:a", chain,
           "-acodec", "pcm_f32le", output_wav]
    ffmpeg_utils.run_ffmpeg(cmd, duration, prefix)


def reduce_hiss_profile(input_wav, output_wav, temp_dir, noise_reduction,
                        gain_smooth, noise_floor=-40.0, noise_at=None,
                        noise_len=1.0, max_excess_db=6.0, duration=None,
                        sample_rate=48000, prefix="Reducing hiss"):
    """Sample a speech-free stretch, then subtract that measured noise shape.

    Returns the NoiseSample that was used.
    """
    if noise_at is None:
        sample = find_quietest_region(input_wav, noise_len)
        print_info(f"Noise profile sampled from the quietest {noise_len:g}s "
                   f"at {sample.start:.2f}s into the window "
                   f"({sample.excess_db:+.1f} dB vs the noise floor).")
        if sample.likely_contains_speech(max_excess_db):
            print_warning(
                f"No clear pause found: the quietest {noise_len:g}s sits "
                f"{sample.excess_db:.1f} dB above the window's noise floor, so the "
                f"'noise' sample probably contains speech and profile mode will remove "
                f"some of it. Point --noise_profile_at at a pause, or use "
                f"--hiss_method patch.")
    else:
        sample = NoiseSample(float(noise_at), float("nan"))
        print_info(f"Noise profile sampled at {sample.start:.2f}s (as requested).")

    noise_wav = os.path.join(temp_dir, "profile_noise.wav")
    joined_wav = os.path.join(temp_dir, "profile_joined.wav")
    filtered_wav = os.path.join(temp_dir, "profile_filtered.wav")

    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-ss", f"{sample.start:.3f}", "-t", f"{noise_len:g}",
         "-i", input_wav, "-acodec", "pcm_f32le", noise_wav],
        noise_len, "Sampling noise", quiet=True)

    padded = (duration + noise_len) if duration else None
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", noise_wav, "-i", input_wav,
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[o]", "-map", "[o]",
         "-acodec", "pcm_f32le", joined_wav],
        padded, "Preparing", quiet=True)

    # Measure across the prepended sample, stopping just short of its end.
    sample_end = max(0.05, noise_len - 0.1)
    denoiser = f"afftdn=nr={noise_reduction:g}:nf={noise_floor:g}:gs={gain_smooth:g}"
    chain = ffmpeg_utils.compensated(
        f"asendcmd=c='0.0 afftdn sn start; {sample_end:g} afftdn sn stop',{denoiser}",
        sample_rate, measure_chain=denoiser)
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", joined_wav, "-filter:a", chain,
         "-acodec", "pcm_f32le", filtered_wav],
        padded, prefix)

    # Drop the sample we prepended.
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", filtered_wav,
         "-af", f"atrim=start_sample={int(round(noise_len * sample_rate))},asetpts=PTS-STARTPTS",
         "-acodec", "pcm_f32le", output_wav],
        duration, "Trimming", quiet=True)

    if duration:
        produced = ffmpeg_utils.probe_duration(output_wav)
        if produced and abs(produced - duration) > 0.25:
            print_warning(f"Hiss stage produced {produced:.2f}s where "
                          f"{duration:.2f}s was expected.")
    return sample
