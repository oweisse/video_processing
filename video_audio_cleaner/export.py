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

from . import events, ffmpeg_utils, procs
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


def transition(clip):
    tr = clip.get("transition_in") or {}
    return float(tr.get("sec") or 0.0) if tr.get("type") == "crossfade" else 0.0


def duration(project):
    cs = clips(project)
    return 0.0 if not cs else cs[-1][1] + cs[-1][2]


def source_of(project, clip):
    src = project.get("sources", {}).get(clip["source"])
    if not src:
        raise ProjectError(f"clip {clip.get('id')} uses an unknown source {clip['source']}")
    return src


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
    return {"width": int(video["width"]), "height": int(video["height"]),
            "fps": float(video.get("fps") or 30)}


def settings(project):
    return {**DEFAULT_EXPORT, **(project.get("export") or {})}


# ------------------------------------------------------------------ copy or encode
def plan(project, keyframe_at=ffmpeg_utils.probe_keyframe_at_or_before):
    """{"mode": "copy"|"encode", "reasons": [...why not copy...], "duration", "canvas"}."""
    cs = clips(project)
    if not cs:
        raise ProjectError("the project has no clips")
    cv = canvas(project)
    reasons = []
    ids = {c["source"] for c, _, _ in cs}
    if len(ids) > 1:
        reasons.append("clips come from more than one video")
    if any(transition(c) for c, _, _ in cs[1:]):
        reasons.append("crossfades need re-encoding")
    if any(c.get("video_filters") for c, _, _ in cs):
        reasons.append("visual edits need re-encoding")
    if len(ids) == 1:
        probe = (source_of(project, cs[0][0]).get("probe") or {}).get("video") or {}
        if (probe.get("width"), probe.get("height")) != (cv["width"], cv["height"]):
            reasons.append(f"the canvas ({cv['width']}×{cv['height']}) differs from the "
                           f"video ({probe.get('width')}×{probe.get('height')})")
        if probe.get("fps") and abs(float(probe["fps"]) - cv["fps"]) > 0.01:
            reasons.append(f"the frame rate changes ({probe['fps']:g} → {cv['fps']:g})")
        if probe.get("field_order") not in (None, "progressive", "unknown"):
            reasons.append("the video is interlaced and gets deinterlaced")
        if not reasons:
            path = source_of(project, cs[0][0])["path"]
            off = []
            for c, _, _ in cs:
                start = float(c["in_sec"])
                if start <= 0:
                    continue
                k = keyframe_at(path, start)
                if k is None or abs(k - start) > KEYFRAME_TOLERANCE:
                    off.append(c.get("id"))
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
    labels = []
    for i, (c, _, length) in enumerate(cs):
        audio = c.get("audio") or {}
        gain = float(audio.get("gain_db") or 0.0)
        if audio.get("mute"):
            idx = inputs.add(f"anullsrc=r={SAMPLE_RATE}:cl=stereo", length=length, lavfi=True)
        elif audio.get("file"):
            offset = float(c["in_sec"]) - float(audio.get("start_sec") or 0.0)
            if offset < -0.05:
                raise ProjectError(f"clip {c.get('id')} starts before its cleaned audio")
            idx = inputs.add(audio["file"], max(0.0, offset), length)
        else:
            idx = (video_inputs or {}).get(i)
            if idx is None:
                idx = inputs.add(source_of(project, c)["path"], float(c["in_sec"]), length)
        chains.append(f"[{idx}:a:0]asetpts=PTS-STARTPTS,{AFMT},apad,"
                      f"atrim=duration={length:.6f},volume={gain:g}dB[a{i}]")
        labels.append(f"a{i}")
    # Join like the picture: hard cuts concatenate, crossfades overlap.
    cur = labels[0]
    for i in range(1, len(cs)):
        fade = min(transition(cs[i][0]), cs[i - 1][2] / 2, cs[i][2] / 2)
        nxt = f"aj{i}"
        if fade > 0:
            chains.append(f"[{cur}][{labels[i]}]acrossfade=d={fade:.6f}:c1=tri:c2=tri[{nxt}]")
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


