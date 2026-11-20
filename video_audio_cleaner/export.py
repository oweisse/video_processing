"""clean-audio-export: render an edit (a project.json) into one upload-ready MP4.

docs/gui-design.md §8-§9. A project is a list of clips (a range of a source video,
heard through a cleaned output when one covers it), optional music under them, and
export settings. The whole edit becomes one ffmpeg command:

  - lossless when it can: one source, no transitions, every cut on a keyframe and
    the picture unchanged → the video is stream-copied, only the audio is encoded;
  - otherwise each clip is scaled/padded to the canvas, joined (hard cut or
    crossfade) and encoded with libx264 (or VideoToolbox, "fast").

Audio: the clips' speech, the music bed ducked under it (sidechaincompress), then one
plain gain to the loudness target, measured (ebur128) on an audio-only pass first, and
a true-peak limiter. No loudnorm: its own measurement drifts on short edits, and a
single gain never changes the dynamics.

Output: MP4, H.264 High yuv420p, AAC 48 kHz stereo, +faststart — accepted as is by
Google Photos and YouTube. A .params.json beside it records the project and command.
"""
import argparse
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import events, ffmpeg_utils, probe as probe_mod, procs
from .console import print_error, print_info, print_step, print_success, print_warning

SCHEMA = 1
SAMPLE_RATE = 48000
DEFAULT_EXPORT = {"encoder": "libx264", "crf": 18, "preset": "medium", "audio_bitrate": "192k",
                  "target_lufs": -16.0, "target_tp": -1.5, "output": None}
DEFAULT_DUCK = {"enabled": True, "threshold_db": -35.0, "ratio": 6.0, "attack_ms": 20.0,
                "release_ms": 600.0}
FAST_BITRATES = ((720, "8M"), (1080, "16M"), (1440, "28M"), (100000, "45M"))
KEYFRAME_TOLERANCE = 0.002


class ProjectError(ValueError):
    pass


# ------------------------------------------------------------------ the model
def load(path):
    project = json.loads(Path(path).read_text(encoding="utf-8"))
    if project.get("schema") != SCHEMA:
        raise ProjectError(f"{path}: unknown project schema {project.get('schema')}")
    return project


def clips(project):
    """Video clips with their timeline position: [(clip, start_on_timeline, length)]."""
    out, t = [], 0.0
    for i, c in enumerate(project.get("tracks", {}).get("video", [])):
        length = float(c["out_sec"]) - float(c["in_sec"])
        if length <= 0:
            raise ProjectError(f"clip {c.get('id', i)} ends before it starts")
        fade = transition(c) if i else 0.0
        t -= fade
        out.append((c, t, length))
        t += length
    return out


TRANSITIONS = ("crossfade", "soft_cut")


def transition(clip):
    """Seconds this clip overlaps the previous one on the timeline (crossfades only; a
    soft cut blends the sound around the cut without moving anything)."""
    tr = clip.get("transition_in") or {}
    if tr and tr.get("type") not in TRANSITIONS:
        raise ProjectError(f"clip {clip.get('id')} has an unknown transition {tr.get('type')!r}")
    return float(tr.get("sec") or 0.0) if tr.get("type") == "crossfade" else 0.0


# ------------------------------------------------------------------ joins
_durations = {}


def file_duration(path):
    """A media file's length in seconds (ffprobe, remembered per run), or None."""
    if path not in _durations:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", str(path)],
                                 capture_output=True, text=True, timeout=30).stdout
            _durations[path] = float(out.strip())
        except (ValueError, OSError, subprocess.TimeoutExpired):
            _durations[path] = None
    return _durations[path]


def _sound(clip):
    """What a clip is heard through: "mute", ("file", path) or ("source", id)."""
    audio = clip.get("audio") or {}
    if audio.get("mute"):
        return "mute"
    return ("file", audio["file"]) if audio.get("file") else ("source", clip["source"])


def spare_sound(project, clip, durations=file_duration):
    """(before the in point, after the out point): sound that exists beyond the clip's
    edges and a soft cut may borrow."""
    sound = _sound(clip)
    if sound == "mute":
        return 0.0, 0.0
    if sound[0] == "file":
        audio = clip["audio"]
        start = float(audio.get("start_sec") or 0.0)
        length = audio.get("duration_sec")
        if length is None:
            length = durations(audio["file"])
        before = float(clip["in_sec"]) - start
        after = (start + float(length) - float(clip["out_sec"])) if length is not None else 0.0
    else:
        src = source_of(project, clip)
        length = (src.get("probe") or {}).get("duration_sec")
        if length is None:
            length = durations(src["path"])
        before = float(clip["in_sec"])
        after = (float(length) - float(clip["out_sec"])) if length is not None else 0.0
    return max(0.0, before), max(0.0, after)


def joins(project, durations=file_duration):
    """How each clip's sound meets the previous one, aligned with clips():
    [{"kind": "cut" | "crossfade" | "blend" | "dip", "sec": seconds}], and for a blend
    "lead": how much of the overlap lies before the cut.

    A soft cut ("soft_cut") overlaps the two sounds for its length: centred on the cut
    when both sides have sound to spare, else shifted toward the side that has it (a
    cleaned piece ending where the original sound goes on blends early, into the
    original). When the two sides together do not have enough, it dips: the first
    fades out, the second fades in. Back-to-back clips of one video with the same sound
    stay a plain cut. Mirrored by joinsOf() in gui/src/lib/edit.ts
    (tests/data/joins.json).
    """
    cs = clips(project)
    out = [{"kind": "cut", "sec": 0.0}]
    for i in range(1, len(cs)):
        (prev, _, plen), (c, _, length) = cs[i - 1], cs[i]
        tr = c.get("transition_in") or {}
        sec = float(tr.get("sec") or 0.0)
        if tr.get("type") == "crossfade" and sec > 0:
            out.append({"kind": "crossfade", "sec": min(sec, plen / 2, length / 2)})
            continue
        if tr.get("type") != "soft_cut" or sec <= 0:
            out.append({"kind": "cut", "sec": 0.0})
            continue
        d = min(sec, plen, length)
        if (prev["source"] == c["source"] and _sound(prev) == _sound(c)
                and abs(float(prev["out_sec"]) - float(c["in_sec"])) < 0.02):
            out.append({"kind": "cut", "sec": 0.0})
            continue
        if _sound(prev) == "mute" or _sound(c) == "mute":
            out.append({"kind": "dip", "sec": d})
            continue
        after = spare_sound(project, prev, durations)[1]
        before = spare_sound(project, c, durations)[0]
        lead = blend_lead(d, before, after)
        out.append({"kind": "dip", "sec": d} if lead is None
                   else {"kind": "blend", "sec": d, "lead": lead})
    return out


