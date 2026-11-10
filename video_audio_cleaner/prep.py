"""Editing aids for a source video (docs/gui-design.md §8.1): proxy, thumbnails, peaks.

  python -m video_audio_cleaner.prep proxy|thumbs|peaks SOURCE --out FILE_OR_DIR

proxy   540p H.264, a keyframe every 0.5 s and no B-frames, so any seek decodes a
        handful of small frames: scrubbing is instant. The originals are only read
        again on export.
thumbs  a JPEG every THUMB_INTERVAL seconds, 72 px high, for the timeline's film strip.
peaks   the waveform overview: per 10 ms, the min and max sample as int8 (one hour is
        720 KB). Header: b"VACP", version u8, columns per second u16, columns u32,
        5 reserved bytes (16 bytes in all).
"""
import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

from . import events, ffmpeg_utils, procs
from .console import print_step, print_success

PROXY_HEIGHT = 540
PROXY_GOP = 15
THUMB_INTERVAL = 2.0
THUMB_HEIGHT = 72
PEAKS_PER_SEC = 100
PEAKS_RATE = 8000
PEAKS_MAGIC = b"VACP"
PEAKS_VERSION = 1


def _partial(path):
    path = Path(path)
    return path.with_name(path.stem + ".partial" + path.suffix)


def proxy(source, out, duration=None):
    tmp = _partial(out)
    procs.register_path(str(tmp))
    vf = f"scale=-2:'min({PROXY_HEIGHT},ih)':flags=bicubic,format=yuv420p"
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
         "-c:v", "h264_videotoolbox", "-b:v", "2500k", "-g", str(PROXY_GOP), "-bf", "0",
         "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(tmp)],
        duration, "Proxy")
    os.replace(tmp, out)
    procs.unregister_path(str(tmp))


def thumbs(source, out_dir, duration=None):
    out_dir = Path(out_dir)
    tmp = out_dir.with_name(out_dir.name + ".partial")
    procs.register_path(str(tmp))
    tmp.mkdir(parents=True, exist_ok=True)
    ffmpeg_utils.run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0",
         "-vf", f"fps=1/{THUMB_INTERVAL:g},scale=-2:{THUMB_HEIGHT}", "-q:v", "6",
         "-start_number", "0", str(tmp / "t%05d.jpg")],
        duration, "Thumbnails")
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    os.replace(tmp, out_dir)
    procs.unregister_path(str(tmp))
    (out_dir / "interval.txt").write_text(f"{THUMB_INTERVAL:g}\n")


def compute_peaks(samples, per_column):
    """(min, max) per column of int16 samples, scaled to int8."""
    import numpy as np
    n = len(samples) // per_column
    if n == 0:
        return np.zeros((0, 2), dtype=np.int8)
    blocks = samples[: n * per_column].reshape(n, per_column)
    lo = (blocks.min(axis=1) // 256).astype(np.int8)
    hi = (blocks.max(axis=1) // 256).astype(np.int8)
    return np.stack([lo, hi], axis=1)


def peaks(source, out, duration=None):
    import numpy as np
    per_column = PEAKS_RATE // PEAKS_PER_SEC
    cmd = ["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-ac", "1",
           "-ar", str(PEAKS_RATE), "-f", "s16le", "-"]
    proc = procs.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    from .progress import ProgressReporter
    reporter = ProgressReporter("Waveform", duration)
    chunks, done, carry = [], 0, b""
    try:
        while True:
            data = proc.stdout.read(PEAKS_RATE * 2 * 10)     # 10 s at a time
            if not data:
                break
            data = carry + data
            usable = len(data) - len(data) % (per_column * 2)
            carry = data[usable:]
            chunks.append(compute_peaks(np.frombuffer(data[:usable], dtype="<i2"), per_column))
            done += usable / 2 / PEAKS_RATE
            reporter.update(done)
        proc.wait()
    finally:
        procs.terminate(proc)
    if proc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg could not read the audio of {source}")
    table = np.concatenate(chunks) if chunks else np.zeros((0, 2), dtype=np.int8)
    tmp = _partial(out)
    with open(tmp, "wb") as f:
        f.write(PEAKS_MAGIC + struct.pack("<BHI5x", PEAKS_VERSION, PEAKS_PER_SEC, len(table)))
        f.write(table.tobytes())
    os.replace(tmp, out)
    reporter.finish()


def read_peaks(path):
    """(columns per second, int8 array of shape (n, 2)) from a peaks file."""
    import numpy as np
    data = Path(path).read_bytes()
    if data[:4] != PEAKS_MAGIC:
        raise ValueError(f"{path} is not a peaks file")
    version, per_sec, n = struct.unpack("<BHI5x", data[4:16])
    return per_sec, np.frombuffer(data[16:16 + 2 * n], dtype=np.int8).reshape(n, 2)


KINDS = {"proxy": proxy, "thumbs": thumbs, "peaks": peaks}


def build_parser():
    p = argparse.ArgumentParser(prog="python -m video_audio_cleaner.prep",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kind", nargs="+", choices=tuple(KINDS),
                   help="What to make (several may be given).")
    p.add_argument("--source", required=True, help="The source video (or audio, for peaks).")
    p.add_argument("--cache", required=True,
                   help="Folder holding proxy/, thumbs/ and peaks/; files are named --id.")
    p.add_argument("--id", required=True, help="The source's id (file names in the cache).")
    events.add_argument(p)
    return p


def targets(cache, source_id):
    cache = Path(cache)
    return {"proxy": cache / "proxy" / f"{source_id}.mp4",
            "thumbs": cache / "thumbs" / source_id,
            "peaks": cache / "peaks" / f"{source_id}.peaks"}


def main(argv=None):
    args = build_parser().parse_args(argv)
    procs.install_signal_handlers()
    with events.session("prep", sys.argv[1:] if argv is None else argv, args.progress):
        try:
            duration = ffmpeg_utils.probe_duration(args.source)
            out = targets(args.cache, args.id)
            kinds = list(dict.fromkeys(args.kind))
            for i, kind in enumerate(kinds, 1):
                print_step(i, len(kinds), {"proxy": "Making the editing proxy",
                                           "thumbs": "Making thumbnails",
                                           "peaks": "Reading the waveform"}[kind])
                out[kind].parent.mkdir(parents=True, exist_ok=True)
                KINDS[kind](args.source, out[kind], duration)
                print_success(f"{kind} ready")
        except KeyboardInterrupt:
            procs.cleanup_all()
            sys.exit(130)
        except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
            procs.cleanup_all()
            from .console import print_error
            print_error(str(exc))
            sys.exit(1)


if __name__ == "__main__":
    main()
