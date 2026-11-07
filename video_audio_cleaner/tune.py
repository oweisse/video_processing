"""clean-audio-tune: find good settings for each window of a video.

For every window it renders a grid of candidate settings, scores each against
the untouched source with video_audio_cleaner.metrics, and picks the best one
that keeps the voices intact. Output, in --out_dir:

  report.md        every candidate's numbers per window, the pick marked
  plan.json        the chosen flags per window, ready for clean-audio-batch.
                   Saved after every window; a rerun keeps windows already tuned
                   (same video and grid) and tunes only the new ones. Changing only
                   the scoring criteria rescores the saved measurements instantly.
  listen/*.wav     per window: source → best → runner-up, loudness-matched

Scoring (all thresholds are flags). Each window is judged against its own
gentlest candidate (the one that keeps the most speech fidelity), so a tape that
is noisy throughout is not held to an absolute bar it can never meet:
  1. A candidate qualifies when its speech fidelity is within --fidelity_tolerance
     of the gentlest candidate's, and its warble at most --max_warble_increase
     above the least-warbly candidate's. (--min_fidelity adds an absolute floor;
     off by default.)
  2. Among those, score = dB of hiss removed + --click_weight × dB of ticks removed.

Candidates are measured *before* loudness normalisation. --normalize is applied
the same way to whatever wins, and on quiet tape it compresses dynamics (cleaner
audio needs more gain, so it is compressed more), which would otherwise bias the
scoring against hiss reduction.

The numbers guide; your ears decide. Listen to listen/*.wav before trusting a pick.
"""
import argparse
import contextlib
import io
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import metrics as M
from .timeparse import format_time_filename, parse_time_string

SAMPLE_RATE = 48000
DEFAULT_DECLICK_NR = 12.0   # clean-audio --declick_nr's default
DEFAULT_MUSIC_LEVELS = "off,-30,-25,-20,-15,-10"
DEFAULT_MUSIC_WARBLE_INCREASE = 2.0
# What the recording came from. Tape has hiss and mechanical ticks worth removing;
# on a digital recording the quiet between words is room tone and reverb, and the
# hiss and declick passes only strip it (the scores cannot tell the two apart).
SOURCE_GRIDS = {
    "tape": {"hiss": "off,profile:12,profile:20,patch:0.0003", "declick": "off,12,20"},
    "digital": {"hiss": "off", "declick": "off"},
}
# Bump when the way candidates are measured changes; old measurements are then
# re-rendered rather than compared. 2 = measured before loudness normalisation.
# 3 = declick strength in the grid; with --separate_music, scored against the
# speech stem with no music bed.
# 4 = with --separate_music the music bed level is chosen (see choose_music_level)
# and candidates are scored against speech + that bed: the mix that is heard.
METHOD = 4
_METHOD_CHANGES = {
    3: "it scored music separation without a music bed",
    None: "it was measured the old way (after loudness normalisation)",
    1: "it was measured the old way (after loudness normalisation)",
    2: "it predates declick strengths and speech-stem scoring",
}
_WINDOW = re.compile(r"(\d[\dhms.]*)-(\d[\dhms.]*)(?![a-z])", re.IGNORECASE)
_EXTENSION = re.compile(r"\.[a-z0-9]{2,4}$", re.IGNORECASE)


# ------------------------------------------------------------------ windows
@dataclass
class Window:
    label: str
    start: float
    end: float

    @property
    def duration(self):
        return self.end - self.start


def parse_window(text):
    """A window from '07m00s-08m00s', '1h05m-1h05m25s' or a filename containing one."""
    text = _EXTENSION.sub("", text.strip())
    for match in reversed(list(_WINDOW.finditer(text))):
        try:
            start, end = (parse_time_string(match.group(1)), parse_time_string(match.group(2)))
        except argparse.ArgumentTypeError:
            continue
        if start is None or end is None or end <= start:
            continue
        label = match.group(0)
        if not re.fullmatch(r"\d+m\d{2}s-\d+m\d{2}s", label):
            label = f"{format_time_filename(start)}-{format_time_filename(end)}"
        return Window(label, start, end)
    raise argparse.ArgumentTypeError(f"No START-END window found in '{text}' "
                                     f"(expected e.g. 07m00s-08m00s)")