def blend_lead(d, before, after):
    """Where a d-second overlap sits: seconds of it before the cut (the incoming sound
    must have that much before its in point, the outgoing the rest after its out point).
    Centred when it can be, else as near the centre as the spare sound allows; None
    when the two together have less than d."""
    lo, hi = max(0.0, d - after), min(d, before)
    if lo > hi + 1e-6:
        return None
    return round(min(max(d / 2, lo), hi), 6)


def duration(project):
    cs = clips(project)
    return 0.0 if not cs else cs[-1][1] + cs[-1][2]


def owned_spans(project):
    """Each clip's share of the timeline, crossfades split in the middle:
    [(clip, start_on_timeline, owned_from, owned_to)]. The spans tile [0, duration]."""
    cs = clips(project)
    froms = []
    for i, (c, start, length) in enumerate(cs):
        fade = min(transition(c), cs[i - 1][2] / 2, length / 2) if i else 0.0
        froms.append(start + fade / 2)
    tos = froms[1:] + [duration(project)]
    return [(c, start, a, b) for (c, start, _), a, b in zip(cs, froms, tos)]


def excerpt(project, t0, t1):
    """The picture of timeline [t0, t1] as a project of its own (pure): the video clips
    trimmed to the window, crossfades inside it turned into cuts at their middle, music
    dropped. Rendering it through video_graph gives that stretch of the export's picture
    (docs/stitch-and-size-budget-plan.md §10.4)."""
    out = []
    for c, start, a, b in owned_spans(project):
        lo, hi = max(a, t0), min(b, t1)
        if hi - lo < 1e-3:
            continue
        clip = {k: v for k, v in c.items() if k != "transition_in"}
        if transition(c):
            clip["zoom_glide"] = False      # it was a crossfade, not a continuing shot
        clip["in_sec"] = round(float(c["in_sec"]) + lo - start, 6)
        clip["out_sec"] = round(float(c["in_sec"]) + hi - start, 6)
        out.append(clip)
    if not out:
        raise ProjectError(f"nothing on the timeline between {t0:g} and {t1:g} s")
    return {**project, "canvas": canvas(project),
            "tracks": {**(project.get("tracks") or {}), "video": out, "music": []}}


def source_of(project, clip):
    src = project.get("sources", {}).get(clip["source"])
    if not src:
        raise ProjectError(f"clip {clip.get('id')} uses an unknown source {clip['source']}")
    return src


def ensure_probes(project):
    """Probe again (in memory) the sources whose embedded probe predates the current
    probe version, so plan() can compare formats. Missing files are left alone."""
    for src in (project.get("sources") or {}).values():
        old = src.get("probe") or {}
        if (old.get("probe_version") or 1) < probe_mod.PROBE_VERSION and os.path.exists(src["path"]):
            try:
                src["probe"] = probe_mod.probe(src["path"])
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                pass
    return project


def sar(video):
    return float((video or {}).get("sar") or 1.0)


def display_width(video):
    """The picture's width once its pixels are square (tape transfers often are not)."""
    return int(round(int(video["width"]) * sar(video) / 2)) * 2


def suggest_canvas(items):
    """A common frame for clips from different videos.

    items: [{"display_width", "height", "fps", "seconds"}]. The size is the largest
    picture (ties: the one with more seconds); the frame rate is the one carrying the
    most seconds (rates within 0.01 count as the same). Mirrored in gui/src/lib/edit.ts.
    """
    items = [i for i in items if i.get("display_width") and i.get("height")]
    if not items:
        return None
    big = max(items, key=lambda i: (i["display_width"] * i["height"], i.get("seconds") or 0))
    rates = []
    for i in items:
        fps = float(i.get("fps") or 30)
        for r in rates:
            if abs(r[0] - fps) <= 0.01:
                r[1] += i.get("seconds") or 0
                break
        else:
            rates.append([fps, i.get("seconds") or 0])
    fps = max(rates, key=lambda r: r[1])[0]
    return {"width": int(big["display_width"]), "height": int(big["height"]), "fps": fps}


def canvas_items(project):
    """suggest_canvas() input from the project's clips."""
    items = []
    for c, _, length in clips(project):
        video = (source_of(project, c).get("probe") or {}).get("video") or {}
        if video.get("width"):
            items.append({"display_width": display_width(video), "height": int(video["height"]),
                          "fps": float(video.get("fps") or 30), "seconds": length})
    return items


def canvas(project):
    """The output picture: the project's, else the first clip's source."""
    c = project.get("canvas")
    if c and c.get("width") and c.get("height") and c.get("fps"):
        return {"width": int(c["width"]), "height": int(c["height"]), "fps": float(c["fps"])}
    cs = clips(project)
    if not cs:
        raise ProjectError("the project has no clips")
    probe = source_of(project, cs[0][0]).get("probe") or {}
    video = probe.get("video") or {}
    if not video.get("width"):
        raise ProjectError("the canvas is not set and the first source has no probe")
    return {"width": display_width(video), "height": int(video["height"]),
            "fps": float(video.get("fps") or 30)}


def settings(project):
    return {**DEFAULT_EXPORT, **(project.get("export") or {})}


# ------------------------------------------------------------------ copy or encode
# What two videos must share for their packets to be joined without re-encoding, with
# the words a mismatch is reported in.
_SAME_FORMAT = (("codec", "codecs"), ("width", "picture sizes"), ("height", "picture sizes"),
                ("pix_fmt", "colour formats"), ("fps", "frame rates"), ("sar", "pixel shapes"),
                ("field_order", "scan types"), ("profile", "encoder settings"),
                ("level", "encoder settings"), ("time_base", "encoder settings"),
                ("extradata_md5", "encoder settings"))


