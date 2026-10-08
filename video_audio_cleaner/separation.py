"""Demucs speech/music separation.

Demucs is a masking model, not a generative one: the vocal stem is built from
the original recording, so speaker identity is preserved. Splitting music off
before denoising matters because DeepFilterNet targets noise, not music.
"""
from pathlib import Path

from .audioio import write_wav
from .console import print_info, print_warning
from .progress import ProgressReporter

SETUP_HINT = (
    "Demucs is not installed. Re-run ./setup.sh, or install it directly:\n"
    "    ./venv/bin/pip install -e \".[separation]\""
)

MUSIC_STEMS = ("drums", "bass", "other")


class _SeparationProgress:
    """Turns Demucs' per-segment callback into the shared progress bar.

    Demucs reports `segment_offset` in samples at the model's rate and never
    reports a total, so the total is derived from the known clip duration.
    Segments finish out of order, hence the monotonic clamp.
    """

    def __init__(self, reporter, duration_sec, model_sr=44100):
        self.reporter = reporter
        self.duration_sec = duration_sec
        self.model_sr = model_sr
        self.furthest = 0.0

    def __call__(self, data):
        try:
            if not self.duration_sec or self.duration_sec <= 0:
                return
            total_samples = self.duration_sec * self.model_sr
            if total_samples <= 0:
                return
            models = max(1, int(data.get("models", 1) or 1))
            index = int(data.get("model_idx_in_bag", 0) or 0)
            offset_samples = float(data.get("segment_offset", 0) or 0)
            within = min(1.0, offset_samples / total_samples)
            fraction = max(0.0, min(1.0, (index + within) / models))
            self.furthest = max(self.furthest, fraction)
            self.reporter.update(self.furthest * self.duration_sec)
        except (TypeError, ValueError, ZeroDivisionError):
            pass


def _separate_once(input_wav, device, segment, duration_sec, prefix):
    from demucs.api import Separator

    reporter = ProgressReporter(prefix, duration_sec)
    tracker = _SeparationProgress(reporter, duration_sec)
    kwargs = {
        "model": "htdemucs",
        "device": device,
        "progress": False,
        "callback": tracker,
    }
    if segment:
        kwargs["segment"] = segment

    separator = Separator(**kwargs)
    # The model's rate is only known once the model is loaded, but the callback
    # does not fire until separation starts, so updating it here is in time.
    tracker.model_sr = int(getattr(separator, "samplerate", 44100) or 44100)
    _origin, stems = separator.separate_audio_file(Path(input_wav))
    reporter.finish()
    return separator, stems


def separate_to_files(input_wav, vocals_path, music_path, target_sr,
                      duration_sec=None, device="auto", segment=None,
                      prefix="Separating"):
    """Split input into a speech stem and a music bed, both at target_sr.

    Returns (vocals_path, music_path).
    """
    try:
        import torch
        import torchaudio
        import demucs.api  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(f"{SETUP_HINT}\n(import error: {exc})") from exc

    if device == "auto":
        devices = ["mps", "cpu"] if torch.backends.mps.is_available() else ["cpu"]
    else:
        devices = [device]

    separator = None
    stems = None
    for index, dev in enumerate(devices):
        is_last = index == len(devices) - 1
        try:
            print_info(f"Separating with Demucs (htdemucs) on device: {dev}")
            separator, stems = _separate_once(input_wav, dev, segment, duration_sec, prefix)
            break
        except ValueError as exc:
            # htdemucs rejects segments longer than its training segment.
            if segment and "segment" in str(exc).lower():
                print_warning(f"Segment length {segment}s rejected by the model "
                              f"({exc}); retrying with the model default.")
                separator, stems = _separate_once(input_wav, dev, None, duration_sec, prefix)
                break
            if is_last:
                raise
            print_warning(f"Separation on '{dev}' failed ({exc}); falling back to CPU.")
        except (RuntimeError, NotImplementedError, TypeError) as exc:
            if is_last:
                raise
            print_warning(f"Separation on '{dev}' failed ({exc}); falling back to CPU.")

    if stems is None:
        raise RuntimeError("Demucs separation produced no stems.")

    model_sr = int(getattr(separator, "samplerate", 44100) or 44100)

    vocals = stems["vocals"]
    music = None
    for name in MUSIC_STEMS:
        if name not in stems:
            continue
        music = stems[name].clone() if music is None else music + stems[name]
    if music is None:
        music = torch.zeros_like(vocals)

    if model_sr != target_sr:
        vocals = torchaudio.functional.resample(vocals, model_sr, target_sr)
        music = torchaudio.functional.resample(music, model_sr, target_sr)

    write_wav(vocals_path, vocals, target_sr)
    write_wav(music_path, music, target_sr)
    return vocals_path, music_path