# ------------------------------------------------------------------ candidates
@dataclass
class Candidate:
    atten: float | None
    hiss: str            # "off", "profile:12", "patch:0.0003"
    declick: float | bool  # False = off, else the declick noise reduction in dB
    separate_music: bool = False
    music_level_db: float | None = None   # bed level with separate_music; None = dropped
    fidelity: float = math.nan
    speech_to_hiss: float = math.nan
    warble: float = math.nan
    clicks: float = math.nan
    hiss_gain_db: float = math.nan
    click_gain_db: float = math.nan
    score: float = math.nan
    qualifies: bool = False
    noise_sample_suspect: bool = False

    def describe(self):
        atten = "none" if self.atten is None else f"{self.atten:g}"
        parts = [f"atten {atten}", "hiss off" if self.hiss == "off" else f"hiss {self.hiss}",
                 f"declick {self.declick_nr:g}" if self.declick else "no declick"]
        if self.separate_music:
            parts.append("music off" if self.music_level_db is None
                         else f"music {self.music_level_db:g} dB")
        return ", ".join(parts)

    @property
    def declick_nr(self):
        # True comes from plans written before strengths were tuned: the default.
        return DEFAULT_DECLICK_NR if self.declick is True else float(self.declick)

    def flags(self, normalize=True, target_lufs=None):
        out = ["--normalize"] if normalize else []
        if normalize and target_lufs is not None:
            out += ["--target_lufs",
                    target_lufs if isinstance(target_lufs, str) else f"{target_lufs:g}"]
        out += ["--atten_lim_db", "none" if self.atten is None else f"{self.atten:g}"]
        if self.separate_music:
            out += ["--separate_music", "--music_level_db",
                    "off" if self.music_level_db is None else f"{self.music_level_db:g}"]
        if self.hiss != "off":
            method, value = self.hiss.split(":")
            out += ["--reduce_hiss", "--hiss_method", method,
                    "--hiss_nr" if method == "profile" else "--hiss_strength", value]
        if self.declick:
            out += ["--declick", "--declick_nr", f"{self.declick_nr:g}"]
        return out


def parse_grid(atten, hiss, declick):
    attens = [None if a.strip().lower() == "none" else float(a) for a in atten.split(",")]
    hisses = [h.strip() for h in hiss.split(",")]
    for h in hisses:
        if h != "off" and not re.fullmatch(r"(profile|patch):[\d.]+", h):
            raise argparse.ArgumentTypeError(
                f"Bad --hiss entry '{h}': use off, profile:NR or patch:STRENGTH")
    declicks = []
    for d in declick.replace(" ", "").lower().split(","):
        if d == "off":
            value = False
        elif d == "on":
            value = DEFAULT_DECLICK_NR
        else:
            try:
                value = float(d)
            except ValueError:
                value = None
            if value is None or value <= 0:
                raise argparse.ArgumentTypeError(
                    f"Bad --declick entry '{d}': use off, on, or a strength in dB (e.g. 20)")
        if value not in declicks:
            declicks.append(value)
    return attens, hisses, declicks


def resolve_source(source):
    """--source as given, or ask: hiss/declick tuning only makes sense for tape."""
    if source:
        return source
    if not sys.stdin.isatty():
        raise SystemExit("Tuning needs to know what the recording came from: pass "
                         "--source tape (Super-8, VHS or other tape transfer) or "
                         "--source digital (camera or phone video).")
    while True:
        answer = input("What did this recording come from?\n"
                       "  [t] tape: Super-8, VHS or another tape transfer "
                       "(tunes hiss and tick removal too)\n"
                       "  [d] digital: a camera or phone video (leaves those off)\n"
                       "Choose t or d (add --source next time to skip this): "
                       ).strip().lower()
        if answer in ("t", "tape"):
            return "tape"
        if answer in ("d", "digital"):
            return "digital"


def parse_music_levels(text):
    """'off,-30,-15' -> [None, -30.0, -15.0], most music removed first."""
    levels = []
    for part in str(text).replace(" ", "").lower().split(","):
        if part == "off":
            value = None
        else:
            try:
                value = float(part)
            except ValueError:
                raise argparse.ArgumentTypeError(
                    f"Bad music level '{part}': use off or a level in dB like -15") from None
        if value not in levels:
            levels.append(value)
    return sorted(levels, key=lambda v: -math.inf if v is None else v)


