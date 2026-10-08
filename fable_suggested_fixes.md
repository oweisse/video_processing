# Audio Cleaning Pipeline — Suggested Fixes & Improvements

**Audience:** This document is an implementation plan for Claude Opus 5 (or any engineer) to execute against this repository.

**Repository context:** `extract_and_filter_audio.py` extracts a time window of audio from a video, denoises it with DeepFilterNet, and merges it back into the video. It is used primarily to clean speech in home/wedding videos.

**Non-negotiable constraint:** The cleaned audio must sound like the *original speakers*. Prefer discriminative (mask-based) processing over generative resynthesis at every step. Any generative option must be opt-in and clearly labeled.

**Target hardware:** MacBook Air M3, 8-core CPU, 10-core GPU (Metal/MPS), 16 GB RAM. Everything must run locally within these limits. CPU-only fallbacks must always work.

---

## Current state (read this first)

- `extract_and_filter_audio.py` — single-file script. Notable behaviors:
  - Tries to `import df` (DeepFilterNet). If unavailable, finds the `deepFilter` executable installed by pipx, reads its shebang, and **re-executes itself** under that venv's Python (lines ~25–52).
  - Step 1 extracts audio with ffmpeg, applying `loudnorm` and `volume=<gain>` **before** denoising.
  - Step 2 denoises in 10 s chunks with 1 s context overlap, butt-joined (no crossfade), `atten_lim_db=None` (hardcoded full suppression).
  - Step 3 merges with `-ss <start>` **before** `-i` plus `-c:v copy`, which snaps to a keyframe while the audio was cut sample-accurately → potential A/V desync.
  - The default `input_video` is a hardcoded personal path.
- `setup.sh` — installs ffmpeg, pipx, `deepfilternet` via pipx, injects `soundfile`. There is **no `setup.py` / `pyproject.toml`** and no pinned dependencies.
- `improve_video.py`, `setup_ai.sh`, `setup_video_tool.sh`, `venv_ai/` — separate video-upscaling tool. **Out of scope**; do not break it, but do not modify it.
- `readme.txt` — plain-text docs for both tools.

---

## Cross-cutting requirements (apply to every phase — treat as acceptance criteria)

### A. Live progress reporting must be preserved and extended

The current script's live progress bars (percentage, seconds processed / total, and speed in `sec/sec`) are a **hard requirement**, not a nice-to-have. The refactor must not lose them, and every long-running stage must report progress in the same visual style:

| Stage | Progress source |
|-------|-----------------|
| Audio extraction (ffmpeg) | existing `-progress - -nostats` parser (`run_ffmpeg_with_progress`) — keep it |
| Demucs separation (Phase 6) | `demucs.api.Separator(..., callback=...)` progress callback; render the same bar (`Separating: [███---] 42.0% (88.2s/210.0s) (Speed: 1.3 sec/sec)`). If the callback API proves awkward, compute progress from processed segments. |
| DeepFilterNet denoising | existing per-chunk bar in `enhance_chunked` — keep it |
| Two-pass loudnorm (Phase 2) | pass 1 and 2 both go through `run_ffmpeg_with_progress` |
| Merge (ffmpeg) | existing parser — keep it |

Additional rules:
- When stdout is **not** a TTY, print a plain-text progress line at most every ~5 seconds (e.g. `Denoising: 42.0% (88.2s/210.0s)`) instead of being silent, so logs from piped runs are still informative.
- The progress-bar rendering should live in one shared helper in `ffmpeg_utils.py` (or a small `progress.py`) so all five stages format identically.
- Each stage prints its `[N/M]` step header as today; the total step count must adapt to enabled stages (separation, normalize, compare).

### B. Mandatory `--output_dir` flag

