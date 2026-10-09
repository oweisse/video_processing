"""ffmpeg/ffprobe helpers: probing, progress-reporting runs, and loudness analysis."""
import json
import re
import shlex
import subprocess
import tempfile

from . import procs
from .console import print_info
from .progress import ProgressReporter

# Print each ffmpeg command line before running it (--verbose). Off by default:
# a single render graph is several hundred characters and drowns the progress.
VERBOSE = False


def _run_capture(cmd, timeout=120):
    """Run a short-lived command and capture its output as text."""
    proc = procs.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            procs.terminate(proc)
        else:
            procs.untrack(proc)
    return proc.returncode, out, err


def probe_duration(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        rc, out, _ = _run_capture(cmd)
        if rc == 0:
            return float(out.strip())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def has_video_stream(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        rc, out, _ = _run_capture(cmd)
        return rc == 0 and "video" in out
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe_keyframe_at_or_before(path, start_sec, lookbacks=(15.0, 60.0, 240.0)):
    """Presentation time of the last video keyframe at or before start_sec.

    Stream-copy seeking always lands on a keyframe, so cutting audio at an
    arbitrary timestamp while copying video desynchronises the two by up to one
    GOP. Returns None when no keyframe can be found (caller falls back).
    """
    if start_sec is None:
        return None
    if start_sec <= 0:
        return 0.0

    for lookback in lookbacks:
        window_start = max(0.0, start_sec - lookback)
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,flags",
            "-of", "csv=print_section=0",
            "-read_intervals", f"{window_start:.3f}%{start_sec + 0.1:.3f}",
            path,
        ]
        try:
            rc, out, _ = _run_capture(cmd)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if rc != 0:
            return None

        best = None
        for line in out.splitlines():
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                ts = float(parts[0])
            except ValueError:
                continue
            if "K" not in parts[1]:
                continue
            if ts <= start_sec + 1e-6 and (best is None or ts > best):
                best = ts
        if best is not None:
            return best
        if window_start <= 0.0:
            break
    return None


def format_speed(raw):
    """ffmpeg's speed field ('31.7x', '1.68e+03x', 'N/A') as 'N sec/sec', or None."""
    raw = raw.strip()
    if not raw.endswith("x"):
        return None
    try:
        value = float(raw[:-1])
    except ValueError:
        return None
    return f"{value:.0f} sec/sec" if value >= 100 else f"{value:.1f} sec/sec"


def run_ffmpeg(cmd_args, duration, prefix, quiet=False):
    """Run ffmpeg, rendering the shared progress bar. Returns captured stderr."""
    cmd = list(cmd_args) + ["-progress", "-", "-nostats"]
    if VERBOSE and not quiet:
        print_info(f"Running command: {shlex.join(cmd)}")

    reporter = ProgressReporter(prefix, duration)
    # stderr goes to a file rather than a pipe: we only drain stdout during the
    # run, and a full stderr pipe buffer would deadlock ffmpeg.
    stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    proc = procs.popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file, text=True)
    speed = None

    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                raw = line.split("=", 1)[1]
                try:
                    reporter.update(int(raw) / 1_000_000.0, speed)
                except ValueError:
                    pass
            elif line.startswith("speed="):
                speed = format_speed(line.split("=", 1)[1]) or speed
        proc.wait()
        stderr_file.seek(0)
        stderr_text = stderr_file.read()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=stderr_text)
        reporter.finish(speed)
        return stderr_text
    finally:
        if proc.poll() is None:
            procs.terminate(proc)
        else:
            procs.untrack(proc)
        stderr_file.close()


def _parse_loudnorm_json(stderr_text):
    """The loudnorm stats block: the last {...} in stderr that holds input_i."""
    for match in reversed(list(re.finditer(r"\{[^{}]*\}", stderr_text, re.S))):
        if '"input_i"' not in match.group(0):
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None


def _finite(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    return num == num and abs(num) != float("inf")


def loudnorm_stats(stderr_text):
    """Validated loudnorm measurement from an analysis run's stderr, or None."""
    stats = _parse_loudnorm_json(stderr_text)
    if not stats:
        return None
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(_finite(stats.get(key)) for key in required):
        # Silent or near-silent input; linear normalisation is not possible.
        return None
    return stats


# --------------------------------------------------------------------------
# Latency compensation
# --------------------------------------------------------------------------
# afftdn, anlmdn and alimiter all look ahead, and ffmpeg does not compensate:
# their output is shifted later by a fixed number of samples (measured on
# ffmpeg 8.1: afftdn 1200, anlmdn patch+search, alimiter 239 at 48 kHz). The
# merge copies video untouched, so any shift becomes lip-sync error. The delay
# depends on filter parameters and ffmpeg version, so it is measured with an
# impulse rather than hard-coded; graph.py removes it in place.
_latency_cache = {}


def filter_latency(chain, sample_rate):
    """Samples by which `chain` delays its input. Measured once per chain."""
    key = (chain, sample_rate)
    if key in _latency_cache:
        return _latency_cache[key]

    import numpy as np
    import soundfile as sf

    with tempfile.TemporaryDirectory(prefix="vac_latency_") as tmp:
        src = f"{tmp}/impulse.wav"
        dst = f"{tmp}/out.wav"
        signal = np.zeros(3 * sample_rate, dtype=np.float32)
        signal[sample_rate] = 0.5
        sf.write(src, signal, sample_rate, subtype="FLOAT")
        rc, _, err = _run_capture(
            ["ffmpeg", "-y", "-v", "error", "-i", src, "-af", chain,
             "-ac", "1", "-ar", str(sample_rate), "-acodec", "pcm_f32le", dst])
        if rc != 0:
            raise RuntimeError(f"Could not measure latency of '{chain}':\n{err.strip()}")
        out, _ = sf.read(dst, dtype="float32")

    peak = int(np.argmax(np.abs(out)))
    latency = peak - sample_rate
    if np.abs(out[peak]) < 1e-4 or not 0 <= latency < sample_rate:
        latency = 0  # the impulse did not survive; assume no shift rather than guess
    _latency_cache[key] = latency
    return latency