def choose_music_level(raw, speech, music, sr, levels, max_increase):
    """The quietest music bed that hides Demucs's residue.

    Separated speech on its own warbles audibly more than the original (Demucs
    leaves a watery residue where it cut the music out); a quiet bed of the
    music masks it. Picks the most music removal whose speech + bed mix warbles
    at most max_increase above the original. Returns (level, table, source warble).
    """
    source_warble = M.warble(raw, sr)
    table = []
    for level in levels:
        mix = speech if level is None else speech + music * 10 ** (level / 20)
        table.append({"level_db": level, "warble": M.warble(mix, sr)})
    ok = [t for t in table if t["warble"] <= source_warble + max_increase]
    chosen = ok[0] if ok else min(table, key=lambda t: t["warble"])
    return chosen["level_db"], table, source_warble


def score_candidates(candidates, source, fidelity_tolerance=0.03, max_warble_increase=0.5,
                     click_weight=0.5, min_fidelity=None):
    """Fill in gains, qualification and score; return (best, runner_up).

    Yardsticks are per window: fidelity is compared with the gentlest candidate
    (highest fidelity), warble with the least-warbly one. Anchoring warble to the
    gentlest instead fails when the gentlest is itself warbly (patch mode often is),
    because then every candidate passes.
    """
    gentlest = max(candidates, key=lambda c: c.fidelity)
    # The warble yardstick comes from candidates that keep the voices: a heavy
    # setting that is calm only because it flattened the speech must not set
    # the bar for everyone else.
    faithful = [c for c in candidates if c.fidelity >= gentlest.fidelity - fidelity_tolerance]
    calmest = min(c.warble for c in faithful)
    for c in candidates:
        c.hiss_gain_db = c.speech_to_hiss - source.speech_to_hiss
        c.click_gain_db = 20 * math.log10(max(source.clicks, 0.1) / max(c.clicks, 0.1))
        c.qualifies = (c.fidelity >= gentlest.fidelity - fidelity_tolerance
                       and c.warble <= calmest + max_warble_increase
                       and (min_fidelity is None or c.fidelity >= min_fidelity))
        c.score = c.hiss_gain_db + click_weight * c.click_gain_db
    pool = [c for c in candidates if c.qualifies]
    if pool:
        ranked = sorted(pool, key=lambda c: (c.score, c.fidelity), reverse=True)
    else:
        # Only reachable through --min_fidelity: keep the warble limit, then fidelity.
        calm = [c for c in candidates if c.warble <= calmest + max_warble_increase]
        ranked = sorted(calm or candidates, key=lambda c: c.fidelity, reverse=True)
    best = ranked[0]
    runner_up = next((c for c in ranked[1:]
                      if (c.hiss, c.declick) != (best.hiss, best.declick)), None)
    if runner_up is None and len(ranked) > 1:
        runner_up = ranked[1]
    return best, runner_up


