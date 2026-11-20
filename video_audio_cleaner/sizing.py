"""Export to a size budget (docs/stitch-and-size-budget-plan.md §10).

Stage A (estimate): a few short samples of the timeline are rendered losslessly through
the export's own picture graph, encoded at the quality ceiling and, when that is too big
for the budget, at the constant-quality setting whose bitrate matches the budget. The
samples give the mode, the predicted size, a quality score (VMAF, else SSIM) and the time
the export will take. Stage B is the one real encode: a copy, one pass at the ceiling
quality, or two passes at the capped bitrate. The whole film is never encoded twice for
sizing; only the samples are.
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import events, export as E, ffmpeg_utils, procs
from .console import print_info, print_step, print_success, print_warning

GB = 1e9
CONTAINER = 0.985            # share of the budget left after the MP4 container
MIN_VIDEO_BPS = 150_000
MAX_CRF = 40.0
EXTRA_SAMPLES = 2            # samples for long clips that the parts missed
WORKERS = 2                  # samples tried at once (x265 on 2-3 s leaves cores idle)
SCORE_HEIGHT = 1080          # quality is scored at most at this height
PASS1_SHARE = 1.0            # the analysis pass takes about as long as the encode
MISS = 1.25                  # a quality trial this far over the budget is not used for the words
OVER_BUDGET_WARN = 0.02
HEIGHTS = (2160, 1440, 1080, 720)
MUX_OVERHEAD = 1.004         # measured MP4 overhead on top of the streams' bits
RENDER_SHARE = 0.5           # part of a sample's reference render the real encode repeats

# (VMAF floor, SSIM floor, words), best first; below the last: "Soft".
WORDS = ((93.0, 0.98, "Excellent: hard to tell from the original"),
         (85.0, 0.96, "Good"),
         (75.0, 0.93, "Fair: fine detail softens"))
SOFT = "Soft"
WEAK_GAP = {"vmaf": 10.0, "ssim": 0.02}       # "one part is noticeably weaker"
SUGGEST_GAIN = {"vmaf": 2.0, "ssim": 0.005}   # a smaller frame must score this much higher


class BudgetError(E.ProjectError):
    pass


# ------------------------------------------------------------------ parsing
_UNITS = {"": GB, "g": GB, "gb": GB, "m": 1e6, "mb": 1e6, "t": 1e12, "tb": 1e12}


def _amount(text):
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*", text)
    if not m or m.group(2).lower() not in _UNITS:
        raise ValueError(f"not a size: {text!r} (try 2GB or 900MB)")
    return float(m.group(1)) * _UNITS[m.group(2).lower()]


def parse_budget(text):
    """'2GB/h', '2 GB/hour', '1500MB/h' → {"per_hour_gb": 2.0}."""
    m = re.fullmatch(r"(.+?)\s*(?:/|per)\s*(h|hr|hour)\s*", str(text), re.I)
    if not m:
        raise ValueError(f"not a budget per hour: {text!r} (try 2GB/h)")
    value = _amount(m.group(1)) / GB
    if value <= 0:
        raise ValueError("the budget must be more than 0")
    return {"per_hour_gb": round(value, 6)}


def parse_size(text):
    """'2.5GB', '900MB' → {"total_gb": 2.5}."""
    value = _amount(str(text)) / GB
    if value <= 0:
        raise ValueError("the size must be more than 0")
    return {"total_gb": round(value, 6)}


def bitrate(text):
    """'192k' / '2M' / 192000 → bits per second."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([kKmM]?)\s*", str(text))
    if not m:
        raise ValueError(f"not a bitrate: {text!r}")
    return float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]


def with_overrides(s, overrides=None):
    """Export settings with the command line's values on top. The budget, when any, comes
    out as {"per_hour_gb"} or {"total_gb"}; a non-positive or empty one is no budget."""
    s = dict(s)
    for key, value in (overrides or {}).items():
        if value is not None:
            s[key] = value
    budget = s.get("budget") or None
    if budget and not any(float(budget.get(k) or 0) > 0 for k in ("per_hour_gb", "total_gb")):
        budget = None
    s["budget"] = budget
    if budget and not s.get("codec"):
        s["codec"] = "hevc"         # smaller at the same look; plain exports stay H.264
    if s.get("codec") not in (None, "h264", "hevc"):
        raise E.ProjectError(f"unknown codec {s['codec']!r} (h264 or hevc)")
    return s