Add a **required** flag `--output_dir PATH`:
- All final outputs (merged MP4, `--filter_audio_only` WAV, `--compare` WAV) are written into this directory. Create it (`os.makedirs(..., exist_ok=True)`) at startup, and fail fast with a clear error if it cannot be created or is not writable — *before* any processing starts.
- The auto-generated filename logic (`<base>_clean_<start>-<end>.mp4` etc.) stays, but the file lands in `--output_dir` instead of next to the input.
- `-o/--output` changes meaning: it is now an optional **filename** (no directory component) placed inside `--output_dir`. If it contains a path separator, exit with an error explaining the new split. Update `readme`/`README.md` examples accordingly.
- Since the flag is mandatory, `argparse` must enforce it (`required=True`) and the examples in the README must all include it.

### C. Interrupt-safe cleanup (Ctrl+C / SIGTERM)

All temporary files must be removed even when the user interrupts a run. Requirements:

1. **Child processes:** every subprocess (ffmpeg, ffprobe, `deepFilter` fallback) must be launched with `start_new_session=True` and tracked; on `KeyboardInterrupt`/`SIGTERM`, send `SIGTERM` to the process group (`os.killpg`), wait up to ~3 s, then `SIGKILL`. Otherwise a killed parent leaves ffmpeg running and writing into the temp dir, which breaks cleanup.
2. **Temp directory:** keep `tempfile.TemporaryDirectory()` but wrap the whole pipeline in `try/finally` so cleanup runs on any exit path; additionally register an `atexit` fallback that ignores `FileNotFoundError`. With `--keep_temp DIR` (Phase 8), intermediates are *copied* out; the temp dir itself is still always deleted.
3. **Partial outputs:** if an interrupt (or any error) occurs while the final output file in `--output_dir` is being written, delete the partial file before exiting. Simplest robust pattern: have ffmpeg write to `<output>.partial.mp4` inside `--output_dir` and `os.replace()` it to the final name only after the merge step succeeds.
4. **Exit behavior:** on Ctrl+C, print a single `⚠ Interrupted — cleaned up temporary files.` line and exit with code 130. No tracebacks.
5. **Torch stages:** `KeyboardInterrupt` during `enhance_chunked` / Demucs runs inside Python — ensure it propagates promptly (check between chunks) rather than being swallowed by a broad `except Exception`. Audit the existing `except Exception` blocks: none may catch `KeyboardInterrupt` (they currently don't, since it subclasses `BaseException`, but the new code must not introduce `except BaseException` or bare `except:`).
6. **Test:** start a run on a 3-minute clip, Ctrl+C during each stage (extraction, separation, denoise, merge), and verify: no ffmpeg/deepFilter processes remain (`pgrep`), the temp dir is gone, and no `.partial` file remains in `--output_dir`.

---

## Phase 1 — Proper Python packaging (`pyproject.toml` + editable install)

Replace the pipx/re-exec hack with a real project environment. Use a `pyproject.toml` (the modern replacement for `setup.py`; setuptools reads it natively). Do **not** write a legacy `setup.py` unless a tool genuinely requires it.

### 1.1 Create `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "video-audio-cleaner"
version = "0.2.0"
description = "Extract, denoise, and re-merge speech audio in videos (DeepFilterNet + optional Demucs)"
requires-python = ">=3.10,<3.13"
dependencies = [
    "deepfilternet>=0.5.6",
    "torch>=2.2",
    "torchaudio>=2.2",
    "soundfile>=0.12",
    "numpy>=1.24,<2.0",   # DeepFilterNet 0.5.x is not numpy-2 compatible; verify before relaxing
]

[project.optional-dependencies]
separation = ["demucs>=4.0.1"]

[project.scripts]
clean-audio = "video_audio_cleaner.cli:main"
```

Notes for the implementer:
- **Verify the numpy pin** against the installed deepfilternet version; adjust if 0.5.x now supports numpy 2.
- On macOS arm64, plain `pip install torch` installs the correct Metal-enabled wheel; no special index needed.
- Python must be ≤3.12 (torch/deepfilternet wheel availability); check and adjust the ceiling.

### 1.2 Restructure into a small package

```
video_audio_cleaner/
    __init__.py
    cli.py           # argparse + main(), moved from extract_and_filter_audio.py
    ffmpeg_utils.py  # duration probe, keyframe probe, progress-bar runner
    denoise.py       # DeepFilterNet chunked enhancement
    separation.py    # (Phase 6) Demucs vocals/music split
    timeparse.py     # parse_time_string, format_time_filename
```

Keep `extract_and_filter_audio.py` at the repo root as a thin backward-compatible shim:

```python
#!/usr/bin/env python3
from video_audio_cleaner.cli import main
if __name__ == "__main__":
    main()
```

so existing muscle-memory commands (`python3 extract_and_filter_audio.py ...`) keep working.

### 1.3 Rewrite `setup.sh`

New behavior:
1. Check/install ffmpeg via Homebrew (keep existing logic).
2. Create `venv/` in the repo root with `python3 -m venv venv` (do not reuse `venv_ai`).
3. `venv/bin/pip install -e ".[separation]"`.
4. Print activation instructions and a smoke-test command.

**Delete the re-exec hack** (lines ~13–52 of the current script): with a proper venv, `import df` either works or the script should exit with a clear message telling the user to run `./setup.sh` and activate `venv`. Keep a friendly error, not silent re-exec magic.

### 1.4 Update docs

Convert `readme.txt` → `README.md` (keep the improve_video.py section intact). Document: setup, the `clean-audio` entry point, all flags including the new ones from later phases, and 2–3 realistic examples (reuse the wedding-video examples).

---

## Phase 2 — Fix the processing order: extract raw → denoise → normalize last

**Problem:** Today `loudnorm,volume=12dB` runs *before* DeepFilterNet, so the noise gets boosted before removal, and `loudnorm` (-24 LUFS, -2 dBTP target) + 12 dB into 16-bit PCM clips badly.

**New pipeline order:**

1. **Extract** the window as *unprocessed* audio: `-acodec pcm_f32le -ar 48000`, **no audio filters**. 32-bit float removes any clipping risk in intermediates.
2. **(Optional Phase 6) separate** music from speech.
3. **Denoise** with DeepFilterNet.
4. **Loudness-normalize the cleaned audio** with two-pass loudnorm:
   - Pass 1: `ffmpeg -i clean.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null -` → parse the JSON block from stderr (`input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`).
   - Pass 2: `loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=...:measured_TP=...:measured_LRA=...:measured_thresh=...:offset=...:linear=true`.
   - Target -16 LUFS (good for home-video speech; make it configurable via `--target_lufs`, default -16).
5. **Merge** back into the video.

**Flag changes:**
- `--normalize` now means "two-pass loudnorm *after* denoising". Update help text.
- `--audio_gain` becomes a *post-denoise* gain applied as `volume=<gain>,alimiter=limit=0.97` (limiter prevents clipping). Print a warning that with `--normalize` a manual gain is usually unnecessary.
- If both are given, order: loudnorm → volume → alimiter.

The final WAV written before merging (and the `--filter_audio_only` output) should be converted back to `pcm_s16le` at the end (after all gain staging), or better, keep `--filter_audio_only` output as 24-bit (`pcm_s24le`).

---

## Phase 3 — Expose DeepFilterNet's attenuation limit (`--atten_lim_db`)

**Problem:** `atten_lim_db=None` (full suppression) is hardcoded in `run_deepfilter_live`. Full suppression is the main source of "underwater"/robotic artifacts on voices.

**Change:**
- Add CLI flag `--atten_lim_db FLOAT`, **default `30.0`** — attenuate noise by at most 30 dB, which removes the vast majority of audible noise while keeping the speech (and a trace of natural ambience) intact.
- Allow `--atten_lim_db 0` or `--atten_lim_db none` to mean unlimited (map to `None`).
- Thread the value through `run_deepfilter_live` → `enhance_chunked` → `enhance(...)`.
- For the CLI fallback path, pass `-a <value>` to the `deepFilter` executable (verify the flag name with `deepFilter --help`; in DeepFilterNet 0.5.x it is `-a/--atten-lim`).
- Document in README: "If voices sound processed or hollow, lower the attenuation (e.g. 20). If noise remains, raise it (e.g. 40) or remove the limit."

---

## Phase 4 — Crossfade chunk boundaries in `enhance_chunked`

**Problem:** Chunks are enhanced with 1 s of context but stitched by hard concatenation; seams every 10 s can be audible.

**Change:** overlap-add with a linear (or Hann) crossfade:
- Process chunks so that consecutive *output* regions overlap by `crossfade_sec = 0.5` (this is separate from the existing context overlap, which should stay).
- In the overlap region, blend: `out = prev * (1 - w) + next * w` where `w` ramps 0→1 across the region.
- Implementation sketch: keep chunk boundaries as today, but instead of copying `enhanced_chunk[valid_start:valid_end]` exactly, extend the copied region by `crossfade` samples on each side and accumulate into the output buffer with triangular weights, tracking a weight-sum buffer to renormalize. Simpler equivalent: iterate chunks sequentially, remember the last `crossfade` samples of the previous chunk's output, and blend them with the first `crossfade` samples of the current chunk.
- Guard: total processing time must not regress noticeably (crossfade adds ~5% redundant computation at these sizes — fine).

---

## Phase 5 — Keyframe-safe merge (fix A/V desync)

**Problem:** The merge uses `-ss <start>` before `-i` with `-c:v copy`. Stream-copy seeking snaps to the nearest keyframe, but the audio was extracted sample-accurately, so audio and video can be offset by up to a GOP (often 1–2 s on consumer cameras).

**Fix (keyframe-aligned cut):**
1. Before extraction, probe the nearest keyframe **at or before** the requested start:
   ```
   ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags \
           -of csv=print_section=0 -read_intervals <start-10>%<start+0.1> <input>
   ```
   Take the largest `pts_time` with the `K` flag that is ≤ requested start. Fall back to the requested start if probing fails.
2. Use that keyframe time as the *actual* start for **both** the audio extraction (Step 1) and the merge (Step 3). Print an info line: `Requested start 530.00s → snapped to keyframe at 528.63s to keep video lossless and in sync.`
3. Extend `--process_n_seconds` by the snap difference so the requested window is fully covered.
4. Add `--precise_cut` flag: instead of snapping, re-encode the video segment (`-c:v libx264 -crf 18 -preset medium` + `-ss` *after* `-i` for frame accuracy) so the cut lands exactly at the requested time. Slower; document the tradeoff.
5. Keep `-shortest`; also pass `-b:a 192k` (or `-c:a aac -b:a 192k`) for the merged AAC track instead of ffmpeg's default.

**Verification:** extract a 5 s test clip both ways and confirm with `ffprobe -show_entries stream=start_time,duration` that video and audio start times match; visually spot-check lip sync at the cut point.

---

## Phase 6 — Optional Demucs music/speech separation (biggest quality upgrade for weddings)

**Problem:** DeepFilterNet targets noise, not *music*. Wedding audio is speech over music + crowd. Separating first lets the denoiser work on a clean vocal stem, and voices remain authentic because Demucs is discriminative (masking, not resynthesis).

**Design:**
- New module `separation.py`, new flags:
  - `--separate_music` — enable the stage.
  - `--music_level_db FLOAT`, default `-12.0` — how much of the separated music/other stems to mix back under the cleaned speech. `-100` (or a `--music_level_db off` sentinel) drops music entirely. Mixing some music back is usually desirable for a wedding video — it keeps the atmosphere while making speech intelligible.
- Use the Python API, not subprocess:
  ```python
  from demucs.api import Separator
  sep = Separator(model="htdemucs", device="cpu")   # try "mps", fall back to cpu on error
  origin, stems = sep.separate_audio_file(path)      # stems: drums, bass, other, vocals
  ```
  - Speech stem = `vocals`. Music bed = `drums + bass + other` summed.
  - **MPS caveat:** Demucs has had MPS op-coverage issues; wrap the MPS attempt in try/except and fall back to CPU with a printed notice. CPU on M3 handles ~3.5 min of audio in a few minutes — acceptable.
  - Demucs works at 44.1 kHz; resample stems back to 48 kHz (`torchaudio.functional.resample`) before the DeepFilterNet stage.
- **Pipeline position:** extract (raw) → Demucs → DeepFilterNet on the *vocals* stem only → two-pass loudnorm on the cleaned vocals → mix music bed at `--music_level_db` → merge.
- Memory guard: for windows > ~10 min, run Demucs with `segment` option (e.g. `Separator(..., segment=10)`) to stay well within 16 GB.
- Keep the dependency optional (`pip install -e ".[separation]"`); if `--separate_music` is passed but demucs isn't importable, exit with the install hint.

---

## Phase 7 — `--compare` preview mode

Add `--compare [SECONDS]` (default 20): process only the first N seconds of the requested window, then write a single WAV next to the output path named `<base>_compare.wav` containing: original segment → 0.5 s silence → processed segment (concatenate with ffmpeg `concat` filter or numpy). Print the path prominently. This lets the user audition settings (`--atten_lim_db`, `--music_level_db`) cheaply before a full run.

---

## Phase 8 — Cleanups

1. Make `input_video` a **required** positional argument (remove the hardcoded personal default). Update readme examples accordingly.
2. Progress bars: see cross-cutting requirement A (shared renderer, non-TTY fallback).
3. Factor the three near-identical `print_*` helpers to use one `_styled()` function (minor).
4. Add a `--keep_temp DIR` debug flag that copies intermediate WAVs (raw, stems, denoised, normalized) into DIR instead of losing them with the temp directory.
5. Add `.gitignore` entries: `__pycache__/`, `venv/`, `venv_ai/`, `weights/`, `bin/`, `gfpgan/`, `output_images_*/`, `*.png` test artifacts, `.DS_Store`.

---

## Explicitly out of scope

- **Generative restoration** (VoiceFixer, Resemble Enhance): do not add. If ever added later it must be a separate opt-in flag with a loud warning that it can alter voice identity.
- `improve_video.py` and its setup scripts: untouched.
- Cloud services: everything stays local.

---

## Suggested implementation order & verification

| Order | Phase | Verify by |
|-------|-------|-----------|
| 1 | Phase 1 (packaging) | Fresh `./setup.sh` on a clean venv; `clean-audio --help` works; `python3 extract_and_filter_audio.py --help` still works |
| 2 | Phase 2 (ordering) | Run on a 30 s slice; confirm intermediate WAV is float32 and unfiltered; confirm no clipping (`ffmpeg -af astats` shows peak < 0 dBFS); output loudness ≈ target LUFS |
| 3 | Phase 3 (atten_lim) | A/B 30 s clips at atten 20 / 30 / unlimited |
| 4 | Phase 4 (crossfade) | Denoise a 35 s tone+noise file; inspect for discontinuities at 10 s boundaries (spectrogram or `astats` per-frame) |
| 5 | Phase 5 (sync) | `ffprobe` start-time check + lip-sync spot check as described |
| 6 | Phase 6 (Demucs) | Run on the wedding clip window (8m50s + 3m30s); listen to vocals stem, music bed, and final mix |
| 7 | Phases 7–8 | `--compare` output audible and correctly ordered; flags documented in README.md |
| every phase | Cross-cutting A (progress) | Every long stage shows a live bar with %, time, and speed on a TTY; periodic plain lines when piped (`... \| cat`) |
| every phase | Cross-cutting B (`--output_dir`) | Running without `--output_dir` exits with an argparse error; all outputs land in the given directory |
| every phase | Cross-cutting C (Ctrl+C) | Interrupt during each stage: no orphan ffmpeg/deepFilter processes, temp dir removed, no partial output file, exit code 130 |

Each phase should be a separate commit with a descriptive message.

**End-to-end acceptance test** (the user's real command, adapted):

```
clean-audio "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" \
    --output_dir "/Users/ofir/Documents/video_processing_temp/cleaned" \
    --skip_first_n_sec 8m50s --process_n_seconds 3m30s \
    --normalize --separate_music --atten_lim_db 30
```

Expected: no clipping, speech clearly intelligible and natural-sounding (same voices), music present but ducked, video in sync at the cut, live progress bars for every stage, output `cleaned/02-wedding_clean_08m50s-12m20s.mp4`. A second run interrupted with Ctrl+C mid-denoise leaves no temp files, no orphan processes, and no partial output.