def format_difference(videos):
    """Why these video streams cannot be joined by copying, or None when they can.

    videos: probe "video" dicts, one per distinct source.
    """
    if len(videos) < 2:
        return None
    if any(not v.get("extradata_md5") for v in videos):
        return "the videos' formats have not been compared yet"
    first = videos[0]
    for v in videos[1:]:
        for key, words in _SAME_FORMAT:
            a, b = first.get(key), v.get(key)
            if key == "fps":
                same = a is not None and b is not None and abs(float(a) - float(b)) <= 0.01
            elif key == "sar":
                same = abs(sar(first) - sar(v)) <= 0.001
            else:
                same = a == b
            if not same:
                if key in ("width", "height"):
                    detail = f"{first.get('width')}×{first.get('height')} and {v.get('width')}×{v.get('height')}"
                elif key in ("codec", "fps"):
                    detail = f"{a:g} and {b:g}" if key == "fps" and a and b else f"{a} and {b}"
                else:
                    detail = None
                return f"the videos have different {words}" + (f" ({detail})" if detail else "")
    return None


def picture_runs(project):
    """The picture as continuous runs: back-to-back clips of the same video (one ends where
    the next begins, no crossfade) are one run, since only their sound differs.
    Returns [{"source", "in_sec", "out_sec", "clips": [indices]}]."""
    runs = []
    for i, (c, _, _) in enumerate(clips(project)):
        last = runs[-1] if runs else None
        if (last and last["source"] == c["source"] and not transition(c)
                and abs(last["out_sec"] - float(c["in_sec"])) < 0.001):
            last["out_sec"] = float(c["out_sec"])
            last["clips"].append(i)
        else:
            runs.append({"source": c["source"], "in_sec": float(c["in_sec"]),
                         "out_sec": float(c["out_sec"]), "clips": [i]})
    return runs


def plan(project, keyframe_at=ffmpeg_utils.probe_keyframe_at_or_before):
    """{"mode": "copy"|"encode", "reasons": [...why not copy...], "duration", "canvas"}.

    Clips from several videos are copied too, when the videos' streams are identical in
    format (format_difference) and every cut is on a keyframe of its own video.
    """
    cs = clips(project)
    if not cs:
        raise ProjectError("the project has no clips")
    cv = canvas(project)
    reasons = []
    ids = list(dict.fromkeys(c["source"] for c, _, _ in cs))
    videos = [(source_of(project, {"source": i, "id": i}).get("probe") or {}).get("video") or {}
              for i in ids]
    if any(transition(c) for c, _, _ in cs[1:]):
        reasons.append("crossfades need re-encoding")
    if any(c.get("video_filters") for c, _, _ in cs):
        reasons.append("visual edits need re-encoding")
    if any(framing(c) for c, _, _ in cs):
        reasons.append("zoomed clips need re-encoding")
    different = format_difference(videos)
    if different:
        reasons.append(different)
    else:
        probe = videos[0]
        shown = (display_width(probe), probe.get("height")) if probe.get("width") else (None, None)
        if shown != (cv["width"], cv["height"]):
            reasons.append(f"the canvas ({cv['width']}×{cv['height']}) differs from the "
                           f"video ({shown[0]}×{shown[1]})")
        if probe.get("fps") and abs(float(probe["fps"]) - cv["fps"]) > 0.01:
            reasons.append(f"the frame rate changes ({probe['fps']:g} → {cv['fps']:g})")
        if probe.get("field_order") not in (None, "progressive", "unknown"):
            reasons.append("the video is interlaced and gets deinterlaced")
        if not reasons:
            # Only where the picture jumps must a cut fall on a keyframe.
            off = []
            for run in picture_runs(project):
                start = run["in_sec"]
                if start <= 0:
                    continue
                k = keyframe_at(project["sources"][run["source"]]["path"], start)
                if k is None or abs(k - start) > KEYFRAME_TOLERANCE:
                    off.append(run["source"])
            if off:
                reasons.append(f"{len(off)} cut(s) do not start on a keyframe")
    return {"mode": "encode" if reasons else "copy", "reasons": reasons,
            "duration": duration(project), "canvas": cv}


# ------------------------------------------------------------------ the graph
class Inputs:
    def __init__(self):
        self.args = []
        self.count = 0

    def add(self, path, start=None, length=None, lavfi=False):
        if lavfi:
            self.args += ["-f", "lavfi"]
        if start:
            self.args += ["-ss", f"{start:.6f}"]
        if length is not None:
            self.args += ["-t", f"{length:.6f}"]
        self.args += ["-i", str(path)]
        self.count += 1
        return self.count - 1


AFMT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


def _db_to_linear(db):
    return 10 ** (db / 20)