# ------------------------------------------------------------------ arithmetic (§10.2)
def budget_bytes(budget, seconds):
    if budget.get("total_gb"):
        return float(budget["total_gb"]) * GB
    return float(budget["per_hour_gb"]) * GB * seconds / 3600


def video_budget_bps(total_bytes, seconds, audio_bps):
    bps = total_bytes * 8 * CONTAINER / seconds - audio_bps
    if bps < MIN_VIDEO_BPS:
        raise BudgetError("the budget is too small for this length")
    return bps


def output_canvas(cv, height=None):
    """The canvas scaled down to `height` lines (even sizes), never up (§10.6)."""
    if not height or int(height) >= cv["height"]:
        return cv
    h = max(2, int(height) // 2 * 2)
    w = max(2, int(round(cv["width"] * h / cv["height"] / 2)) * 2)
    return {**cv, "width": w, "height": h}


def codec_lib(s):
    return "libx265" if s.get("codec") == "hevc" else "libx264"


def ceiling_crf(s):
    return E.CEILING_CRF[codec_lib(s)]


# ------------------------------------------------------------------ source ceiling (§10.3)
def source_bps_of(src):
    """A source's video bitrate: the probe's, else the file's average minus its sound."""
    probe = src.get("probe") or {}
    video = probe.get("video") or {}
    if video.get("bit_rate"):
        return float(video["bit_rate"])
    size = probe.get("size") or (os.path.getsize(src["path"]) if os.path.exists(src["path"]) else None)
    if size and probe.get("duration_sec"):
        audio = float((probe.get("audio") or {}).get("bit_rate") or 0)
        return max(0.0, size * 8 / float(probe["duration_sec"]) - audio)
    return None


def source_rate(project, cv):
    """(length-weighted video bitrate of the clips as the sources hold them, scaled up for
    clips smaller than the canvas; the same without the scaling, for a copy)."""
    total = scaled = plain = 0.0
    for c, _, length in E.clips(project):
        src = E.source_of(project, c)
        bps = source_bps_of(src)
        if bps is None:
            continue
        video = (src.get("probe") or {}).get("video") or {}
        pixels = E.display_width(video) * int(video["height"]) if video.get("width") else None
        grow = max(1.0, cv["width"] * cv["height"] / pixels) if pixels else 1.0
        total += length
        scaled += length * bps * grow
        plain += length * bps
    if not total:
        return None, None
    return scaled / total, plain / total


# ------------------------------------------------------------------ samples (§10.4.1)
def sample_shape(seconds, height, length=None, count=None):
    """(sample length, sample count) at an output height: few and short, so an estimate
    takes well under a minute (4K: 6 × 2 s; else 10 × 3 s)."""
    big = height > 1080
    return float(length or (2.0 if big else 3.0)), int(count or (6 if big else 10))


def pick_samples(seconds, spans, L, n, extra_min=60.0, most=16):
    """Where to sample: [{"t0", "t1", "at_sec", "weight"}], weights summing to `seconds`.

    spans: [(owned_from, owned_to)] per clip. n equal parts, L seconds from the middle of
    each; then one in the middle of every clip of at least `extra_min` s that has none (up
    to `most` in all), which takes over the timeline time of its clip from the parts.
    A timeline shorter than 2 × n × L is one sample: the whole of it.
    """
    if seconds < 2 * n * L:
        return [{"t0": 0.0, "t1": seconds, "at_sec": seconds / 2, "weight": seconds}]
    part = seconds / n
    out = []
    for k in range(n):
        mid = (k + 0.5) * part
        out.append({"t0": mid - L / 2, "t1": mid + L / 2, "at_sec": mid,
                    "weight": part, "part": (k * part, (k + 1) * part)})
    owned = []
    for a, b in spans:
        if len(out) >= most:
            break
        if b - a < extra_min or any(a <= s["at_sec"] < b for s in out):
            continue
        mid = (a + b) / 2
        out.append({"t0": mid - L / 2, "t1": mid + L / 2, "at_sec": mid, "weight": b - a})
        owned.append((a, b))
    for s in out:
        if "part" in s:
            lo, hi = s.pop("part")
            s["weight"] = (hi - lo) - sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in owned)
    out = [s for s in out if s["weight"] > 1e-6]
    out.sort(key=lambda s: s["t0"])
    for s in out:
        s.update({k: round(s[k], 6) for k in ("t0", "t1", "at_sec", "weight")})
    return out