# ------------------------------------------------------------------ rendering
def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def evaluate_window(video, window, grid, separate_music, work, music=None):
    """Render and measure every candidate for one window. Returns (source, candidates).

    music: with separate_music, {"levels": "off,-30,...", "max_warble_increase": 2.0};
    the bed level chosen and its table are written back into it.
    """
    from . import denoise, graph, hiss, postprocess, separation

    raw = os.path.join(work, "raw.wav")
    _ffmpeg("-ss", f"{window.start:.6f}", "-t", f"{window.duration:.6f}", "-i", video,
            "-vn", "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE), raw)
    speech, music_wav, level = raw, None, None
    if separate_music:
        speech, music_wav = os.path.join(work, "vocals.wav"), os.path.join(work, "music.wav")
        _quiet(separation.separate_to_files, raw, speech, music_wav,
               SAMPLE_RATE, duration_sec=window.duration)
        music = music if music is not None else {}
        x_raw, sr = M.load_mono(raw)
        x_speech, _ = M.load_mono(speech)
        x_music, _ = M.load_mono(music_wav)
        n = min(len(x_raw), len(x_speech), len(x_music))
        level, table, source_warble = choose_music_level(
            x_raw[:n], x_speech[:n], x_music[:n], sr,
            parse_music_levels(music.get("levels", DEFAULT_MUSIC_LEVELS)),
            music.get("max_warble_increase", DEFAULT_MUSIC_WARBLE_INCREASE))
        music.update(level_db=level, table=table, source_warble=source_warble)
        spell = lambda v: "off" if v is None else f"{v:g}"
        print(f"  music bed: {spell(level)} dB  (warble: original {source_warble:.1f}; "
              + ", ".join(f"{spell(t['level_db'])} {t['warble']:.1f}" for t in table)
              + ")", flush=True)
        # Score against what will be heard, speech + that bed. Against the
        # music-laden source, removing the music would count as lost fidelity;
        # against the bare speech stem, Demucs's own damage would be invisible.
        ref = x_speech[:n] if level is None else x_speech[:n] + x_music[:n] * 10 ** (level / 20)
    else:
        ref, sr = M.load_mono(raw)
    source = Candidate(atten=None, hiss="off", declick=False,
                       fidelity=1.0, speech_to_hiss=M.speech_to_hiss_db(ref, sr),
                       warble=M.warble(ref, sr), clicks=M.clicks_per_sec(ref, sr))

    attens, hisses, declicks = grid
    candidates = []
    for atten in attens:
        denoised = os.path.join(work, f"dn_{atten}.wav")
        _quiet(denoise.denoise_file, speech, denoised, atten_lim_db=atten)
        noise = hiss.find_quietest_region(denoised, 1.0)
        for h in hisses:
            for dc in declicks:
                c = Candidate(atten=atten, hiss=h, declick=dc, separate_music=separate_music,
                              music_level_db=level)
                # Measured before normalisation: see the module docstring.
                o = graph.GraphOptions(sample_rate=SAMPLE_RATE, normalize=False,
                                       declick=bool(dc), music_level_db=level)
                if dc:
                    o.declick_nr = float(dc)
                if h != "off":
                    method, value = h.split(":")
                    o.hiss_method = method
                    if method == "profile":
                        o.hiss_nr, o.noise_at = float(value), noise.start
                        c.noise_sample_suspect = noise.likely_contains_speech(6.0)
                    else:
                        o.hiss_strength = float(value)
                o.latency = postprocess.measure_latencies(o)
                out = os.path.join(work, "cand.wav")
                _quiet(postprocess.render, o, denoised, music_wav if level is not None else None,
                       out, window.duration, "r")
                x, _ = M.load_mono(out)
                x = x[:len(ref)]
                c.fidelity = M.speech_fidelity(ref, x, sr)
                c.speech_to_hiss = M.speech_to_hiss_db(x, sr)
                c.warble = M.warble(x, sr)
                c.clicks = M.clicks_per_sec(x, sr)
                os.replace(out, os.path.join(work, f"cand_{len(candidates)}.wav"))
                candidates.append(c)
                yield source, c, candidates


def write_listening_file(work, candidates, picks, out_path):
    """source → best → runner-up, each loudness-normalised, 0.7 s of silence between."""
    parts = [os.path.join(work, "raw.wav")]
    parts += [os.path.join(work, f"cand_{candidates.index(p)}.wav") for p in picks if p]
    fmt = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    inputs, chains, order = [], [], []
    for i, path in enumerate(parts):
        inputs += ["-i", path]
        chains.append(f"[{i}:a]loudnorm=I=-16:TP=-1.5,aresample={SAMPLE_RATE},{fmt}[p{i}]")
    gap = len(parts)
    inputs += ["-f", "lavfi", "-t", "0.7", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    chains.append(f"[{gap}:a]{fmt},asplit={max(1, len(parts) - 1)}"
                  + "".join(f"[g{i}]" for i in range(max(1, len(parts) - 1))))
    for i in range(len(parts)):
        order.append(f"[p{i}]")
        if i < len(parts) - 1:
            order.append(f"[g{i}]")
    graph = ";".join(chains) + ";" + "".join(order) + \
        f"concat=n={len(order)}:v=0:a=1[out]"
    _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[out]", "-acodec", "pcm_s24le",
            str(out_path))


# ------------------------------------------------------------------ reports
def _row(c, mark=""):
    return (f"| {mark}{c.describe()} | {c.fidelity:.3f} | {c.hiss_gain_db:+.1f} | "
            f"{c.warble:.1f} | {c.clicks:.1f} | {c.score:+.1f} | "
            f"{'yes' if c.qualifies else 'no'} |")


def render_report(source_video, criteria, results):
    lines = [f"# Tuning report", "",
             f"Source: `{source_video}`  ",
             f"Created: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             "Each window is judged on its own: a candidate qualifies when it loses at most "
             "{fidelity_tolerance} fidelity against the window's gentlest candidate and has "
             "at most {max_warble_increase} more warble than its least-warbly one. Score = dB of hiss removed + "
             "{click_weight} × dB of ticks removed. Measured before loudness "
             "normalisation.".format(**criteria), "",
             "Fidelity: 1.0 = speech structure untouched. Warble: musical-noise flicker "
             "(the source's own value is shown). Listen to `listen/*.wav` before trusting "
             "a pick.", ""]
    for r in results:
        w, src, best = r["window"], r["source"], r["best"]
        lines += [f"## {w.label}  ({w.duration:.0f} s)", ""]
        if not best.qualifies:
            lines += ["**No candidate met the criteria — picked the highest fidelity.**", ""]
        if best.noise_sample_suspect:
            lines += ["⚠ No clear pause found for the hiss profile in this window; "
                      "consider `--noise_profile_at` or patch mode.", ""]
        if r.get("music") and "table" in r["music"]:
            m = r["music"]
            spell = lambda v: "off" if v is None else f"{v:g} dB"
            lines += [f"Music bed: **{spell(m['level_db'])}** — the quietest whose mix "
                      f"warbles at most {m.get('max_warble_increase', DEFAULT_MUSIC_WARBLE_INCREASE):g} "
                      f"above the original ({m['source_warble']:.1f}): "
                      + ", ".join(f"{spell(t['level_db'])} {t['warble']:.1f}" for t in m["table"]),
                      ""]
        lines += [f"Source: hiss {src.speech_to_hiss:.1f} dB, warble {src.warble:.1f}, "
                  f"{src.clicks:.1f} clicks/s. Picked: **{best.describe()}**", ""]
        if r.get("listen_pick") and r["listen_pick"] != best.describe():
            lines += [f"⚠ The listening file was made for an earlier pick "
                      f"({r['listen_pick']}); the criteria changed since. Re-run with "
                      f"--retune to hear the current pick.", ""]
        lines += [
                  f"Listening file: `listen/{w.label}.wav` (source → picked"
                  + (f" → {r['runner_up'].describe()}" if r["runner_up"] else "") + ")", "",
                  "| Settings | Fidelity | Hiss removed dB | Warble | Clicks/s | Score | Qualifies |",
                  "|---|---|---|---|---|---|---|"]
        ranked = sorted(r["candidates"], key=lambda c: (c.qualifies, c.score), reverse=True)
        for c in ranked:
            lines.append(_row(c, "★ " if c is best else ""))
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ main
def build_parser():
    p = argparse.ArgumentParser(
        prog="clean-audio-tune", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", help="Source video.")
    p.add_argument("windows", nargs="*", type=parse_window,
                   help="Windows as START-END (07m00s-08m00s, 1h05m-1h05m25s) or "
                        "filenames containing one.")
    p.add_argument("--windows_file", type=Path,
                   help="A file with one window or filename per line.")
    p.add_argument("--out_dir", required=True, type=Path,
                   help="Where report.md, plan.json and listen/ are written.")
    p.add_argument("--stem", default=None,
                   help="Name prefix for the final outputs in plan.json "
                        "(default: the video's file name).")
    p.add_argument("--atten", default="10,20,30",
                   help="DeepFilterNet attenuation limits to try (default: 10,20,30).")
    p.add_argument("--source", choices=tuple(SOURCE_GRIDS), default=None,
                   help="What the recording came from (asked if not given). 'digital' (a "
                        "camera or phone video) tries no hiss reduction or declick: "
                        "there they only strip the room tone.")
    p.add_argument("--hiss", default=None,
                   help="Hiss settings to try: off, profile:NR, patch:STRENGTH "
                        "(default: off,profile:12,profile:20,patch:0.0003; off for "
                        "--source digital).")
    p.add_argument("--declick", default=None,
                   help="Declick settings to try: off, or a strength in dB like "
                        "clean-audio --declick_nr ('on' = 12). Default: off,12,20; off "
                        "for --source digital.")
    p.add_argument("--separate_music", action="store_true",
                   help="Run Demucs first in every candidate (recordings with music). "
                        "Picks the music bed level too, and scores candidates against "
                        "speech + that bed.")
    p.add_argument("--music_levels", default=DEFAULT_MUSIC_LEVELS,
                   help="With --separate_music: music bed levels to choose from, in dB, "
                        f"'off' = dropped (default: {DEFAULT_MUSIC_LEVELS}). The quietest "
                        "one that passes --max_music_warble_increase wins.")
    p.add_argument("--max_music_warble_increase", type=float,
                   default=DEFAULT_MUSIC_WARBLE_INCREASE,
                   help="With --separate_music: how much more warble than the original "
                        "the speech + bed mix may have. Separated speech alone warbles; a "
                        f"quiet bed masks it (default: {DEFAULT_MUSIC_WARBLE_INCREASE:g}).")
    p.add_argument("--fidelity_tolerance", type=float, default=0.03,
                   help="How much fidelity a candidate may lose relative to the window's "
                        "gentlest candidate (default: 0.03).")
    p.add_argument("--max_warble_increase", type=float, default=0.5,
                   help="How much more warble than the window's least-warbly candidate "
                        "is allowed (default: 0.5).")
    p.add_argument("--min_fidelity", type=float, default=None,
                   help="Optional absolute fidelity floor (default: none).")
    p.add_argument("--click_weight", type=float, default=0.5,
                   help="Weight of tick removal against hiss removal (default: 0.5).")
    p.add_argument("--no_normalize", action="store_true",
                   help="Leave --normalize out of the plan's flags.")
    p.add_argument("--target_lufs", default="auto",
                   type=lambda v: "auto" if v.strip().lower() == "auto" else float(v),
                   help="Loudness target to put in the plan's flags (default: auto, the "
                        "loudest a plain volume change reaches without compression).")
    p.add_argument("--retune", action="store_true",
                   help="Ignore windows already tuned in OUT_DIR/plan.json and redo them.")
    return p


def _window_entry(r, output_flags):
    return {
        "label": r["window"].label,
        "start_sec": r["window"].start,
        "end_sec": r["window"].end,
        "flags": r["best"].flags(**output_flags),
        "listening_file_pick": r.get("listen_pick", r["best"].describe()),
        "met_criteria": r["best"].qualifies,
        "noise_sample_suspect": r["best"].noise_sample_suspect,
        "picked": asdict(r["best"]),
        "runner_up": asdict(r["runner_up"]) if r["runner_up"] else None,
        "source": {k: getattr(r["source"], k) for k in ("speech_to_hiss", "warble", "clicks")},
        "music": r.get("music"),
        "candidates": [asdict(c) for c in r["candidates"]],
    }


def _result_from_entry(e):
    """Rebuild a window's result from plan.json, so reused windows still get reported."""
    def key(c):
        return (c.atten, c.hiss, c.declick)
    candidates = [Candidate(**c) for c in e["candidates"]]
    picked = Candidate(**e["picked"])
    best = next((c for c in candidates if key(c) == key(picked)), picked)
    runner = None
    if e.get("runner_up"):
        r = Candidate(**e["runner_up"])
        runner = next((c for c in candidates if key(c) == key(r)), r)
    src = e["source"]
    source = Candidate(atten=None, hiss="off", declick=False, fidelity=1.0,
                       speech_to_hiss=src["speech_to_hiss"], warble=src["warble"],
                       clicks=src["clicks"])
    return dict(window=Window(e["label"], e["start_sec"], e["end_sec"]), source=source,
                candidates=candidates, best=best, runner_up=runner,
                listen_pick=e.get("listening_file_pick"), music=e.get("music"))


def _reusable(previous, video, grid_spec):
    """Measured windows from an earlier run that can stand: same video, grid, method.

    Criteria are not compared: the measurements are saved, so a criteria change
    only means rescoring them.
    """
    if not previous:
        return {}, None
    if os.path.abspath(previous.get("source", "")) != video:
        return {}, "it was tuned on a different video"
    if previous.get("method") != METHOD:
        return {}, _METHOD_CHANGES.get(previous.get("method"),
                                       "it was measured with a different method")
    if previous.get("grid") != grid_spec:
        return {}, "the candidate grid changed"
    return {w["label"]: w for w in previous.get("windows", [])}, None


def _save(out_dir, video, stem, criteria, grid_spec, output_flags, argv, results):
    plan = {
        "schema": 1,
        "method": METHOD,
        "source": video,
        "stem": stem,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tune_command": shlex.join(["clean-audio-tune",
                                    *(argv if argv is not None else sys.argv[1:])]),
        "criteria": criteria,
        "grid": grid_spec,
        "output_flags": output_flags,
        "windows": [_window_entry(r, output_flags) for r in results],
    }
    tmp = out_dir / "plan.json.partial"
    tmp.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / "plan.json")
    (out_dir / "report.md").write_text(render_report(video, criteria, results) + "\n",
                                       encoding="utf-8")