def audio_graph(project, inputs, video_inputs=None):
    """Filter chains producing [speech] and, with music, the mix in [mix].

    video_inputs: clip index → input index of that clip's source (when the source's
    own audio is used); created here when not given (audio-only measuring pass).
    Returns (chains, final label).
    """
    chains = []
    cs = clips(project)
    total = duration(project)
    js = joins(project)
    labels = []
    for i, (c, _, length) in enumerate(cs):
        audio = c.get("audio") or {}
        gain = float(audio.get("gain_db") or 0.0)
        # A blend borrows sound from beyond the cut: before it for the incoming clip
        # (its lead), after it for the outgoing one (the rest of the overlap).
        pre = js[i]["lead"] if js[i]["kind"] == "blend" else 0.0
        nxt = js[i + 1] if i + 1 < len(cs) else {"kind": "cut", "sec": 0.0}
        post = nxt["sec"] - nxt["lead"] if nxt["kind"] == "blend" else 0.0
        span = length + pre + post
        if audio.get("mute"):
            idx = inputs.add(f"anullsrc=r={SAMPLE_RATE}:cl=stereo", length=span, lavfi=True)
        elif audio.get("file"):
            offset = float(c["in_sec"]) - float(audio.get("start_sec") or 0.0)
            if offset < -0.05:
                raise ProjectError(f"clip {c.get('id')} starts before its cleaned audio")
            idx = inputs.add(audio["file"], max(0.0, offset - pre), span)
        else:
            idx = (video_inputs or {}).get(i) if not (pre or post) else None
            if idx is None:
                idx = inputs.add(source_of(project, c)["path"], float(c["in_sec"]) - pre, span)
        chain = (f"[{idx}:a:0]asetpts=PTS-STARTPTS,{AFMT},apad,"
                 f"atrim=duration={span:.6f},volume={gain:g}dB")
        # A dip fades the sound out at the end of one clip and in at the start of the next.
        if js[i]["kind"] == "dip":
            chain += f",afade=t=in:st=0:d={js[i]['sec'] / 2:.6f}:curve=qsin"
        if nxt["kind"] == "dip":
            chain += f",afade=t=out:st={length - nxt['sec'] / 2:.6f}:d={nxt['sec'] / 2:.6f}:curve=qsin"
        chains.append(chain + f"[a{i}]")
        labels.append(f"a{i}")
    # Join like the picture: hard cuts and dips concatenate, crossfades overlap, and a
    # blend overlaps the borrowed sound on both sides of the cut (the length stays).
    cur = labels[0]
    for i in range(1, len(cs)):
        j = js[i]
        nxt = f"aj{i}"
        if j["kind"] == "crossfade":
            chains.append(f"[{cur}][{labels[i]}]acrossfade=d={j['sec']:.6f}:c1=tri:c2=tri[{nxt}]")
        elif j["kind"] == "blend":
            chains.append(f"[{cur}][{labels[i]}]acrossfade=d={j['sec']:.6f}:c1=qsin:c2=qsin[{nxt}]")
        else:
            chains.append(f"[{cur}][{labels[i]}]concat=n=2:v=0:a=1[{nxt}]")
        cur = nxt
    chains.append(f"[{cur}]atrim=duration={total:.6f}[speech]")

    music = (project.get("tracks") or {}).get("music") or []
    if not music:
        return chains, "speech"
    mlabels = []
    for k, m in enumerate(music):
        m_in, m_out = float(m.get("in_sec") or 0.0), float(m["out_sec"])
        length = m_out - m_in
        at = max(0.0, float(m.get("at_sec") or 0.0))
        if length <= 0 or at >= total:
            continue
        length = min(length, total - at)
        idx = inputs.add(m["file"], m_in, length)
        chain = f"[{idx}:a:0]asetpts=PTS-STARTPTS,{AFMT},volume={float(m.get('gain_db', -18)):g}dB"
        fi, fo = float(m.get("fade_in_sec") or 0), float(m.get("fade_out_sec") or 0)
        if fi > 0:
            chain += f",afade=t=in:st=0:d={min(fi, length):.3f}"
        if fo > 0:
            chain += f",afade=t=out:st={max(0.0, length - fo):.3f}:d={min(fo, length):.3f}"
        if at > 0:
            chain += f",adelay=delays={at * 1000:.0f}:all=1"
        chains.append(chain + f"[m{k}]")
        mlabels.append(f"m{k}")
    if not mlabels:
        return chains, "speech"
    if len(mlabels) > 1:
        chains.append("".join(f"[{m}]" for m in mlabels)
                      + f"amix=inputs={len(mlabels)}:normalize=0:duration=longest[mus0]")
    else:
        chains.append(f"[{mlabels[0]}]anull[mus0]")
    chains.append(f"[mus0]apad,atrim=duration={total:.6f}[music]")
    duck = {**DEFAULT_DUCK, **((project.get("tracks") or {}).get("music_duck") or {})}
    if duck.get("enabled"):
        threshold = min(1.0, max(0.000976563, _db_to_linear(float(duck["threshold_db"]))))
        chains.append("[speech]asplit=2[sp][key]")
        chains.append(f"[music][key]sidechaincompress=threshold={threshold:.6f}"
                      f":ratio={float(duck['ratio']):g}:attack={float(duck['attack_ms']):g}"
                      f":release={float(duck['release_ms']):g}[ducked]")
        chains.append("[sp][ducked]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        chains.append("[speech][music]amix=inputs=2:normalize=0:duration=first[mix]")
    return chains, "mix"


LIMITER_HEADROOM_DB = 3.0    # how far over the ceiling the limiter may be asked to catch


def gain_db(s, measured):
    """Gain from the measured (integrated LUFS, true peak dBTP) to the target.

    Peaks the gain would push over the ceiling are left to the limiter, but only a
    few dB of them: past that the gain is lowered rather than the speech squashed.
    """
    if measured is None or measured[0] is None:
        return 0.0
    lufs, peak = measured
    gain = float(s["target_lufs"]) - lufs
    if peak is not None:
        gain = min(gain, float(s["target_tp"]) - peak + LIMITER_HEADROOM_DB)
    return gain


def loudness(s, measured):
    ceiling = 10 ** (float(s["target_tp"]) / 20)
    return (f"volume={gain_db(s, measured):.2f}dB,aresample={SAMPLE_RATE},"
            f"alimiter=limit={ceiling:.4f}:level=disabled")


def _deinterlace(project, clip):
    probe = (source_of(project, clip).get("probe") or {}).get("video") or {}
    return probe.get("field_order") not in (None, "progressive", "unknown")


def framing(c):
    """A clip's zoom: {x, y, w, h} as fractions of its (square-pixel) picture, or None for the whole."""
    f = c.get("frame")
    if not f:
        return None
    w, h = (min(1.0, max(0.02, float(f.get(k, 1)))) for k in ("w", "h"))
    x = min(1.0 - w, max(0.0, float(f.get("x", 0))))
    y = min(1.0 - h, max(0.0, float(f.get("y", 0))))
    if w > 0.999 and h > 0.999:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def crop_filter(f):
    """The ffmpeg crop for a framing (even sizes, so any encoder takes them)."""
    return (f"crop=w=trunc(iw*{f['w']:.6f}/2)*2:h=trunc(ih*{f['h']:.6f}/2)*2"
            f":x=trunc(iw*{f['x']:.6f}):y=trunc(ih*{f['y']:.6f})")


# ------------------------------------------------------------------ zoom glides
ZOOM_GLIDE_SEC = 1.0


def _same_frame(a, b):
    if not a or not b:
        return not a and not b
    return all(abs(a[k] - b[k]) < 1e-4 for k in ("x", "y", "w", "h"))


def zoom_glide(prev, c):
    """The glide into c ({from, to, sec}, frames as framing() gives them), or None.

    Where c continues prev's shot (same video, no gap) with another framing, the picture
    moves from prev's framing to c's over c's first `sec` seconds, eased. c["zoom_glide"]:
    absent = ZOOM_GLIDE_SEC, False = a plain cut, {"sec": n} = another length. Mirrors
    zoomGlide() in gui/src/lib/edit.ts."""
    if prev is None or prev.get("source") != c.get("source"):
        return None
    if abs(float(prev["out_sec"]) - float(c["in_sec"])) > 0.05:
        return None
    if (c.get("transition_in") or {}).get("type") == "crossfade" or c.get("zoom_glide") is False:
        return None
    a, b = framing(prev), framing(c)
    if _same_frame(a, b):
        return None
    sec = min(float((c.get("zoom_glide") or {}).get("sec", ZOOM_GLIDE_SEC)),
              float(c["out_sec"]) - float(c["in_sec"]))
    return {"from": a, "to": b, "sec": sec} if sec > 0.04 else None


def _canvas_rect(f, picture, shape):
    """A framing as (left, top, size) fractions of the output-shaped canvas the whole picture
    sits in (letterboxed or pillarboxed); None = the whole canvas."""
    if picture >= shape:
        pw, ph, ox, oy = 1.0, shape / picture, 0.0, (1 - shape / picture) / 2
    else:
        pw, ph, ox, oy = picture / shape, 1.0, (1 - picture / shape) / 2, 0.0
    if f is None:
        return 0.0, 0.0, 1.0
    return ox + f["x"] * pw, oy + f["y"] * ph, f["w"] * pw


def glide_filters(g, picture, shape, w, h, src_fps):
    """Steps that put the whole picture in an output-shaped canvas at its own resolution and
    move a zoompan window from one framing to the other (smoothstep easing)."""
    if picture >= shape:
        pad = f"pad=w=iw:h=trunc(iw/{shape:.6f}/2)*2:x=0:y=(oh-ih)/2:color=black"
    else:
        pad = f"pad=w=trunc(ih*{shape:.6f}/2)*2:h=ih:x=(ow-iw)/2:y=0:color=black"
    u0, v0, s0 = _canvas_rect(g["from"], picture, shape)
    u1, v1, s1 = _canvas_rect(g["to"], picture, shape)
    e = f"clip(it/{g['sec']:.6f},0,1)"
    k = f"({e}*{e}*(3-2*{e}))"
    mix = lambda a, b: f"({a:.6f}+({b - a:.6f})*{k})"
    zp = (f"zoompan=z='1/{mix(s0, s1)}':x='{mix(u0, u1)}*iw':y='{mix(v0, v1)}*ih'"
          f":d=1:s={w}x{h}:fps={src_fps:.6f}")
    return [pad, zp]


def video_graph(project, inputs, cv):
    """Per-clip picture chains joined into [video]; returns (chains, {clip: input})."""
    chains, used = [], {}
    cs = clips(project)
    w, h, fps = cv["width"], cv["height"], cv["fps"]
    for i, (c, _, length) in enumerate(cs):
        glide = zoom_glide(cs[i - 1][0] if i else None, c)
        idx = inputs.add(source_of(project, c)["path"], float(c["in_sec"]), length)
        used[i] = idx
        steps = ["setpts=PTS-STARTPTS"]
        if _deinterlace(project, c):
            steps.append("bwdif=mode=send_frame")
        if abs(sar((source_of(project, c).get("probe") or {}).get("video")) - 1) > 0.001:
            steps += ["scale=trunc(iw*sar/2)*2:ih", "setsar=1"]
        f = framing(c)
        if glide:
            v = (source_of(project, c).get("probe") or {}).get("video") or {}
            picture = display_width(v) / int(v["height"]) if v.get("height") else w / h
            steps += list(c.get("video_filters") or [])
            steps += glide_filters(glide, picture, w / h, w, h, float(v.get("fps") or fps))
            steps += ["setsar=1"]
        else:
            if f:
                steps.append(crop_filter(f))
            steps += list(c.get("video_filters") or [])
            steps += [f"scale={w}:{h}:force_original_aspect_ratio=decrease" + (":flags=lanczos" if f else ""),
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1"]
        steps += [f"fps={fps:g}", "format=yuv420p", f"trim=duration={length:.6f}",
                  "setpts=PTS-STARTPTS"]
        chains.append(f"[{idx}:v:0]{','.join(steps)}[v{i}]")
    cur, t = "v0", cs[0][2]
    for i in range(1, len(cs)):
        fade = min(transition(cs[i][0]), cs[i - 1][2] / 2, cs[i][2] / 2)
        nxt = f"vj{i}"
        if fade > 0:
            chains.append(f"[{cur}][v{i}]xfade=transition=fade:duration={fade:.6f}"
                          f":offset={t - fade:.6f}[{nxt}]")
            t += cs[i][2] - fade
        else:
            chains.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[{nxt}]")
            t += cs[i][2]
        cur = nxt
    chains.append(f"[{cur}]null[video]")
    return chains, used


CEILING_CRF = {"libx264": 18.0, "libx265": 20.0}     # visually lossless (§10.4)


def encoder_args(s, cv, rate=None):
    """The video encoder's arguments.

    rate None with H.264: today's fixed-quality settings, unchanged. Otherwise (HEVC, or a
    budgeted encode, docs/stitch-and-size-budget-plan.md §10.5) rate may hold "crf" (a
    constant quality, default the codec's ceiling), "bps" (a bitrate), "pass" (1 or 2)
    with "log" (the stats file prefix), and "hardware" (VideoToolbox at "bps").
    """
    hevc = s.get("codec") == "hevc"
    hardware = s.get("encoder") == "h264_videotoolbox" or s.get("fast")
    if rate is None and not hevc and not s.get("fast"):
        if hardware:
            rate = next(r for limit, r in FAST_BITRATES if cv["height"] <= limit)
            return ["-c:v", "h264_videotoolbox", "-b:v", rate, "-profile:v", "high",
                    "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-crf", str(int(s.get("crf", 18))), "-preset",
                str(s.get("preset", "medium")), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    rate = rate or {}
    gop = ["-g", str(max(1, round(2 * float(cv["fps"]))))]
    if rate.get("hardware") or (hardware and not rate):
        bps = rate.get("bps") or next(r for limit, r in FAST_BITRATES if cv["height"] <= limit)
        args = ["-c:v", "hevc_videotoolbox" if hevc else "h264_videotoolbox", "-b:v", str(bps)]
        args += ["-tag:v", "hvc1"] if hevc else ["-profile:v", "high"]
        return args + ["-pix_fmt", "yuv420p", *gop]
    lib = "libx265" if hevc else "libx264"
    args = ["-c:v", lib, "-preset", str(s.get("preset", "medium"))]
    if rate.get("bps"):
        args += ["-b:v", str(int(rate["bps"]))]
    else:
        args += ["-crf", f"{float(rate.get('crf', CEILING_CRF[lib])):g}"]
    if hevc:
        params = ["log-level=error"]
        if rate.get("pass"):
            params += [f"pass={int(rate['pass'])}", f"stats={rate['log']}.log"]
        args += ["-x265-params", ":".join(params), "-tag:v", "hvc1"]
    else:
        args += ["-profile:v", "high"]
        if rate.get("pass"):
            args += ["-pass", str(int(rate["pass"])), "-passlogfile", str(rate["log"])]
    return args + ["-pix_fmt", "yuv420p", *gop]


def encoder_name(s, rate=None):
    """The encoder encoder_args() picks, for messages."""
    args = encoder_args(s, {"width": 2, "height": 2, "fps": 30}, rate)
    return args[args.index("-c:v") + 1]


def pass1_command(project, s, cv, rate):
    """The analysis pass of a two-pass encode: the picture only, nothing written."""
    inputs = Inputs()
    vchains, _ = video_graph(project, inputs, cv)
    return ["ffmpeg", "-y", *inputs.args, "-filter_complex", ";".join(vchains),
            "-map", "[video]", *encoder_args(s, cv, {**rate, "pass": 1}), "-an",
            "-t", f"{duration(project):.6f}", "-f", "null", "-"]


def measure_command(project, s):
    inputs = Inputs()
    chains, label = audio_graph(project, inputs)
    graph = ";".join(chains) + f";[{label}]ebur128=peak=true[out]"
    return ["ffmpeg", "-y", "-hide_banner", "-nostats", *inputs.args,
            "-filter_complex", graph, "-map", "[out]", "-f", "null", "-"]


def encode_command(project, s, out, measured, cv, rate=None, extra=()):
    inputs = Inputs()
    vchains, used = video_graph(project, inputs, cv)
    achains, label = audio_graph(project, inputs, video_inputs=used)
    tail = f"[{label}]{loudness(s, measured)}[aout]"
    graph = ";".join(vchains + achains + [tail])
    return ["ffmpeg", "-y", *inputs.args, "-filter_complex", graph,
            "-map", "[video]", "-map", "[aout]", *encoder_args(s, cv, rate),
            "-c:a", "aac", "-b:a", str(s["audio_bitrate"]), "-ar", str(SAMPLE_RATE),
            "-ac", "2", "-t", f"{duration(project):.6f}", *extra, "-movflags", "+faststart",
            str(out)]


def concat_list(project):
    lines = ["ffconcat version 1.0"]
    for run in picture_runs(project):
        path = project["sources"][run["source"]]["path"].replace("'", "'\\''")
        lines += [f"file '{path}'", f"inpoint {run['in_sec']:.6f}",
                  f"outpoint {run['out_sec']:.6f}"]
    return "\n".join(lines) + "\n"


def copy_commands(project, s, out, measured, work):
    """(audio render, mux) for the lossless path: copy the video, encode the audio."""
    inputs = Inputs()
    achains, label = audio_graph(project, inputs)
    tail = f"[{label}]{loudness(s, measured)}[aout]"
    wav = os.path.join(work, "audio.wav")
    audio = ["ffmpeg", "-y", *inputs.args, "-filter_complex", ";".join(achains + [tail]),
             "-map", "[aout]", "-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE), wav]
    lst = os.path.join(work, "clips.ffconcat")
    Path(lst).write_text(concat_list(project), encoding="utf-8")
    mux = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", wav,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
           "-b:a", str(s["audio_bitrate"]), "-t", f"{duration(project):.6f}",
           "-movflags", "+faststart", str(out)]
    return audio, mux


def verify_copy(path, joins, expected_sec, fps):
    """Check a stream-copied export: the picture decodes cleanly around every join and
    the video lasts as long as the timeline. Returns None, or what went wrong."""
    for t in joins:
        start = max(0.0, t - 1.0)
        out = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-ss", f"{start:.3f}",
                              "-t", "3", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                             capture_output=True, text=True)
        if out.returncode != 0 or out.stderr.strip():
            return f"the picture does not decode cleanly at {_clock(t)}"
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=duration", "-of", "json", str(path)],
        capture_output=True, text=True).stdout or "{}")
    try:
        got = float(info["streams"][0]["duration"])
    except (KeyError, IndexError, TypeError, ValueError):
        return "the result's length could not be read"
    if abs(got - expected_sec) > 2.0 / fps + 0.001:
        return f"the video lasts {got:.2f} s instead of {expected_sec:.2f} s"
    return None


