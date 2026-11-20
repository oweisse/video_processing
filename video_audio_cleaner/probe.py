"""What a media file is: codecs, picture size, frame rate, keyframe spacing.

Used by the GUI to describe sources and, later, to decide whether a cut can be
stream-copied. `python -m video_audio_cleaner.probe FILE` prints it as JSON.
"""
import hashlib
import json
import os
import subprocess
import sys

KEYFRAME_SAMPLE_SEC = 60
# Bumped when the result gains fields; caches holding an older probe are probed again.
PROBE_VERSION = 2


def source_id(path):
    """Stable id for a file: path, size and mtime (hashing 12 GB would take too long)."""
    path = os.path.abspath(path)
    st = os.stat(path)
    return hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


def _ffprobe(*args, timeout=60):
    out = subprocess.run(["ffprobe", "-v", "error", *args], capture_output=True, text=True,
                         timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()}")
    return out.stdout


def _rate(text):
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
        return round(value, 3) if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _sar(text):
    """Sample (pixel) aspect ratio as a float; 1.0 when square, missing or undefined."""
    try:
        num, _, den = str(text).partition(":")
        value = float(num) / float(den)
        return round(value, 6) if value > 0 else 1.0
    except (ValueError, ZeroDivisionError):
        return 1.0


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def keyframe_stats(path, sample_sec=KEYFRAME_SAMPLE_SEC):
    """Mean and largest keyframe spacing over the first sample_sec, or None."""
    out = _ffprobe("-select_streams", "v:0", "-skip_frame", "nokey",
                   "-show_entries", "frame=pts_time", "-of", "csv=p=0",
                   "-read_intervals", f"%+{sample_sec}", path)
    times = []
    for line in out.splitlines():
        try:
            times.append(float(line.strip().rstrip(",")))
        except ValueError:
            continue
    if len(times) < 2:
        return None
    gaps = [b - a for a, b in zip(times, times[1:])]
    return {"sampled_sec": sample_sec, "mean_interval_sec": round(sum(gaps) / len(gaps), 3),
            "max_interval_sec": round(max(gaps), 3)}


def probe(path):
    path = os.path.abspath(path)
    info = json.loads(_ffprobe("-show_data_hash", "md5", "-show_entries",
                               "format=duration:format_tags=creation_time:"
                               "stream=codec_type,codec_name,width,height,"
                               "r_frame_rate,avg_frame_rate,field_order,pix_fmt,bit_rate,"
                               "sample_rate,channels,sample_aspect_ratio,profile,level,"
                               "time_base,extradata_hash",
                               "-of", "json", path))
    st = os.stat(path)
    result = {"probe_version": PROBE_VERSION, "path": path, "id": source_id(path),
              "size": st.st_size, "mtime": st.st_mtime, "duration_sec": None,
              "creation_time": (info.get("format", {}).get("tags") or {}).get("creation_time"),
              "video": None, "audio": None, "keyframes": None}
    try:
        result["duration_sec"] = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and result["video"] is None:
            result["video"] = {
                "codec": s.get("codec_name"), "width": _int(s.get("width")),
                "height": _int(s.get("height")),
                "fps": _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate")),
                "field_order": s.get("field_order") or "unknown",
                "pix_fmt": s.get("pix_fmt"), "bit_rate": _int(s.get("bit_rate")),
                "sar": _sar(s.get("sample_aspect_ratio")), "profile": s.get("profile"),
                "level": _int(s.get("level")), "time_base": s.get("time_base"),
                # Identical decoder setup is what lets two files be joined losslessly.
                "extradata_md5": (s.get("extradata_hash") or "").removeprefix("MD5:") or None}
        elif s.get("codec_type") == "audio" and result["audio"] is None:
            result["audio"] = {"codec": s.get("codec_name"),
                               "sample_rate": _int(s.get("sample_rate")),
                               "channels": _int(s.get("channels")),
                               "bit_rate": _int(s.get("bit_rate"))}
    if result["video"]:
        try:
            result["keyframes"] = keyframe_stats(path)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    return result


def main(argv=None):
    for path in (argv if argv is not None else sys.argv[1:]):
        print(json.dumps(probe(path), indent=2))


if __name__ == "__main__":
    main()