def default_tune_dir(output_dir, video, separate_music=False):
    """Per-video tuning cache under an output folder, shared by all three tools.

    Music separation changes every measurement, so it gets its own cache.
    """
    name = Path(video).stem + ("-with-music" if separate_music else "")
    return Path(output_dir) / "_tuning" / name


def default_grid_and_criteria(separate_music=False, source="tape"):
    d = build_parser().parse_args(["x", "--out_dir", "x"])
    grid_spec = {"atten": d.atten, **SOURCE_GRIDS[source], "source": source,
                 "separate_music": separate_music, "music_levels": d.music_levels,
                 "max_music_warble_increase": d.max_music_warble_increase}
    criteria = dict(fidelity_tolerance=d.fidelity_tolerance,
                    max_warble_increase=d.max_warble_increase,
                    click_weight=d.click_weight, min_fidelity=d.min_fidelity)
    return grid_spec, criteria


def _same_window(entry, w):
    return (entry["label"] == w.label or
            (abs(entry["start_sec"] - w.start) < 1e-3 and abs(entry["end_sec"] - w.end) < 1e-3))


def _archive(plan_path, why):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = plan_path.with_name(f"plan.{stamp}.json")
    os.replace(plan_path, kept)
    print(f"Earlier tuning kept as {kept.name} ({why}).")