def _clock(t):
    t = int(round(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# ------------------------------------------------------------------ running it
def output_path(project, project_path):
    s = settings(project)
    if s.get("output"):
        return Path(os.path.expanduser(s["output"]))
    name = project.get("name") or Path(project_path).parent.name
    # Projects live in OUTPUT_DIR/_projects/<name>/; exports go to OUTPUT_DIR.
    return Path(project_path).resolve().parent.parent.parent / f"{name}.mp4"


def check_files(project):
    for c, _, _ in clips(project):
        for path in (source_of(project, c)["path"], (c.get("audio") or {}).get("file")):
            if path and not os.path.exists(path):
                raise ProjectError(f"missing file: {path}")


def run(project_path, out=None, force_encode=False, overrides=None):
    """Export the project. overrides: command-line values for the size-budget keys
    (budget, codec, height, fast, one_pass; docs/stitch-and-size-budget-plan.md §10)."""
    from . import sizing
    project = ensure_probes(load(project_path))
    s = sizing.with_overrides(settings(project), overrides)
    out = Path(out) if out else output_path(project, project_path)
    check_files(project)
    if s.get("budget"):
        return sizing.run(project_path, project, s, out, force_encode)
    total_steps = 3
    print_step(1, total_steps, "Checking the cuts")
    p = plan(project)
    if force_encode and p["mode"] == "copy":
        p = {**p, "mode": "encode", "reasons": ["re-encoding was asked for"]}
    shown = sizing.output_canvas(p["canvas"], s.get("height"))
    if shown != p["canvas"]:
        p = {**p, "canvas": shown}
        if p["mode"] == "copy":
            p = {**p, "mode": "encode", "reasons": [f"the picture is made smaller "
                                                    f"({shown['width']}×{shown['height']})"]}
    total = p["duration"]
    if p["mode"] == "copy":
        print_success(f"Lossless: the video is copied as is ({total:.1f} s).")
    else:
        print_info("Re-encoding the video: " + "; ".join(p["reasons"]) + ".")

    print_step(2, total_steps, "Measuring loudness")
    measured = measure(project, s, total)

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial" + out.suffix)
    procs.register_path(str(partial))
    work = tempfile.mkdtemp(prefix="vac_export_")
    procs.register_path(work)
    try:
        verified = None
        if p["mode"] == "copy":
            print_step(3, total_steps, "Writing (video copied, audio encoded)")
            commands, verified, p = write_copy(project, s, partial, measured, work, p)
        if p["mode"] == "encode":
            print_step(3, total_steps, f"Encoding {p['canvas']['width']}×"
                                       f"{p['canvas']['height']} ({encoder_name(s)})")
            cmd = encode_command(project, s, partial, measured, p["canvas"])
            ffmpeg_utils.run_ffmpeg(cmd, total, "Encoding")
            commands = [cmd]
        subtitle_info = add_subtitles(partial, project, project_path, s, work)
        os.replace(partial, out)
        procs.unregister_path(str(partial))
    finally:
        procs.unregister_path(work)
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    result = base_result(p, s, measured)
    if verified is not None:
        result["copy_verified"] = verified
    if subtitle_info:
        result["subtitles"] = subtitle_result(subtitle_info, write_subtitle_files(out, subtitle_info))
    finish(out, project_path, project, commands, result)
    return out, result


def measure(project, s, total):
    """The mix's loudness (integrated LUFS, true peak), measured on an audio-only pass."""
    from .postprocess import parse_ebur128_summary
    measured = parse_ebur128_summary(ffmpeg_utils.run_ffmpeg(measure_command(project, s),
                                                             total, "Measuring"))
    if measured[0] is None:
        print_warning("The audio is near silent; loudness is left as it is.")
    else:
        print_info(f"Measured {measured[0]:.1f} LUFS (true peak {measured[1]:.1f} dBTP); "
                   f"gain {gain_db(s, measured):+.1f} dB to reach "
                   f"{float(s['target_lufs']):g} LUFS.")
    return measured


def write_copy(project, s, partial, measured, work, p):
    """The lossless path, checked at the joins. Returns (commands, verified, plan); the plan
    turns to "encode" (and partial is gone) when the joins did not hold."""
    total = p["duration"]
    audio_cmd, mux = copy_commands(project, s, partial, measured, work)
    ffmpeg_utils.run_ffmpeg(audio_cmd, total, "Audio")
    ffmpeg_utils.run_ffmpeg(mux, total, "Writing")
    verified = None
    cs = clips(project)
    runs = picture_runs(project)
    if len(runs) > 1:
        joins = [cs[run["clips"][0]][1] for run in runs[1:]]
        problem = verify_copy(partial, joins, total, p["canvas"]["fps"])
        verified = problem is None
        if problem:
            print_warning(f"Lossless joining did not hold ({problem}); encoding instead.")
            partial.unlink(missing_ok=True)
            p = {**p, "mode": "encode", "reasons": [f"lossless joining failed: {problem}"]}
    return [audio_cmd, mux], verified, p


def base_result(p, s, measured):
    return {"mode": p["mode"], "reasons": p["reasons"], "duration_sec": round(p["duration"], 3),
            "canvas": p["canvas"], "measured_lufs": measured[0],
            "measured_true_peak_dbtp": measured[1], "gain_db": round(gain_db(s, measured), 2),
            "target_lufs": float(s["target_lufs"])}


# ------------------------------------------------------------------ subtitles (docs/subtitles-plan.md §10)
SUB_TAGS = {"mixed": ("mul", "Original (spoken)"), "en": ("eng", "English"), "he": ("heb", "Hebrew")}


def subtitle_request(s):
    """{"srt": [...], "tracks": [...], "default": lang|None} from export.subtitles, or None."""
    from . import subs
    sub = s.get("subtitles") or {}
    srt = [x for x in sub.get("srt") or [] if x in subs.LANGS]
    tracks = [x for x in sub.get("tracks") or [] if x in subs.LANGS]
    if not srt and not tracks:
        return None
    default = sub.get("default_track") if sub.get("default_track") in tracks else None
    return {"srt": srt, "tracks": tracks, "default": default}


def library_of(project_path):
    """The library folder a project lives in (…/_projects/<name>/project.json)."""
    p = Path(project_path).resolve()
    return p.parents[2] if p.parent.parent.name == "_projects" else p.parent


def add_subtitles(partial, project, project_path, s, work):
    """Embed the requested subtitle tracks into `partial` (a stream-copy remux) and prepare
    the .srt files; returns what write_subtitle_files needs and the report, or None."""
    from . import subs
    req = subtitle_request(s)
    if req is None:
        return None
    lib = Path(s.get("subtitles_dir")) if s.get("subtitles_dir") else subs.docs_dir(library_of(project_path))
    cs = (project.get("tracks") or {}).get("video") or []
    docs = {}
    for sid in {c["source"] for c in cs}:
        p = lib / sid / "subtitles.json"
        docs[sid] = subs.load(p) if p.is_file() else subs.empty(sid)
    wanted = list(dict.fromkeys(req["tracks"] + req["srt"]))
    texts, report = {}, {}
    for lang in wanted:
        items = subs.timeline_cues(cs, docs, lang)
        report[lang] = subs.completeness(cs, docs, lang)
        if not items:
            print_warning(f"No {SUB_TAGS[lang][1].lower()} subtitles on the timeline; left out.")
            continue
        texts[lang] = subs.to_srt(items)
        c = report[lang]
        print_info(f"{SUB_TAGS[lang][1]} subtitles: {c['cues']} lines"
                   + (f", {c['missing']} not translated (shown as spoken)" if lang != "mixed" and c["missing"] else "")
                   + (f", {c['stale']} out of date" if c["stale"] else "")
                   + (f"; {c['uncovered_sec']:.0f} s of the video has none" if c["uncovered_sec"] >= 1 else ""))
    tracks = [lang for lang in req["tracks"] if lang in texts]
    if tracks:
        files = []
        for lang in tracks:
            f = Path(work) / f"{lang}.srt"
            f.write_text(texts[lang], encoding="utf-8")
            files.append(f)
        out2 = Path(partial).with_name(Path(partial).stem + ".subs" + Path(partial).suffix)
        procs.register_path(str(out2))
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(partial)]
        for f in files:
            cmd += ["-f", "srt", "-i", str(f)]
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        for k in range(len(files)):
            cmd += ["-map", f"{k + 1}:0"]
        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
        if _video_codec(partial) == "hevc":
            cmd += ["-tag:v", "hvc1"]
        for k, lang in enumerate(tracks):
            code, title = SUB_TAGS[lang]
            cmd += [f"-metadata:s:s:{k}", f"language={code}", f"-metadata:s:s:{k}", f"title={title}",
                    f"-disposition:s:{k}", "default" if lang == req["default"] else "0"]
        cmd += ["-movflags", "+faststart", str(out2)]
        ffmpeg_utils.run_ffmpeg(cmd, None, "Adding subtitles")
        os.replace(out2, partial)
        procs.unregister_path(str(out2))
    return {"texts": {lang: texts[lang] for lang in req["srt"] if lang in texts}, "tracks": tracks,
            "report": report}


def _video_codec(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out
    except (OSError, subprocess.CalledProcessError):
        return None


def write_subtitle_files(out, info):
    """Name.mixed.srt, Name.en.srt, Name.he.srt beside the exported video; returns the paths."""
    if not info:
        return []
    paths = []
    for lang, text in info["texts"].items():
        p = Path(out).with_name(f"{Path(out).stem}.{lang}.srt")
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        paths.append(str(p))
    return paths


def subtitle_result(info, paths):
    return {"tracks": info["tracks"], "srt": paths, "completeness": info["report"]} if info else None


def finish(out, project_path, project, commands, result):
    """Write the .params.json sidecar and emit the result event."""
    sidecar = out.with_suffix(".params.json")
    from .provenance import environment
    sidecar.write_text(json.dumps({
        "schema": 1, "kind": "export", "output": out.name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_path": str(Path(project_path).resolve()), "project": project,
        "commands": [shlex.join(c) for c in commands], "results": result,
        "environment": environment()}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    events.emit("result", output=str(out), sidecar=str(sidecar), replaced=False, results=result)
    print_success(f"Exported {out}")
    return sidecar


def build_parser():
    p = argparse.ArgumentParser(prog="clean-audio-export", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", help="project.json written by clean-audio-gui.")
    p.add_argument("-o", "--output", default=None,
                   help="Output file (default: the project's export.output, else "
                        "OUTPUT_DIR/<project name>.mp4).")
    p.add_argument("--reencode", action="store_true",
                   help="Re-encode the video even when it could be copied.")
    p.add_argument("--plan", action="store_true",
                   help="Only say whether the export would be lossless, and why not.")
    size = p.add_argument_group(
        "size budget", "Find the best quality that fits, trying short samples first "
        "(docs/stitch-and-size-budget-plan.md §10). 1 GB = 10^9 bytes.")
    which = size.add_mutually_exclusive_group()
    which.add_argument("--budget", default=None, metavar="SIZE/h",
                       help="Size per hour of video: 2GB/h, '2 GB/hour', 1500MB/h. "
                            "Overrides the project's export.budget.")
    which.add_argument("--size", default=None, metavar="SIZE",
                       help="Size of the whole file: 2.5GB, 900MB.")
    size.add_argument("--codec", choices=("h264", "hevc"), default=None,
                      help="hevc (smaller, tagged hvc1 for Apple players; the default under "
                           "a budget) or h264 (plays everywhere; the default otherwise).")
    size.add_argument("--height", type=int, default=None, metavar="N",
                      help="Scale the picture down to N lines (never up).")
    size.add_argument("--fast", action="store_true", default=None,
                      help="Under a budget: Apple's hardware encoder at the budget's "
                           "bitrate (much faster, lower quality).")
    size.add_argument("--one_pass", action="store_true", default=None,
                      help="When the budget is tight: one pass at the quality the samples "
                           "found instead of two passes at the budget's bitrate (faster; "
                           "the size can miss by about 10%%).")
    size.add_argument("--estimate", action="store_true",
                      help="Only try the samples: print the estimate and write "
                           "estimate.json beside the project. No video is written.")
    sub = p.add_argument_group("subtitles (docs/subtitles-plan.md §10)")
    sub.add_argument("--subtitles_srt", default=None, metavar="LANGS",
                     help="Write .srt files beside the video: mixed,en,he (or none). Default: the project's.")
    sub.add_argument("--subtitle_tracks", default=None, metavar="LANGS",
                     help="Embed selectable subtitle tracks: mixed,en,he (or none). Default: the project's.")
    sub.add_argument("--subtitles_dir", default=None,
                     help="Where the subtitle documents are (default: the library's _subtitles).")
    events.add_argument(p)
    return p


def overrides_of(args):
    """The size-budget keys the command line sets (None = the project's)."""
    from . import sizing
    budget = (sizing.parse_budget(args.budget) if args.budget else
              sizing.parse_size(args.size) if args.size else None)
    subtitles = None
    if args.subtitles_srt is not None or args.subtitle_tracks is not None:
        pick = lambda v: [] if v in (None, "none") else [x.strip() for x in v.split(",") if x.strip()]
        subtitles = {"srt": pick(args.subtitles_srt), "tracks": pick(args.subtitle_tracks)}
    return {"budget": budget, "codec": args.codec, "height": args.height,
            "fast": args.fast, "one_pass": args.one_pass, "subtitles": subtitles,
            "subtitles_dir": args.subtitles_dir}


def main(argv=None):
    args = build_parser().parse_args(argv)
    procs.install_signal_handlers()
    with events.session("clean-audio-export", sys.argv[1:] if argv is None else argv,
                        args.progress):
        try:
            if args.plan:
                p = plan(ensure_probes(load(args.project)))
                events.emit("result", output=None, sidecar=None, replaced=False, results=p)
                if not events.enabled():
                    print(json.dumps(p, indent=2))
                return
            overrides = overrides_of(args)
            if args.estimate:
                from . import sizing
                sizing.run_estimate(args.project, overrides, args.reencode)
                return
            run(args.project, args.output, args.reencode, overrides)
        except KeyboardInterrupt:
            procs.cleanup_all()
            print_warning("Interrupted — cleaned up.")
            sys.exit(130)
        except (ProjectError, subprocess.CalledProcessError, RuntimeError, OSError,
                ValueError, KeyError) as exc:
            procs.cleanup_all()
            detail = getattr(exc, "stderr", None)
            print_error(f"{exc}" + (f"\n{detail[-1500:]}" if detail else ""))
            sys.exit(1)


if __name__ == "__main__":
    main()
