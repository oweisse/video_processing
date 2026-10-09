#!/usr/bin/env python3
"""Regenerate the measurement tables in README.md.

Runs the real CLI end to end (--filter_audio_only) for each configuration and
scores the result against the untouched source with video_audio_cleaner.metrics.

    ./venv/bin/python scripts/readme_numbers.py SPEECH_VIDEO TICK_VIDEO

SPEECH_VIDEO is used at 7m00s for 60 s (quiet home-video speech with hiss);
TICK_VIDEO at 8m50s for 20 s (film-transfer projector tick). The README's
tables were produced from the owner's 04-random_chat_w_dad.mp4 and
02-wedding.mp4.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from video_audio_cleaner import metrics as M

CLEAN = Path(sys.executable).with_name("clean-audio")


def source(video, start, length, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(length),
                    "-i", video, "-vn", "-ac", "1", "-acodec", "pcm_f32le", "-ar", "48000",
                    out], check=True)
    return M.load_mono(out)


def clean(video, start, length, workdir, *flags):
    out = Path(tempfile.mkdtemp(dir=workdir))
    subprocess.run([str(CLEAN), video, "--output_dir", str(out), "--filter_audio_only",
                    "--skip_first_n_sec", str(start), "--process_n_seconds", str(length),
                    *flags], check=True, capture_output=True, text=True)
    return M.load_mono(next(out.glob("*.wav")))[0]


def table(header, rows):
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()


def main(speech_video, tick_video):
    work = tempfile.mkdtemp(prefix="readme_numbers_")
    raw, sr = source(speech_video, 420, 60, os.path.join(work, "speech.wav"))

    def score(x):
        return (f"{M.speech_fidelity(raw, x, sr):.3f}", f"{M.speech_to_hiss_db(x, sr):.1f} dB",
                f"{M.warble(x, sr):.1f}")

    def run(*flags):
        return score(clean(speech_video, 420, 60, work, *flags))

    src = score(raw)
    a10 = run("--atten_lim_db", "10")
    print("### Hiss: attenuation vs --reduce_hiss  (speech video, 7m00s, 60 s)\n")
    table(["Setting", "Speech fidelity", "Speech-to-hiss", "Warble"], [
        ["Source", *src],
        ["atten 10", *a10],
        ["atten 30", *run("--atten_lim_db", "30")],
        ["**atten 10 + `--reduce_hiss`**", *run("--atten_lim_db", "10", "--reduce_hiss")],
    ])

    print("### Hiss method  (same window, atten 10)\n")
    table(["Method", "Speech fidelity", "Speech-to-hiss", "Warble"], [
        ["No hiss stage", *a10],
        ["`patch`", *run("--atten_lim_db", "10", "--reduce_hiss", "--hiss_method", "patch")],
        ["**`profile` (default)**", *run("--atten_lim_db", "10", "--reduce_hiss")],
    ])

    print("### Patch mode settings  (same window, atten 10)\n")
    rows = [["No hiss stage", *a10]]
    for strength, window in (("0.0003", "0.006"), ("0.0003", "0.0025"), ("0.0006", "0.0025")):
        rows.append([f"strength {strength}, window {window}",
                     *run("--atten_lim_db", "10", "--reduce_hiss", "--hiss_method", "patch",
                          "--hiss_strength", strength, "--hiss_window", window)])
    table(["Setting", "Speech fidelity", "Speech-to-hiss", "Warble"], rows)

    traw, tsr = source(tick_video, 530, 20, os.path.join(work, "tick.wav"))

    def tick(*flags):
        x = clean(tick_video, 530, 20, work, *flags)
        rate = M.clicks_per_sec(x, tsr)
        # No fidelity column: this source carries music, which DeepFilterNet
        # rightly removes, so fidelity against the source is not meaningful here.
        return (f"{rate:.1f}", f"{100 * rate / M.clicks_per_sec(traw, tsr):.0f}%")

    print("### Declick  (tick video, 8m50s, 20 s)\n")
    table(["Setting", "Clicks/sec", "vs source"], [
        ["Source", f"{M.clicks_per_sec(traw, tsr):.1f}", "100%"],
        ["DeepFilterNet (atten 30)", *tick("--atten_lim_db", "30")],
        ["**+ `--declick`**", *tick("--atten_lim_db", "30", "--declick")],
        ["atten `none` instead", *tick("--atten_lim_db", "none")],
    ])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
