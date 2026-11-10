"""Audio prepared for the browser's A/B player.

decodeAudioData needs a whole file, so windows are cut and encoded once and
cached under OUTPUT_DIR/_gui/cache/audio/. AAC: what the outputs carry anyway,
and 8× smaller than WAV.
"""
import asyncio
import hashlib
import os
from pathlib import Path

ORIGINAL_BITRATE = "256k"
_locks = {}


def cache_dir(output_dir):
    return Path(output_dir) / "_gui" / "cache" / "audio"


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _stamp(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


async def _ffmpeg(args, dst):
    tmp = dst.with_name(dst.stem + ".partial" + dst.suffix)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-v", "error", *args, str(tmp),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not prepare the audio: {err.decode(errors='replace')}")
    os.replace(tmp, dst)


async def _cached(dst, make):
    if dst.exists():
        return dst
    lock = _locks.setdefault(str(dst), asyncio.Lock())
    async with lock:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            await make(dst)
    return dst


async def output_audio(output_dir, output_path):
    """The output's own audio track (stream copy), or the file itself if it is audio."""
    output_path = Path(output_path)
    if output_path.suffix.lower() in (".wav", ".m4a"):
        return output_path
    dst = cache_dir(output_dir) / f"out_{_key(output_path, _stamp(output_path))}.m4a"
    return await _cached(dst, lambda d: _ffmpeg(
        ["-i", str(output_path), "-vn", "-map", "0:a:0", "-c:a", "copy"], d))


async def original_audio(output_dir, source_path, start_sec, duration_sec):
    """The source's audio for a window, cut exactly as clean-audio cuts it."""
    start = round(float(start_sec or 0.0), 6)
    dur = None if duration_sec is None else round(float(duration_sec), 6)
    dst = cache_dir(output_dir) / \
        f"orig_{_key(source_path, _stamp(source_path), start, dur)}.m4a"
    args = []
    if start > 0:
        args += ["-ss", f"{start:.6f}"]
    args += ["-i", str(source_path), "-vn", "-map", "0:a:0"]
    if dur is not None:
        args += ["-t", f"{dur:.6f}"]
    args += ["-c:a", "aac", "-b:a", ORIGINAL_BITRATE, "-ar", "48000"]
    return await _cached(dst, lambda d: _ffmpeg(args, d))


POSTER_FRAMES = 4          # frames per source, spread across it
POSTER_WIDTH = 320
SCRUB_FRAMES = 12          # the denser set the Media grid shows while hovering a card
SCRUB_WIDTH = 240
POSTER_SETS = {POSTER_FRAMES: POSTER_WIDTH, SCRUB_FRAMES: SCRUB_WIDTH}


def poster_at(duration, index, frames=POSTER_FRAMES):
    """Where frame `index` of `frames` is taken: evenly inside the video, avoiding the very ends."""
    return max(0.0, (duration or 0.0) * (index + 0.5) / frames)


async def poster(output_dir, source_path, source_id, index, duration, frames=POSTER_FRAMES):
    """A JPEG of the source at poster_at(duration, index, frames), cached per source."""
    name = f"{source_id}_{index}" if frames == POSTER_FRAMES else f"{source_id}_{frames}_{index}"
    dst = Path(output_dir) / "_gui" / "cache" / "posters" / f"{name}.jpg"
    at = poster_at(duration, index, frames)
    return await _cached(dst, lambda d: _ffmpeg(
        ["-ss", f"{at:.3f}", "-i", str(source_path), "-map", "0:v:0", "-frames:v", "1",
         "-vf", f"scale={POSTER_SETS[frames]}:-2", "-q:v", "4"], d))


FRAME_WIDTHS = (160, 320)  # filmstrips under piece bars, hover previews


async def frame(output_dir, source_path, source_id, at, width=FRAME_WIDTHS[0]):
    """A JPEG of the source at `at` seconds (to 0.1 s), cached per source, time and width."""
    tenths = max(0, round(at * 10))
    dst = Path(output_dir) / "_gui" / "cache" / "frames" / f"{source_id}_{tenths}_{width}.jpg"
    return await _cached(dst, lambda d: _ffmpeg(
        ["-ss", f"{tenths / 10:.1f}", "-i", str(source_path), "-map", "0:v:0", "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "5"], d))


def cache_size(output_dir):
    folder = cache_dir(output_dir)
    return sum(p.stat().st_size for p in folder.glob("*")) if folder.is_dir() else 0