# ------------------------------------------------------------------ the CRF for a bitrate (§10.4.4)
def crf_for(cap_bps, ceiling, top_bps, q1=None, b1=None, max_crf=MAX_CRF):
    """The CRF whose bitrate is cap_bps, on a straight line through (CRF, log bitrate): the
    ceiling's point and, when given, a second measured one (q1, b1); without it the
    bitrate is taken to halve every 6 CRF. Clamped to [ceiling, max_crf]."""
    slope = -math.log(2) / 6
    if q1 is not None and b1 and abs(q1 - ceiling) > 0.05:
        measured = (math.log(b1) - math.log(top_bps)) / (q1 - ceiling)
        if measured < -1e-3:
            slope = measured
    q = ceiling + (math.log(cap_bps) - math.log(top_bps)) / slope
    return round(min(max_crf, max(ceiling, q)), 2)


# ------------------------------------------------------------------ quality words (§10.4.6)
_filters = None


def has_vmaf(filters_text=None):
    """Whether this ffmpeg has libvmaf (`ffmpeg -filters`, asked once)."""
    global _filters
    if filters_text is None:
        if _filters is None:
            try:
                _filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                          capture_output=True, text=True, timeout=30).stdout
            except (OSError, subprocess.TimeoutExpired):
                _filters = ""
        filters_text = _filters
    return re.search(r"\blibvmaf\b", filters_text) is not None


def words(metric, mean, low=None, low_at=None):
    i = 0 if metric == "vmaf" else 1
    text = next((w[2] for w in WORDS if mean is not None and mean >= w[i]), SOFT)
    if low is not None and mean is not None and mean - low > WEAK_GAP[metric]:
        text += f"; one part is noticeably weaker (at {E._clock(low_at or 0)})"
    return text


def _good(metric, mean):
    return mean is not None and mean >= WORDS[1][0 if metric == "vmaf" else 1]


def parse_quality(metric, stderr):
    if metric == "vmaf":
        m = re.findall(r"VMAF score:\s*([0-9.]+)", stderr)
    else:
        m = re.findall(r"SSIM .*All:([0-9.]+)", stderr)
    if not m:
        raise RuntimeError(f"no {metric.upper()} score in ffmpeg's output")
    return float(m[-1])


# ------------------------------------------------------------------ running ffmpeg quietly
def _run(cmd):
    """Run ffmpeg to the end (tracked, so a cancel kills it); returns (stderr, seconds)."""
    began = time.time()
    proc = procs.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        _, err = proc.communicate()
    finally:
        if proc.poll() is None:
            procs.terminate(proc)
        else:
            procs.untrack(proc)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=err)
    return err, time.time() - began


FF = ["ffmpeg", "-y", "-hide_banner", "-nostats"]