def _print_candidate(n, total, source, c):
    print(f"  {n:2d}/{total}  {c.describe():42s} "
          f"fidelity {c.fidelity:.3f}  hiss {c.speech_to_hiss - source.speech_to_hiss:+5.1f} dB  "
          f"warble {c.warble:4.1f}  clicks {c.clicks:4.1f}/s", flush=True)


def run(video, windows, out_dir, stem, grid_spec, criteria, output_flags,
        retune=False, argv=None):
    """Tune `windows` into out_dir/plan.json, reusing what is already measured there.

    Returns (results, tuned): results for every window in the plan (not only the
    requested ones), and the labels of the windows that were measured just now.
    """
    video = os.path.abspath(video)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    listen = out_dir / "listen"
    listen.mkdir(exist_ok=True)
    grid = parse_grid(grid_spec["atten"], grid_spec["hiss"], grid_spec["declick"])
    per_window = len(grid[0]) * len(grid[1]) * len(grid[2])

    previous = None
    plan_path = out_dir / "plan.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8"))
        stem = stem or previous.get("stem")
    reusable, why_not = _reusable(previous, video, grid_spec)
    if previous and (retune or why_not):
        # Never overwrite measurements that can't be merged: keep them aside.
        _archive(plan_path, "--retune" if retune else why_not)
        reusable = {}
    stem = stem or Path(video).stem

    def rescored(entry):
        r = _result_from_entry(entry)
        r["best"], r["runner_up"] = score_candidates(r["candidates"], r["source"], **criteria)
        return r

    # Windows already measured are kept and rescored under the current criteria;
    # a requested window matches a measured one by label or by its start and end.
    results, todo, used = [], [], set()
    for w in windows:
        hit = next((label for label, e in reusable.items() if _same_window(e, w)), None)
        if hit:
            results.append(rescored(reusable[hit]))
            used.add(hit)
        else:
            todo.append(w)
    results = [rescored(e) for label, e in reusable.items() if label not in used] + results
    if used:
        print(f"Reusing earlier tuning for {len(used)} window(s) from {plan_path} "
              f"(--retune to redo).")
    if not todo:
        _save(out_dir, video, stem, criteria, grid_spec, output_flags, argv, results)
        print("Nothing new to tune; picks rescored with the current criteria.")
        return results, set()

    total_audio = sum(w.duration for w in todo)
    print(f"Tuning {len(todo)} window(s), {total_audio / 60:.1f} min of audio, "
          f"{per_window} candidates each ({len(grid[0])} DeepFilterNet runs per window). "
          f"Progress is saved after every window.")
    for wi, w in enumerate(todo, 1):
        print(f"\n[{wi}/{len(todo)}] {w.label}  ({w.duration:.0f} s)")
        with tempfile.TemporaryDirectory(prefix="vac_tune_") as work:
            t0 = time.time()
            done = []
            music = {"levels": grid_spec.get("music_levels", DEFAULT_MUSIC_LEVELS),
                     "max_warble_increase": grid_spec.get("max_music_warble_increase",
                                                          DEFAULT_MUSIC_WARBLE_INCREASE)}
            for source, c, done in evaluate_window(video, w, grid,
                                                   grid_spec["separate_music"], work, music):
                _print_candidate(len(done), per_window, source, c)
            best, runner_up = score_candidates(done, source, **criteria)
            write_listening_file(work, done, [best, runner_up], listen / f"{w.label}.wav")
        tag = "" if best.qualifies else "  (nothing met --min_fidelity: highest fidelity)"
        print(f"  ★ {best.describe()}{tag}   [{time.time() - t0:.0f}s]")
        results.append(dict(window=w, source=source, candidates=done, best=best,
                            runner_up=runner_up,
                            music=music if grid_spec["separate_music"] else None))
        _save(out_dir, video, stem, criteria, grid_spec, output_flags, argv, results)
    return results, {w.label for w in todo}


