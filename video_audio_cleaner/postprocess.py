"""Runs the post-DeepFilterNet graph (see graph.py): one analysis pass, one render pass."""
import math
import re
from dataclasses import dataclass

from . import ffmpeg_utils, graph


@dataclass
class RenderResult:
    lufs: float | None          # integrated loudness of the final audio
    true_peak: float | None     # dBTP of the final audio
    loudnorm_mode: str | None = None   # "linear" or "dynamic", as loudnorm reports it


def measure_latencies(o):
    """Look-ahead delay of every filter the render pass will use, in samples."""
    sr = o.sample_rate
    lat = {}
    if o.hiss_method:
        lat["hiss"] = ffmpeg_utils.filter_latency(graph.hiss_filter(o), sr)
    if o.audio_gain or o.music_level_db is not None:
        lat["limiter"] = ffmpeg_utils.filter_latency(graph.limiter_filter(o), sr)
    if o.declick:
        lat["declick"] = ffmpeg_utils.filter_latency(graph.declick_filter(o), sr)
    return lat


def analyse(o, denoised_wav, duration, label):
    """Two-pass loudnorm, pass one: measure the hiss-reduced signal."""
    cmd = (["ffmpeg", "-y", *graph.inputs(o, denoised_wav, mode="analysis"),
            "-filter_complex", graph.build(o, "analysis"), "-map", "[out]",
            "-f", "null", "-"])
    stderr = ffmpeg_utils.run_ffmpeg(cmd, duration, label)
    return ffmpeg_utils.loudnorm_stats(stderr)


def render(o, denoised_wav, music_wav, out_wav, duration, label):
    cmd = (["ffmpeg", "-y", *graph.inputs(o, denoised_wav, music_wav),
            "-filter_complex", graph.build(o, "render"), "-map", "[out]",
            "-acodec", "pcm_f32le", "-ar", str(o.sample_rate), out_wav])
    stderr = ffmpeg_utils.run_ffmpeg(cmd, duration, label)
    mode = None
    if o.normalize:
        stats = ffmpeg_utils._parse_loudnorm_json(stderr) or {}
        mode = stats.get("normalization_type")
    return RenderResult(*parse_ebur128_summary(stderr), loudnorm_mode=mode)


_NUMBER = r"(-?\d+(?:\.\d+)?|-inf)"


def parse_ebur128_summary(stderr_text):
    """(integrated LUFS, true peak dBTP) from ebur128's closing Summary block."""
    at = stderr_text.rfind("Summary:")
    if at < 0:
        return None, None
    tail = stderr_text[at:]
    i = re.search(rf"\bI:\s+{_NUMBER}\s+LUFS", tail)
    p = re.search(rf"\bPeak:\s+{_NUMBER}\s+dBFS", tail)

    def value(match):
        if not match or match.group(1) == "-inf":
            return None
        return float(match.group(1))

    return value(i), value(p)


# True-peak estimates differ slightly between loudnorm's analysis and its output;
# keep this much headroom in hand so the chosen target really stays linear.
AUTO_TARGET_MARGIN_DB = 0.5
LOUDNORM_MAX_LRA = 50.0


@dataclass
class AutoTarget:
    target_lufs: float
    target_lra: float
    linear: bool          # False when even the floor needs compression
    reachable_lufs: float  # loudest plain-volume target before the floor/cap applied


def auto_loudness_target(measured, max_lufs, min_lufs, target_tp, target_lra):
    """Loudest target reachable with a plain volume change, within [min_lufs, max_lufs].

    loudnorm compresses (dynamic mode) when the gain to its target would push the
    true peak over target_tp, or when the input's loudness range exceeds the target
    range. Both are avoided: the target stops where the peaks reach the ceiling,
    and the range target is raised to the input's own range.
    """
    in_i = float(measured["input_i"])
    in_tp = float(measured["input_tp"])
    in_lra = float(measured["input_lra"])
    reachable = in_i + (target_tp - in_tp) - AUTO_TARGET_MARGIN_DB
    target = min(max_lufs, math.floor(reachable * 2) / 2)       # 0.5 dB steps, downwards
    lra = min(LOUDNORM_MAX_LRA, max(target_lra, math.ceil(in_lra * 10 + 1) / 10))
    return AutoTarget(max(target, min_lufs), lra, target >= min_lufs, reachable)