class Sampler:
    """The samples' lossless references (kept for the whole estimate) and trial encodes,
    in a work dir. Safe to use from two threads at once (one sample each)."""

    def __init__(self, project, s, samples, work, metric):
        self.project, self.s, self.samples, self.work = project, s, samples, Path(work)
        self.metric = metric
        self.refs = {}             # sample index → path
        self.render_sec = {}       # sample index → seconds its reference took
        self.n = 0
        self._lock = threading.Lock()

    def length(self, i):
        sm = self.samples[i]
        return sm["t1"] - sm["t0"]

    def reference(self, i, cv):
        if i in self.refs:
            return self.refs[i]
        sm = self.samples[i]
        ex = E.excerpt(self.project, sm["t0"], sm["t1"])
        inputs = E.Inputs()
        chains, _ = E.video_graph(ex, inputs, cv)
        path = self.work / f"ref{i}.mkv"
        _, sec = _run([*FF, "-v", "error", *inputs.args, "-filter_complex", ";".join(chains),
                       "-map", "[video]", "-an", "-c:v", "libx264", "-qp", "0",
                       "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                       "-t", f"{self.length(i):.6f}", str(path)])
        with self._lock:
            self.render_sec[i] = sec
            self.refs[i] = path
        return path

    def encode(self, i, ref, cv, rate, name, scale=None):
        """A trial encode of sample i's reference (scaled to `scale`, a canvas, when given):
        (path, bits per second)."""
        with self._lock:
            self.n += 1
        out = self.work / name
        vf = ["-vf", f"scale={scale['width']}:{scale['height']}:flags=bicubic"] if scale else []
        _run([*FF, "-v", "error", "-i", str(ref), "-map", "0:v:0", *vf,
              *E.encoder_args(self.s, scale or cv, rate), "-an", str(out)])
        return out, out.stat().st_size * 8 / self.length(i)

    def quality(self, trial, ref, size):
        """The trial's score against its reference, both seen at `size` (w, h): at most
        1080 lines, which is what the default VMAF model is made for and twice as fast."""
        fit = f"scale={size[0]}:{size[1]}:flags=bicubic,"
        cmp = "libvmaf=n_threads=8:n_subsample=3" if self.metric == "vmaf" else "ssim"
        graph = (f"[0:v]{fit}setpts=PTS-STARTPTS[d];[1:v]{fit}setpts=PTS-STARTPTS[r];"
                 f"[d][r]{cmp}")
        err, _ = _run([*FF, "-v", "info", "-i", str(trial), "-i", str(ref),
                       "-lavfi", graph, "-f", "null", "-"])
        return parse_quality(self.metric, err)

    def clear(self):
        for path in self.work.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
        self.refs.clear()


def score_size(cv):
    if cv["height"] <= SCORE_HEIGHT:
        return cv["width"], cv["height"]
    return int(round(cv["width"] * SCORE_HEIGHT / cv["height"] / 2)) * 2, SCORE_HEIGHT


def each(n, fn, workers=None):
    """fn(i) for i in range(n), `workers` at a time (results in order). On any failure or
    a cancel, the running ffmpegs are stopped before it returns."""
    workers = workers or WORKERS
    if workers <= 1:
        return [fn(i) for i in range(n)]
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(fn, i) for i in range(n)]
        try:
            return [f.result() for f in futures]
        except BaseException:
            for f in futures:
                f.cancel()
            procs.terminate_all()
            raise


def _weighted(samples, values):
    total = sum(s["weight"] for s in samples)
    return sum(s["weight"] * v for s, v in zip(samples, values)) / total


# ------------------------------------------------------------------ stage A
def estimate_key(project, s, reencode=False, sample_len=None, sample_count=None):
    """What an estimate depends on, hashed: the picture, the sources, the output settings
    and the budget."""
    cs = E.clips(project)
    seconds = E.duration(project)
    cv = output_canvas(E.canvas(project), s.get("height"))
    ids = sorted({c["source"] for c, _, _ in cs})
    what = {
        "clips": [[c["source"], float(c["in_sec"]), float(c["out_sec"]), E.transition(c),
                   E.framing(c), c.get("video_filters") or [], c.get("zoom_glide")]
                  for c, _, _ in cs],
        "sources": {i: ((project["sources"][i].get("probe") or {}).get("id")
                        or project["sources"][i]["path"]) for i in ids},
        "canvas": cv, "codec": s.get("codec") or "h264", "height": s.get("height"),
        "fast": bool(s.get("fast")), "preset": s.get("preset"),
        "audio_bitrate": str(s.get("audio_bitrate")),
        "budget_bytes": round(budget_bytes(s["budget"], seconds)), "reencode": bool(reencode),
        "samples": [sample_len, sample_count]}
    return hashlib.sha1(json.dumps(what, sort_keys=True, default=str).encode()).hexdigest()


def _gb_per_hour(bps):
    return bps / 8 * 3600 / GB


def size_words(n):
    """Bytes as people say them: 2.67 GB, 930 MB."""
    return f"{n / GB:.2f} GB" if n >= GB else f"{n / 1e6:.0f} MB"


def _budget_words(budget, total_bytes):
    if budget.get("total_gb"):
        return size_words(total_bytes)
    return f"{float(budget['per_hour_gb']):g} GB per hour"


def estimate(project, s, copy_allowed, work, reencode=False, sample_len=None,
             sample_count=None, step=None, metric=None):
    """Stage A. Returns the estimate dict (§10.4.7).

    copy_allowed: plan() allows a lossless copy (and nothing forces an encode).
    step(title): progress, called before each sample ("Trying sample 3 of 8").
    """
    step = step or (lambda title: None)
    began = time.time()
    seconds = E.duration(project)
    cv0 = E.canvas(project)
    cv = output_canvas(cv0, s.get("height"))
    audio_bps = bitrate(s["audio_bitrate"])
    total_bytes = budget_bytes(s["budget"], seconds)
    budget_bps = video_budget_bps(total_bytes, seconds, audio_bps)
    src_scaled, src_plain = source_rate(project, cv)
    cap_bps = min(budget_bps, src_scaled) if src_scaled else budget_bps
    cap = "source" if src_scaled and src_scaled < budget_bps else "budget"
    est = {"key": estimate_key(project, s, reencode, sample_len, sample_count),
           "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": None, "budget_bytes": round(total_bytes), "predicted_bytes": None,
           "video_bps": None, "source_bps": round(src_plain) if src_plain else None,
           "cap": cap, "codec": s.get("codec") or "h264", "height": cv["height"],
           "width": cv["width"], "duration_sec": round(seconds, 3), "crf": None,
           "crf_equivalent": None, "target_bps": None, "passes": 0, "metric": None, "quality_mean": None, "quality_low": None,
           "quality_low_at_sec": None, "words": None, "encode_minutes": None, "samples": [],
           "suggest_height": None, "notes": []}
    bw = _budget_words(s["budget"], total_bytes)
    est["audio"] = {"codec": "aac", "bps": round(audio_bps), "channels": 2,
                    "sample_rate": E.SAMPLE_RATE}
    est["footage"] = footage(project)
    if est["footage"] and est["footage"]["height"] < cv["height"]:
        f = est["footage"]
        est["notes"].append(f"Most of the footage ({E._clock(f['seconds'])} of "
                            f"{E._clock(seconds)}) is {f['height']} lines high; the export is "
                            f"{cv['height']}, so that part is enlarged.")

    # A lossless copy that fits needs no samples (§10.3).
    if copy_allowed and src_plain and cv == cv0:
        copy_bytes = (src_plain + audio_bps) * seconds / 8 * MUX_OVERHEAD
        if copy_bytes <= total_bytes:
            per_hour = _gb_per_hour(src_plain + audio_bps)
            est.update(mode="copy", predicted_bytes=round(copy_bytes), video_bps=round(src_plain),
                       words="The original picture, unchanged", encode_minutes=0)
            est["notes"].append(f"The videos are already about {per_hour:.1f} GB per hour, "
                                f"under your {bw}: joined without re-encoding.")
            return est
        est["notes"].append(f"Joining without re-encoding would make "
                            f"{size_words(copy_bytes)}, over your {bw}.")

    if metric is None:
        metric = "vmaf" if has_vmaf() else "ssim"
    est["metric"] = metric
    L, n = sample_shape(seconds, cv["height"], sample_len, sample_count)
    spans = [(a, b) for _, _, a, b in E.owned_spans(project)]
    samples = pick_samples(seconds, spans, L, n, most=n + EXTRA_SAMPLES)
    sampler = Sampler(project, s, samples, work, metric)
    size = score_size(cv)
    count = len(samples)
    print_info(f"{count} sample(s) of {L:g} s.")

    def trials(rate, label, scored, small=None):
        """Encode every sample at `rate` (scoring it when `scored`); returns the round."""
        done = []
        lock = threading.Lock()
        tag = f"t{sampler.n}"

        def one(i):
            with lock:
                done.append(i)
                k = len(done)
            step(f"Trying sample {k} of {count} ({label})")
            ref = sampler.reference(i, cv)
            path, bps = sampler.encode(i, ref, cv, rate, f"{tag}_{i}.mp4", small)
            return bps, (sampler.quality(path, ref, size) if scored else None), path
        began = time.time()
        rows = each(count, one)
        r = {"rate": rate, "bps": [b for b, _, _ in rows], "trials": [t for _, _, t in rows],
             "quality": [q for _, q, _ in rows] if scored else [], "sec": time.time() - began}
        r["weighted"] = _weighted(samples, r["bps"])
        return r

    def score(r):
        if not r["quality"]:
            def one(i):
                step(f"Scoring sample {i + 1} of {count}")
                return sampler.quality(r["trials"][i], sampler.reference(i, cv), size)
            r["quality"] = each(count, one)
        return r["quality"]

    ceiling = ceiling_crf(s)
    if s.get("fast"):
        # The hardware encoder has no constant quality: the samples are measured at the
        # capped bitrate directly (§10.5).
        chosen = trials({"bps": round(cap_bps), "hardware": True}, "hardware", True)
        mode = "size"
        est.update(video_bps=round(chosen["weighted"]), target_bps=round(cap_bps), passes=1)
    else:
        top = trials({"crf": ceiling}, "top quality", False)
        if top["weighted"] <= cap_bps:
            mode, chosen = "quality", top
            est.update(crf=ceiling, video_bps=round(top["weighted"]), passes=1)
        else:
            # One more round at the CRF the line predicts; the two points then give the
            # CRF that matches the budget (what --one_pass uses; two passes hit it exactly).
            mode = "size"
            q1 = crf_for(cap_bps, ceiling, top["weighted"])
            chosen = trials({"crf": q1}, f"quality {q1:g}", True)
            q = crf_for(cap_bps, ceiling, top["weighted"], q1, chosen["weighted"])
            if chosen["weighted"] > cap_bps * MISS:
                # Even the lowest quality setting is over the budget (a starved picture):
                # score what the bitrate itself gives instead.
                chosen = trials({"bps": round(cap_bps)}, "at the budget", True)
            est.update(crf_equivalent=q, video_bps=round(cap_bps), target_bps=round(cap_bps),
                       passes=2)
    est["mode"] = mode
    qs = score(chosen)
    mean = _weighted(samples, qs)
    low_i = min(range(count), key=lambda i: qs[i])
    digits = 4 if metric == "ssim" else 2
    est.update(quality_mean=round(mean, digits), quality_low=round(qs[low_i], digits),
               quality_low_at_sec=round(samples[low_i]["at_sec"], 1),
               quality_low_video=video_at(project, samples[low_i]["at_sec"]),
               words=words(metric, mean, qs[low_i], samples[low_i]["at_sec"]))
    est["samples"] = [{"at_sec": sm["at_sec"], "weight": sm["weight"],
                       "bps": round(b), "quality": round(v, 4)}
                      for sm, b, v in zip(samples, chosen["bps"], qs)]

    # Time: the chosen round's encoding speed scaled to the timeline, part of the picture
    # graph's cost (the references' render), and the analysis pass when there are two
    # (measured on ffmpeg 8.1: about as long as the encode).
    sampled = sum(sampler.length(i) for i in range(count))
    enc = chosen["sec"] / sampled
    graph = RENDER_SHARE * sum(sampler.render_sec.values()) / sampled / WORKERS
    passes = est.get("passes") or 1
    est["encode_minutes"] = round(seconds * (enc + graph) * (1 + PASS1_SHARE * (passes - 1)) / 60, 1)
    est["predicted_bytes"] = round((est["video_bps"] + audio_bps) * seconds / 8 * MUX_OVERHEAD)
    if cap == "source":
        est["notes"].append(f"The videos are {_gb_per_hour(src_plain + audio_bps):.1f} GB per "
                            f"hour; the budget allows {_gb_per_hour(budget_bps + audio_bps):.1f}.")
    if mode == "quality":
        est["notes"].append("Encoded at top quality: smaller than your budget.")
    elif cap == "source":
        est["notes"].append(f"Encoded at the originals' bitrate ({cap_bps / 1e6:.1f} Mbit/s): "
                            f"more would not add quality.")
    else:
        est["notes"].append(f"Encoded to fit {size_words(total_bytes)}.")

    # A smaller frame may look better than a starved big one (§10.6): one round at a
    # smaller height (1080 for anything taller, else 720), encoded from the same references
    # scaled down, at the same bitrate, scored the same way.
    smaller = 1080 if cv["height"] > 1080 else 720 if cv["height"] > 720 else None
    if mode == "size" and not s.get("fast") and not _good(metric, mean) and smaller:
        small = output_canvas(cv0, smaller)
        r = trials({"bps": round(cap_bps)}, _name(smaller), True, small)
        small_mean = _weighted(samples, r["quality"])
        est["suggest_quality"] = round(small_mean, digits)
        if small_mean >= mean + SUGGEST_GAIN[metric]:
            est["suggest_height"] = smaller
            est["notes"].append(f"At this size {_name(smaller)} would look better than "
                                f"{_name(cv['height'])} ({metric.upper()} {small_mean:.0f} "
                                f"instead of {mean:.0f}).")
    sampler.clear()
    est["trials"] = sampler.n
    est["estimate_sec"] = round(time.time() - began, 1)
    return est


def footage(project):
    """The height carrying the most timeline time: {"height", "seconds"}, or None."""
    by_height = {}
    for item in E.canvas_items(project):
        by_height[item["height"]] = by_height.get(item["height"], 0.0) + item["seconds"]
    if not by_height:
        return None
    h, sec = max(by_height.items(), key=lambda kv: kv[1])
    return {"height": int(h), "seconds": round(sec, 1)}


def video_at(project, t):
    """The file name of the video on the timeline at t seconds."""
    for c, _, a, b in E.owned_spans(project):
        if a <= t < b:
            return os.path.basename(E.source_of(project, c)["path"])
    return None


def _name(height):
    return "4K" if height >= 2160 else f"{height}p"


def describe(est):
    """The estimate in a few lines, for the terminal."""
    lines = [f"Mode: {est['mode']}; predicted {size_words(est['predicted_bytes'])} of "
             f"{size_words(est['budget_bytes'])} ({est['width']}×{est['height']}, {est['codec']})."]
    if est.get("quality_mean") is not None:
        lines.append(f"Quality: {est['words']} ({est['metric'].upper()} {est['quality_mean']:g}, "
                     f"lowest {est['quality_low']:g}).")
    if est.get("crf_equivalent") is not None:
        lines.append(f"Looks like constant quality {est['crf_equivalent']:g}.")
    if est.get("encode_minutes"):
        lines.append(f"Encoding will take about {est['encode_minutes']:g} min.")
    return lines + est["notes"]


# ------------------------------------------------------------------ files
def estimate_path(project_path):
    return Path(project_path).resolve().parent / "estimate.json"


def save_estimate(project_path, est):
    path = estimate_path(project_path)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(est, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def saved_estimate(project_path, key):
    try:
        est = json.loads(estimate_path(project_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return est if isinstance(est, dict) and est.get("key") == key else None


def _workdir():
    work = tempfile.mkdtemp(prefix="vac_sizing_")
    procs.register_path(work)
    return work


def _drop(work):
    procs.unregister_path(work)
    shutil.rmtree(work, ignore_errors=True)


# Injectable for tests: sample length and count (None = §10.4's values).
SAMPLE_LEN = None
SAMPLE_COUNT = None


def _copy_allowed(project, s, reencode):
    """(plan, whether a copy is possible)."""
    p = E.plan(project)
    cv = output_canvas(p["canvas"], s.get("height"))
    return p, p["mode"] == "copy" and not reencode and cv == p["canvas"]


def run_estimate(project_path, overrides=None, reencode=False):
    """--estimate: stage A only; writes estimate.json beside the project."""
    project = E.ensure_probes(E.load(project_path))
    s = with_overrides(E.settings(project), overrides)
    if not s.get("budget"):
        raise E.ProjectError("--estimate needs a budget (--budget, --size or export.budget)")
    E.check_files(project)
    print_step(1, 2, "Checking the cuts")
    _, allowed = _copy_allowed(project, s, reencode)
    work = _workdir()
    try:
        est = estimate(project, s, allowed, work, reencode, SAMPLE_LEN, SAMPLE_COUNT,
                       step=lambda title: print_step(2, 2, title))
    finally:
        _drop(work)
    path = save_estimate(project_path, est)
    for line in describe(est):
        print_info(line)
    events.emit("result", output=None, sidecar=str(path), replaced=False,
                results={"estimate": est})
    print_success(f"Estimate written to {path}")
    return est


# ------------------------------------------------------------------ stage B (§10.5)
def creation_time(project):
    times = [((src.get("probe") or {}).get("creation_time"))
             for src in (project.get("sources") or {}).values()]
    times = sorted(t for t in times if t)
    return times[0] if times else None


def _estimate_for(project_path, project, s, allowed, reencode, step):
    """The saved estimate when its key matches, else a new one (saved)."""
    key = estimate_key(project, s, reencode, SAMPLE_LEN, SAMPLE_COUNT)
    est = saved_estimate(project_path, key)
    if est:
        step("Using the saved estimate")
    else:
        work = _workdir()
        try:
            est = estimate(project, s, allowed, work, reencode, SAMPLE_LEN, SAMPLE_COUNT,
                           step=step)
        finally:
            _drop(work)
        save_estimate(project_path, est)
    for line in describe(est):
        print_info(line)
    return est


def _encode(project, s, est, partial, measured, cv, work, steps):
    """Stage B for the encoding modes; returns (commands, passes)."""
    seconds = E.duration(project)
    mode = est["mode"]
    one_pass = bool(s.get("one_pass")) and mode == "size" and not s.get("fast")
    stamp = creation_time(project)
    extra = ["-metadata", f"creation_time={stamp}"] if stamp else []
    if s.get("fast"):
        rate = {"bps": est["target_bps"], "hardware": True}
    elif mode == "quality":
        rate = {"crf": est.get("crf") or ceiling_crf(s)}
    elif one_pass:
        rate = {"crf": est["crf_equivalent"]}
    else:
        rate = {"bps": est["target_bps"], "log": str(Path(work) / "stats")}
    commands = []
    if "log" in rate:
        steps(4, "Analysing the picture")
        cmd = E.pass1_command(project, s, cv, rate)
        ffmpeg_utils.run_ffmpeg(cmd, seconds, "Analysing")
        commands.append(cmd)
        rate = {**rate, "pass": 2}
    steps(5, f"Encoding {cv['width']}×{cv['height']} ({E.encoder_name(s, rate)})")
    cmd = E.encode_command(project, s, partial, measured, cv, rate, extra)
    ffmpeg_utils.run_ffmpeg(cmd, seconds, "Encoding")
    commands.append(cmd)
    return commands, len(commands)


def run(project_path, project, s, out, force_encode=False):
    """A budgeted export: estimate (or reuse a saved one), then the one real encode."""
    seconds = E.duration(project)
    steps = lambda i, title: print_step(i, 5, title)
    steps(1, "Checking the cuts")
    p, allowed = _copy_allowed(project, s, force_encode)
    est = _estimate_for(project_path, project, s, allowed, force_encode,
                        lambda title: steps(2, title))
    cv = {**p["canvas"], "width": est["width"], "height": est["height"]}
    if est["mode"] != "copy" and p["mode"] == "copy":
        why = ("re-encoding was asked for" if force_encode else
               "the picture is made smaller" if cv != p["canvas"] else
               "the videos are larger than the budget")
        p = {**p, "mode": "encode", "reasons": [why]}

    steps(3, "Measuring loudness")
    measured = E.measure(project, s, seconds)

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial" + out.suffix)
    procs.register_path(str(partial))
    work = _workdir()
    verified = None
    commands = []
    try:
        if est["mode"] == "copy":
            steps(4, "Writing (video copied, audio encoded)")
            commands, verified, p = E.write_copy(project, s, partial, measured, work, p)
            passes = 0
            if p["mode"] == "encode":
                # The joins did not hold: estimate as an encode, then encode.
                est = _estimate_for(project_path, project, s, False, True,
                                    lambda title: steps(4, title))
                cv = {**p["canvas"], "width": est["width"], "height": est["height"]}
        if est["mode"] != "copy":
            p = {**p, "canvas": cv}
            more, passes = _encode(project, s, est, partial, measured, cv, work, steps)
            commands = (commands if verified is False else []) + more
        subtitle_info = E.add_subtitles(partial, project, project_path, s, work)
        os.replace(partial, out)
        procs.unregister_path(str(partial))
    finally:
        _drop(work)

    size = out.stat().st_size
    over = size / est["budget_bytes"] - 1
    if over > OVER_BUDGET_WARN:
        print_warning(f"The file is {size_words(size)}, {over:.0%} over the budget of "
                      f"{size_words(est['budget_bytes'])}.")
    result = E.base_result(p, s, measured)
    if verified is not None:
        result["copy_verified"] = verified
    result.update(budget_bytes=est["budget_bytes"], size_bytes=size, budget_mode=est["mode"],
                  passes=passes, over_budget=over > OVER_BUDGET_WARN,
                  codec="copy" if est["mode"] == "copy" else s.get("codec") or "h264",
                  estimate=est)
    if subtitle_info:
        result["subtitles"] = E.subtitle_result(subtitle_info, E.write_subtitle_files(out, subtitle_info))
    E.finish(out, project_path, project, commands, result)
    return out, result
