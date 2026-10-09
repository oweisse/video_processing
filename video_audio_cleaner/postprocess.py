"""Runs the post-DeepFilterNet graph (see graph.py): one analysis pass, one render pass."""
import re
from dataclasses import dataclass

from . import ffmpeg_utils, graph


@dataclass
class RenderResult:
    lufs: float | None        # integrated loudness of the final audio
    true_peak: float | None   # dBTP of the final audio


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
    return RenderResult(*parse_ebur128_summary(stderr))


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