def autotune_window(video, start, end, tune_dir, separate_music=False, music_levels=None,
                    source="tape"):
    """Best settings for one window, from the cache or by tuning it now.

    Adopts the cache's grid and criteria when it has compatible measurements, so
    one-off windows and clean-audio-tune runs share one memory per video.
    Returns (picked Candidate, window entry dict, reused flag, plan path).
    """
    video = os.path.abspath(video)
    tune_dir = Path(tune_dir)
    plan_path = tune_dir / "plan.json"
    grid_spec, criteria = default_grid_and_criteria(separate_music, source)
    if music_levels is not None:      # the user fixed the bed level
        grid_spec["music_levels"] = music_levels
    output_flags = dict(normalize=True, target_lufs="auto")
    stem = None
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8"))
        if (os.path.abspath(previous.get("source", "")) == video
                and previous.get("method") == METHOD
                and (previous.get("grid") or {}).get("separate_music") == separate_music
                and previous["grid"].get("source", "tape") == source
                and (music_levels is None
                     or previous["grid"].get("music_levels") == music_levels)):
            grid_spec = previous["grid"]
            criteria = previous.get("criteria", criteria)
            output_flags = previous.get("output_flags", output_flags)
            stem = previous.get("stem")
    window = Window(f"{format_time_filename(start)}-{format_time_filename(end)}", start, end)

    results, tuned = run(video, [window], tune_dir, stem, grid_spec, criteria, output_flags)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    entry = next(e for e in plan["windows"] if _same_window(e, window))
    r = next(r for r in results if r["window"].label == entry["label"])
    return r["best"], entry, window.label not in tuned, plan_path


