"""DeepFilterNet denoising, chunked with crossfaded boundaries.

DeepFilterNet is discriminative: it masks the recorded signal rather than
resynthesising speech, so the voices in the output are the original voices.
"""
import os
import shlex
import shutil
import subprocess
import sys

from . import procs
from .audioio import write_wav
from .console import print_info, print_warning
from .progress import ProgressReporter

HAS_DF_API = False
torch = None
try:
    import warnings

    import torch

    with warnings.catch_warnings():
        # DeepFilterNet 0.5.6 still imports the relocated torchaudio.backend.common.
        warnings.filterwarnings("ignore", message=r".*torchaudio\.backend\.common.*")
        from df.enhance import enhance, init_df
        from df.io import load_audio

    HAS_DF_API = True
except (ImportError, OSError):
    # OSError: a broken torch install (missing dylib) fails at import time too.
    pass

SETUP_HINT = (
    "DeepFilterNet is not available in this interpreter.\n"
    "Run ./setup.sh, then either 'source venv/bin/activate' or call "
    "./venv/bin/clean-audio directly."
)


def enhance_chunked(model, df_state, audio, sr, chunk_len_sec=10.0, context_sec=1.0,
                    crossfade_sec=0.5, atten_lim_db=None, prefix="Denoising"):
    """Enhance long audio in chunks, crossfading the joins.

    Each chunk is fed to the model with `context_sec` of extra audio on both
    sides (used as model context, then discarded). Consecutive output regions
    additionally overlap by `crossfade_sec` and are blended, so the joins do not
    produce an audible seam every chunk.
    """
    total = audio.shape[-1]
    if total == 0:
        return audio

    chunk_samples = max(1, int(chunk_len_sec * sr))
    context = max(0, int(context_sec * sr))
    crossfade = min(max(0, int(crossfade_sec * sr)), chunk_samples // 2)

    out = torch.zeros_like(audio)
    num_chunks = (total + chunk_samples - 1) // chunk_samples
    total_sec = total / sr
    reporter = ProgressReporter(prefix, total_sec)

    with torch.no_grad():
        for i in range(num_chunks):
            start = i * chunk_samples
            end = min(total, start + chunk_samples)
            write_start = max(0, start - crossfade) if i > 0 else start

            pad_start = max(0, write_start - context)
            pad_end = min(total, end + context)

            enhanced = enhance(model, df_state, audio[:, pad_start:pad_end],
                               pad=True, atten_lim_db=atten_lim_db)

            available = enhanced.shape[-1]
            slice_start = min(write_start - pad_start, available)
            slice_end = min(slice_start + (end - write_start), available)
            segment = enhanced[:, slice_start:slice_end]
            seg_len = segment.shape[-1]
            if seg_len == 0:
                continue

            fade_len = min(start - write_start, seg_len)
            if fade_len > 0:
                ramp = torch.linspace(0.0, 1.0, fade_len,
                                      device=segment.device, dtype=segment.dtype)
                previous = out[:, write_start:write_start + fade_len]
                out[:, write_start:write_start + fade_len] = (
                    previous * (1.0 - ramp) + segment[:, :fade_len] * ramp
                )
                tail = segment[:, fade_len:]
                if tail.shape[-1]:
                    out[:, start:start + tail.shape[-1]] = tail
            else:
                out[:, write_start:write_start + seg_len] = segment

            reporter.update(min(total_sec, end / sr))

    reporter.finish()
    return out


def _denoise_via_cli(input_wav, output_wav, atten_lim_db, prefix="Denoising"):
    """Fallback to the deepFilter executable when the Python API is unavailable."""
    executable = shutil.which("deepFilter")
    if not executable:
        raise RuntimeError(SETUP_HINT)

    output_dir = os.path.dirname(os.path.abspath(output_wav))
    os.makedirs(output_dir, exist_ok=True)
    cmd = [executable, input_wav, "-o", output_dir, "--no-suffix"]
    if atten_lim_db:
        cmd += ["-a", str(atten_lim_db)]

    print_warning("Using the deepFilter command-line fallback; "
                  "it prints its own progress instead of the standard bar.")
    print_info(f"Running command: {shlex.join(cmd)}")
    proc = procs.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, bufsize=1)
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
    finally:
        if proc.poll() is None:
            procs.terminate(proc)
        else:
            procs.untrack(proc)

    produced = os.path.join(output_dir, os.path.basename(input_wav))
    if os.path.abspath(produced) != os.path.abspath(output_wav):
        os.replace(produced, output_wav)


def denoise_file(input_wav, output_wav, atten_lim_db=None, chunk_len_sec=10.0,
                 context_sec=1.0, crossfade_sec=0.5, prefix="Denoising"):
    if not HAS_DF_API:
        _denoise_via_cli(input_wav, output_wav, atten_lim_db, prefix=prefix)
        return

    model, df_state, _ = init_df(log_level="ERROR", log_file=None)
    sr = df_state.sr()
    audio, _ = load_audio(input_wav, sr=sr)
    enhanced = enhance_chunked(
        model=model,
        df_state=df_state,
        audio=audio,
        sr=sr,
        chunk_len_sec=chunk_len_sec,
        context_sec=context_sec,
        crossfade_sec=crossfade_sec,
        atten_lim_db=atten_lim_db,
        prefix=prefix,
    )
    write_wav(output_wav, enhanced, sr)
