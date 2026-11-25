"""clean-audio-subtitles: local subtitles for Hebrew/English videos (docs/subtitles-plan.md).

  setup        what is installed and what is missing; --download whisper|ivrit|translate
  transcribe   a stretch of a video → the mixed track of its subtitle document
  translate    the mixed track → English only or Hebrew only
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import events, procs, subs
from .console import print_error, print_info, print_success, print_warning

TRANSLATE_MODEL = "gemma3:27b"


# ------------------------------------------------------------------ setup
def check():
    """{piece: {ok, detail, command}} for everything subtitles need."""
    from . import transcribe as T
    out = {}
    wb = T.whisper_bin()
    out["whisper_cpp"] = {"ok": bool(wb), "label": "Speech recognition program (whisper.cpp)",
                          "detail": wb or "not installed", "command": "brew install whisper-cpp"}
    for key, name in (("whisper_model", "large-v3"), ("ivrit_model", "ivrit")):
        p = T.model_path(name)
        out[key] = {"ok": p.is_file(), "label": T.MODELS[name]["label"],
                    "detail": f"{p} ({p.stat().st_size / 1e9:.1f} GB)" if p.is_file() else
                    f"missing ({T.MODELS[name]['size_gb']} GB to download)",
                    "command": f"clean-audio-subtitles setup --download {'whisper' if name == 'large-v3' else 'ivrit'}",
                    "optional": name == "ivrit"}
    try:
        import silero_vad  # noqa: F401
        vad = {"ok": True, "detail": "installed"}
    except ImportError:
        vad = {"ok": False, "detail": "not installed; a simpler speech detector is used"}
    out["vad"] = {**vad, "label": "Speech detection (Silero VAD)", "optional": True,
                  "command": "./venv/bin/pip install silero-vad"}
    ob = shutil.which("ollama")
    out["ollama"] = {"ok": bool(ob), "label": "Translation program (Ollama)", "detail": ob or "not installed",
                     "command": "brew install ollama"}
    model_ok = False
    if ob:
        try:
            from . import translate
            model_ok = translate.model_installed(TRANSLATE_MODEL)
        except Exception:  # noqa: BLE001
            model_ok = False
    out["translate_model"] = {"ok": model_ok, "label": f"Translation model ({TRANSLATE_MODEL})",
                              "detail": "installed" if model_ok else "missing (about 17 GB to download)",
                              "command": "clean-audio-subtitles setup --download translate"}
    out["ready"] = {"transcribe": out["whisper_cpp"]["ok"] and out["whisper_model"]["ok"],
                    "translate": out["ollama"]["ok"] and model_ok}
    return out


def _download(url, dest, label):
    from .progress import ProgressReporter
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".partial")
    procs.register_path(str(part))
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0) + have
        rep = ProgressReporter(f"Downloading {label}", total / 1e6 if total else None)
        with open(part, "ab" if have and r.status == 206 else "wb") as f:
            done = have if r.status == 206 else 0
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                rep.update(done / 1e6, speed=None)
        rep.finish()
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for buf in iter(lambda: f.read(1 << 22), b""):
            h.update(buf)
    part.replace(dest)
    procs.unregister_path(str(part))
    return h.hexdigest()


def setup(args):
    from . import transcribe as T
    if args.download in ("whisper", "ivrit"):
        name = "large-v3" if args.download == "whisper" else "ivrit"
        m = T.MODELS[name]
        dest = T.model_path(name)
        if dest.is_file():
            print_success(f"{m['label']} is already there: {dest}")
        else:
            sha = _download(m["url"], dest, m["label"])
            reg = T.models_dir() / "models.json"
            data = json.loads(reg.read_text()) if reg.exists() else {}
            data[m["file"]] = {"url": m["url"], "sha256": sha, "downloaded": subs.now()}
            reg.write_text(json.dumps(data, indent=2))
            print_success(f"Downloaded {m['label']}")
    elif args.download == "translate":
        from . import translate
        translate.pull(TRANSLATE_MODEL)
        print_success(f"Downloaded {TRANSLATE_MODEL}")
    report = check()
    if events.enabled():
        events.emit("result", output=None, results=report)
    else:
        for key, v in report.items():
            if key == "ready":
                continue
            mark = "✓" if v["ok"] else ("–" if v.get("optional") else "✗")
            print(f"{mark} {v['label']}: {v['detail']}" + ("" if v["ok"] else f"\n    {v['command']}"))
    if not report["ready"]["transcribe"] and not args.download:
        sys.exit(2)


# ------------------------------------------------------------------ transcribe / translate
def cmd_transcribe(args):
    from . import transcribe as T
    gl = subs.load_glossary(args.output_dir)
    dur = None
    try:
        from . import ffmpeg_utils
        dur = ffmpeg_utils.probe_duration(args.source)
    except Exception:  # noqa: BLE001
        pass
    T.transcribe(args.output_dir, args.id, args.source, args.start, args.end, replace=args.replace,
                 model=args.model, language=args.language, audio=args.audio, audio_start=args.audio_start,
                 channel=args.channel, duration=dur, glossary=gl)


def cmd_translate(args):
    from . import translate
    translate.translate(args.output_dir, args.id, args.to, start=args.start, end=args.end, mode=args.mode,
                        model=args.model, replace_edited=args.replace_edited,
                        context=json.loads(args.context) if args.context else None)


def build_parser():
    p = argparse.ArgumentParser(prog="clean-audio-subtitles", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("setup", help="Check (and download) what subtitles need.")
    s.add_argument("--download", choices=("whisper", "ivrit", "translate"))
    events.add_argument(s)
    t = sp.add_parser("transcribe", help="Transcribe a stretch of a video.")
    t.add_argument("source")
    t.add_argument("--output_dir", required=True)
    t.add_argument("--id", required=True, help="The source's library id.")
    t.add_argument("--start", type=float, required=True)
    t.add_argument("--end", type=float, required=True)
    t.add_argument("--replace", choices=("missing", "all"), default="missing")
    t.add_argument("--model", choices=("large-v3", "ivrit"), default="large-v3")
    t.add_argument("--language", choices=("auto", "he", "en"), default="auto")
    t.add_argument("--audio", help="A cleaned output to listen to instead of the original sound.")
    t.add_argument("--audio_start", type=float, default=0.0, help="Where --audio starts in the source.")
    t.add_argument("--channel", choices=("mix", "left", "right"), default="mix")
    events.add_argument(t)
    r = sp.add_parser("translate", help="Translate the mixed track into one language.")
    r.add_argument("--output_dir", required=True)
    r.add_argument("--id", required=True)
    r.add_argument("--to", choices=("en", "he"), required=True)
    r.add_argument("--start", type=float)
    r.add_argument("--end", type=float)
    r.add_argument("--mode", choices=("missing", "stale", "all"), default="missing")
    r.add_argument("--model", default=TRANSLATE_MODEL)
    r.add_argument("--replace_edited", action="store_true",
                   help="With --mode all: also replace translations edited by hand.")
    r.add_argument("--context", help='JSON {"before": [{"source", "translation"}], "after": "text"}: the '
                   "lines around the stretch in the edit, used as context instead of the video's own.")
    events.add_argument(r)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    procs.install_signal_handlers()
    with events.session(f"subtitles {args.cmd}", sys.argv[1:] if argv is None else argv, args.progress):
        try:
            {"setup": setup, "transcribe": cmd_transcribe, "translate": cmd_translate}[args.cmd](args)
        except KeyboardInterrupt:
            procs.cleanup_all()
            sys.exit(130)
        except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
            procs.cleanup_all()
            print_error(str(exc))
            sys.exit(1)


if __name__ == "__main__":
    main()