def main(argv=None):
    args = build_parser().parse_args(argv)
    windows = list(args.windows)
    if args.windows_file:
        windows += [parse_window(line) for line in
                    args.windows_file.read_text().splitlines() if line.strip()]
    if not windows:
        sys.exit("Give at least one window (positional or --windows_file).")
    args.source = resolve_source(args.source)
    args.hiss = args.hiss or SOURCE_GRIDS[args.source]["hiss"]
    args.declick = args.declick or SOURCE_GRIDS[args.source]["declick"]
    grid_spec = {"atten": args.atten, "hiss": args.hiss, "declick": args.declick,
                 "source": args.source, "separate_music": args.separate_music, "music_levels": args.music_levels,
                 "max_music_warble_increase": args.max_music_warble_increase}
    parse_grid(args.atten, args.hiss, args.declick)   # validate before any work
    parse_music_levels(args.music_levels)
    criteria = dict(fidelity_tolerance=args.fidelity_tolerance,
                    max_warble_increase=args.max_warble_increase,
                    click_weight=args.click_weight, min_fidelity=args.min_fidelity)
    output_flags = dict(normalize=not args.no_normalize, target_lufs=args.target_lufs)

    started = time.time()
    run(args.video, windows, args.out_dir, args.stem, grid_spec, criteria, output_flags,
        retune=args.retune, argv=argv)
    plan_path = args.out_dir / "plan.json"
    print(f"\nDone in {(time.time() - started) / 60:.1f} min.")
    print(f"  Report:    {args.out_dir / 'report.md'}")
    print(f"  Listen:    {args.out_dir / 'listen'}/")
    print(f"  Plan:      {plan_path}")
    print(f"  Render with:  clean-audio-batch {plan_path} --output_dir <DIR>")


if __name__ == "__main__":
    main()