def video_graph(project, inputs, cv):
    """Per-clip picture chains joined into [video]; returns (chains, {clip: input})."""
    chains, used = [], {}
    cs = clips(project)
    w, h, fps = cv["width"], cv["height"], cv["fps"]
    for i, (c, _, length) in enumerate(cs):
        idx = inputs.add(source_of(project, c)["path"], float(c["in_sec"]), length)
        used[i] = idx
        steps = ["setpts=PTS-STARTPTS"]
        if _deinterlace(project, c):
            steps.append("bwdif=mode=send_frame")
        steps += list(c.get("video_filters") or [])
        steps += [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1",
                  f"fps={fps:g}", "format=yuv420p", f"trim=duration={length:.6f}",
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


def encoder_args(s, cv):
    if s.get("encoder") == "h264_videotoolbox":
        rate = next(r for limit, r in FAST_BITRATES if cv["height"] <= limit)
        return ["-c:v", "h264_videotoolbox", "-b:v", rate, "-profile:v", "high",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", str(int(s.get("crf", 18))), "-preset",
            str(s.get("preset", "medium")), "-profile:v", "high", "-pix_fmt", "yuv420p"]


def measure_command(project, s):
    inputs = Inputs()
    chains, label = audio_graph(project, inputs)
    graph = ";".join(chains) + f";[{label}]ebur128=peak=true[out]"
    return ["ffmpeg", "-y", "-hide_banner", "-nostats", *inputs.args,
            "-filter_complex", graph, "-map", "[out]", "-f", "null", "-"]


def encode_command(project, s, out, measured, cv):
    inputs = Inputs()
    vchains, used = video_graph(project, inputs, cv)
    achains, label = audio_graph(project, inputs, video_inputs=used)
    tail = f"[{label}]{loudness(s, measured)}[aout]"
    graph = ";".join(vchains + achains + [tail])
    return ["ffmpeg", "-y", *inputs.args, "-filter_complex", graph,
            "-map", "[video]", "-map", "[aout]", *encoder_args(s, cv),
            "-c:a", "aac", "-b:a", str(s["audio_bitrate"]), "-ar", str(SAMPLE_RATE),
            "-ac", "2", "-t", f"{duration(project):.6f}", "-movflags", "+faststart", str(out)]


def concat_list(project):
    lines = ["ffconcat version 1.0"]
    for c, _, _ in clips(project):
        path = source_of(project, c)["path"].replace("'", "'\\''")
        lines += [f"file '{path}'", f"inpoint {float(c['in_sec']):.6f}",
                  f"outpoint {float(c['out_sec']):.6f}"]
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


# ------------------------------------------------------------------ running it
def output_path(project, project_path):
    s = settings(project)
    if s.get("output"):
        return Path(os.path.expanduser(s["output"]))
    name = project.get("name") or Path(project_path).parent.name
    # Projects live in OUTPUT_DIR/_projects/<name>/; exports go to OUTPUT_DIR.
    return Path(project_path).resolve().parent.parent.parent / f"{name}.mp4"


def run(project_path, out=None, force_encode=False):
    project = load(project_path)
    s = settings(project)
    out = Path(out) if out else output_path(project, project_path)
    for c, _, _ in clips(project):
        for path in (source_of(project, c)["path"], (c.get("audio") or {}).get("file")):
            if path and not os.path.exists(path):
                raise ProjectError(f"missing file: {path}")
    total_steps = 3
    print_step(1, total_steps, "Checking the cuts")
    p = plan(project)
    if force_encode and p["mode"] == "copy":
        p = {**p, "mode": "encode", "reasons": ["re-encoding was asked for"]}
    total = p["duration"]
    if p["mode"] == "copy":
        print_success(f"Lossless: the video is copied as is ({total:.1f} s).")
    else:
        print_info("Re-encoding the video: " + "; ".join(p["reasons"]) + ".")

    print_step(2, total_steps, "Measuring loudness")
    cmd = measure_command(project, s)
    from .postprocess import parse_ebur128_summary
    measured = parse_ebur128_summary(ffmpeg_utils.run_ffmpeg(cmd, total, "Measuring"))
    if measured[0] is None:
        print_warning("The audio is near silent; loudness is left as it is.")
    else:
        print_info(f"Measured {measured[0]:.1f} LUFS (true peak {measured[1]:.1f} dBTP); "
                   f"gain {gain_db(s, measured):+.1f} dB to reach "
                   f"{float(s['target_lufs']):g} LUFS.")

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial" + out.suffix)
    procs.register_path(str(partial))
    work = tempfile.mkdtemp(prefix="vac_export_")
    procs.register_path(work)
    try:
        if p["mode"] == "copy":
            print_step(3, total_steps, "Writing (video copied, audio encoded)")
            audio_cmd, mux = copy_commands(project, s, partial, measured, work)
            ffmpeg_utils.run_ffmpeg(audio_cmd, total, "Audio")
            ffmpeg_utils.run_ffmpeg(mux, total, "Writing")
            commands = [audio_cmd, mux]
        else:
            print_step(3, total_steps, f"Encoding {p['canvas']['width']}×"
                                       f"{p['canvas']['height']} ({s['encoder']})")
            cmd = encode_command(project, s, partial, measured, p["canvas"])
            ffmpeg_utils.run_ffmpeg(cmd, total, "Encoding")
            commands = [cmd]
        os.replace(partial, out)
        procs.unregister_path(str(partial))
    finally:
        procs.unregister_path(work)
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    result = {"mode": p["mode"], "reasons": p["reasons"], "duration_sec": round(total, 3),
              "canvas": p["canvas"], "measured_lufs": measured[0],
              "measured_true_peak_dbtp": measured[1], "gain_db": round(gain_db(s, measured), 2),
              "target_lufs": float(s["target_lufs"])}
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
    return out, result


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
    events.add_argument(p)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    procs.install_signal_handlers()
    with events.session("clean-audio-export", sys.argv[1:] if argv is None else argv,
                        args.progress):
        try:
            if args.plan:
                p = plan(load(args.project))
                events.emit("result", output=None, sidecar=None, replaced=False, results=p)
                if not events.enabled():
                    print(json.dumps(p, indent=2))
                return
            run(args.project, args.output, args.reencode)
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
