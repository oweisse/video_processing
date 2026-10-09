# Video Processing Tools

Two independent tools:

1. **`clean-audio`** — extracts a time window of audio from a video, cleans it, and merges it back.
2. **`improve_video.py`** — upscales and restores the *picture* (Real-ESRGAN / GFP-GAN / Stable Diffusion).

---

# 1. Audio Cleaning (`clean-audio`)

Cleans speech in a video — designed for home and wedding footage, where the goal is
intelligible speech that still **sounds like the original people**.

## Why it sounds like the original speakers

Every model in the pipeline is **discriminative (mask-based)**, not generative:

| Stage | Model | What it does |
|-------|-------|--------------|
| Separation (optional) | Demucs `htdemucs` | Splits the recording into a speech stem and a music bed |
| Denoising | DeepFilterNet 3 | Masks out hiss, hum, air-conditioning and crowd rumble |

Both compute a filter over your **actual recording** and subtract what you don't want.
Nothing is re-synthesised, so speaker identity, timbre and accent are preserved by
construction.

Generative restorers (VoiceFixer, Resemble Enhance) are deliberately **not** included:
they re-generate speech from a degraded input and can subtly change how people sound.

## Installation

```bash
./setup.sh
```

This installs FFmpeg (via Homebrew), creates `./venv`, and installs the project with the
Demucs extra and the test tools. It picks Python 3.11 or 3.10 automatically: DeepFilterNet's
Rust core (DeepFilterLib 0.5.6) only ships macOS wheels up to 3.11, so newer Pythons would
need a Rust toolchain. Any recent PyTorch works — DeepFilterNet 0.5.6 predates current
torchaudio, and `denoise.py` bridges the two (a slow test guards it).

Then run the tool with either:

```bash
./venv/bin/clean-audio --help
```

or activate the environment first, which makes `clean-audio` available from any directory:

```bash
source venv/bin/activate
```

> **`./venv/bin/clean-audio` is a relative path** — it only resolves when your shell is in
> the repo root (`/Users/ofir/git/video_processing`). From anywhere else, either activate
> the environment as above, or use the full path
> `/Users/ofir/git/video_processing/venv/bin/clean-audio`. The examples below assume you
> are in the repo root.

The legacy entry point still works: `python3 extract_and_filter_audio.py ...`

## Pipeline

Order matters. Audio is extracted **completely unprocessed**, cleaned, and only then
made louder. Normalising or boosting *before* denoising amplifies the very noise the
denoiser is meant to remove, and clips.

```
extract (raw 32-bit float, 48 kHz)
   └─> [optional] Demucs: split speech / music
         └─> DeepFilterNet on the speech
               └─> two-pass loudness normalisation
                     └─> [optional] mix music bed back underneath
                           └─> merge into video (or write WAV)
```

Everything after DeepFilterNet runs as a single ffmpeg filter graph: one pass to measure
loudness (only with `--normalize`), one pass to render. Intermediate files are written
only at the boundaries above, and the loudness the tool reports is measured on the final
audio — after the music bed and declick — not on an intermediate.

### What you see while it runs

The full stage chain is printed once, before any work starts. Each *pass* then gets a
numbered header, a live bar with speed and ETA, and ✔ lines. The render pass covers
several stages at once, so its header names them, and it prints one ✔ per stage when it
finishes. Its single bar is accurate for all of them: every filter in one graph is at the
same point in the audio, so 40 % means each stage is 40 % through.

```
Stages:       extract → denoise (atten 20) → hiss (profile, nr 12) → loudnorm (-16 LUFS) → declick (nr 12) → merge

[1/5] Extracting audio track from video
Extracting: [██████████████████████████████] 100.0% (31.0s/31.0s) 1680 sec/sec, took 0:00
✔ Audio extracted (unfiltered 32-bit float).

[2/5] Filtering noise using DeepFilterNet
Denoising: [███████████████████-----------] 64.5% (20.0s/31.0s) 20.9 sec/sec, ETA 0:01

[3/5] Analysing loudness (hiss → loudnorm measure)
✔ Measured -18.4 LUFS before normalising.

[4/5] Rendering: hiss → loudnorm → declick
Rendering hiss→loudnorm→declick: [██████████████████████████████] 100.0% (31.0s/31.0s) 88.7 sec/sec, took 0:00
✔ Hiss reduction (profile, nr 12) — noise sampled at 3.00s
✔ Loudness normalised (two-pass, linear, target -16 LUFS)
✔ Declick (nr 12)
✔ Final audio: -16.1 LUFS, true peak -5.6 dBTP (target -16)

[5/5] Merging cleaned audio track back into video
```

When output is piped to a file, bars are replaced by a plain line every 5 seconds
(`Denoising: 64.5% (20.0s/31.0s), ETA 0:01`). ffmpeg command lines are hidden by default
— a render graph is several hundred characters — and shown with `--verbose`; a failing
command is always printed with its error.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `input_video` | *(required)* | Path to the input video or audio file. |
| `--output_dir` | **required** | Directory every output is written to. Created if missing. |
| `-o, --output` | derived | Output **filename** (not a path) placed inside `--output_dir`. |
| `--skip_first_n_sec` | `0` | Skip this much at the start (`8m50s`, `1h20m`, `90`). See *Time values* below. |
| `--process_n_seconds` | to end | Process only this much (`3m30s`, `45s`). Must be > 0. |
| `--filter_audio_only` | off | Write a 24-bit WAV instead of merging into video. |
| `--compare [SECONDS]` | off (`20` if bare) | Preview mode — see below. |
| `--atten_lim_db` | `20` | Cap noise attenuation, in dB. `none` = unlimited. |
| `--chunk_len_sec` | `10` | Denoiser chunk length. |
| `--crossfade_sec` | `0.5` | Crossfade between denoised chunks. |
| `--normalize` | off | Two-pass loudness normalisation of the **cleaned** audio. |
| `--target_lufs` | `-16` | Target integrated loudness for `--normalize`. |
| `--audio_gain` | off | Extra gain after denoising (`6dB`, `2.0`), then limited. |
| `--reduce_hiss` | off | Extra pass to reduce steady background hiss. See below. |
| `--hiss_method` | `profile` | `profile` (learned noise fingerprint) or `patch`. |
| `--noise_profile_at` | auto | Where to sample noise for `profile` mode, relative to `--skip_first_n_sec`. |
| `--noise_profile_len` | `1` | Length of the noise sample, seconds. |
| `--noise_profile_max_excess_db` | `6` | Warn when the auto-picked sample sits this far above the noise floor (likely speech). |
| `--hiss_nr` | `12` | Strength for `profile` mode, in dB. |
| `--hiss_nf` | `-40` | Noise floor for `profile` mode, in dB (−80 to −20). |
| `--hiss_gain_smooth` | `0` | Raise (0–50) if `profile` mode still warbles. |
| `--hiss_strength` | `0.0003` | Strength for `patch` mode. |
| `--hiss_window` | `0.0025` | Patch search window, seconds. Larger removes more hiss, with more warble. |
| `--declick` | off | Extra pass to reduce mechanical tick/click noise. See below. |
| `--declick_nr` | `12` | Declick strength in dB (0.01–97). |
| `--declick_nf` | `-28` | Declick noise floor in dB (−80 to −20). |
| `--separate_music` | off | Split speech from music with Demucs first. |
| `--music_level_db` | `-12` | Level to mix music back under the speech. `off` drops it. |
| `--separation_device` | `auto` | `auto` (try MPS, fall back to CPU), `cpu`, or `mps`. |
| `--separation_segment` | auto | Demucs segment length (memory guard for long windows). |
| `--precise_cut` | off | Exact cut instead of keyframe snap — re-encodes the video. |
| `--video_crf` | `18` | x264 quality for `--precise_cut`. |
| `--audio_bitrate` | `192k` | AAC bitrate of the merged video. |
| `--keep_temp DIR` | off | Copy intermediates to DIR: `01_raw`, `02_vocals`/`02_music`, `03_denoised`, `04_final`. |
| `--verbose` | off | Print every ffmpeg command line before running it. |

**Advanced thresholds** — rarely needed; listed so nothing in the pipeline is a hidden
constant:

| Flag | Default | Description |
|------|---------|-------------|
| `--target_tp` | `-1.5` | True-peak ceiling for `--normalize`, dBTP. |
| `--target_lra` | `11` | Loudness range target for `--normalize`, LU. |
| `--limiter_ceiling` | `0.97` | Limiter ceiling after `--audio_gain` and the music mix (linear). |
| `--silence_warn_lufs` | `-45` | Warn when the cleaned audio is this quiet before normalising. |
| `--loudness_tolerance_db` | `2` | Warn when the result lands this far from `--target_lufs`. |
| `--separation_auto_segment_sec` | `600` | Longer windows get a 7 s Demucs segment to bound memory. |

### Time values

Times accept plain seconds (`90`, `1.5`) or hours/minutes/seconds in that order:
`1h20m`, `1h20m5s`, `8m50s`, `5m30` (a bare number after minutes is seconds), `45s`.
Anything else is rejected with an error — including negative values, `1h30` (ambiguous:
minutes or seconds?) and units out of order. Windows past the first hour are named in
hours, e.g. `…_01h01m00s-01h01m20s.mp4`.

### Tuning `--atten_lim_db` (the knob that matters most)

This caps how much the denoiser is allowed to attenuate. Unlimited suppression is the
main cause of the hollow, "underwater" sound on voices.

- `20` (the default) removes most of the noise while keeping voices natural.
- Quiet or distant speech → **lower** it to `10`. On a quiet passage of real home video,
  10 kept speech fidelity at 0.99 where 30 dropped it to 0.84.
- Genuinely noisy material with strong voices → raise it to `30`, or pass `none`.

### Steady background hiss: `--reduce_hiss`

For tape/film hiss that survives DeepFilterNet. It runs immediately after denoising,
*before* normalisation, so it sees the recording's own noise floor rather than one already
boosted by tens of dB.

The instinct is to raise `--atten_lim_db` instead. Don't — measured on a quiet passage of
home video (speech fidelity: 1.0 = speech structure untouched; speech-to-hiss: higher is
cleaner; warble: musical-noise flicker, 5.6 is the untouched recording):

| Setting | Speech fidelity | Speech-to-hiss | Warble |
|---|---|---|---|
| Source | 1.000 | 13.3 dB | 5.6 |
| atten 10 | 0.993 | 13.0 dB | 5.6 |
| atten 30 | 0.843 | 10.7 dB | 5.6 |
| **atten 10 + `--reduce_hiss`** | **0.971** | **19.6 dB** | **5.6** |

*Regenerate with:* `./venv/bin/python scripts/readme_numbers.py SPEECH_VIDEO TICK_VIDEO`

Raising attenuation to 30 costs a lot of speech and leaves the hiss *worse* than the
source. `--reduce_hiss` removes 6.6 dB of hiss for a small fraction of that cost, and
adds no warble.

#### `profile` mode (default)

This is the technique Audacity's Noise Reduction uses, done natively in ffmpeg — no extra
software. It finds the quietest second of your window (assumed to be pure noise), measures
that noise's exact spectral fingerprint, and subtracts *that specific shape* rather than
guessing at a generic noise floor. Knowing what the noise actually looks like is what
avoids the warbling.

Override the sample point with `--noise_profile_at 12s` if the automatic pick lands on
speech; turn strength up with `--hiss_nr 20`. The tool warns when the chosen second does
not look like a pause.

The two methods trade off differently on the same passage (atten 10):

| Method | Speech fidelity | Speech-to-hiss | Warble |
|---|---|---|---|
| No hiss stage | 0.993 | 13.0 dB | 5.6 |
| `patch` | 0.986 | 16.8 dB | 6.4 |
| **`profile` (default)** | 0.971 | **19.6 dB** | **5.6** |

*Regenerate with:* `./venv/bin/python scripts/readme_numbers.py SPEECH_VIDEO TICK_VIDEO`

Profile mode removes the most hiss and adds no warble at all; patch mode touches the
speech slightly less. If voices sound thinner with profile mode, try `--hiss_method patch`.

#### `patch` mode

*Warbling* ("pets pets pets") comes from `--hiss_strength`: isolated frequency bins
surviving as short tonal bursts. It scales with how much hiss you remove; patch mode has
no setting that removes a lot of hiss with none of it.

| Setting | Speech fidelity | Speech-to-hiss | Warble |
|---|---|---|---|
| No hiss stage | 0.993 | 13.0 dB | 5.6 |
| strength 0.0003, window 0.006 | 0.985 | 19.3 dB | 6.8 |
| **strength 0.0003, window 0.0025 (default)** | 0.986 | 16.8 dB | 6.4 |
| strength 0.0006, window 0.0025 | 0.977 | 19.6 dB | 6.8 |

*Regenerate with:* `./venv/bin/python scripts/readme_numbers.py SPEECH_VIDEO TICK_VIDEO`

A wider `--hiss_window` removes more hiss at the same fidelity, with more warble. An
earlier version of this README blamed the wide window for an *echo*. That echo was
the filter's look-ahead delay — 8 ms at window 0.006 — which is now removed for every
setting (see *Audio/video sync*).

A lowpass filter is *not* a substitute — it removes hiss and speech harmonics in equal
measure, leaving the ratio unchanged (13.1 dB at 8 kHz, versus 13.3 untouched).

### Mechanical tick/click noise: `--declick`

Film-transfer footage often carries the projector or camera mechanism as a rapid
"tick tick" — a broadband click train of roughly 20–25 clicks per second.
DeepFilterNet is trained on *stationary* noise (hiss, hum, fans) and only removes about
half of these. `--declick` adds a spectral pass afterwards that takes out about a third
of what remains.

Measured on real Super-8 transfer footage:

| Setting | Clicks/sec | vs source |
|---|---|---|
| Source | 20.2 | 100% |
| DeepFilterNet (atten 30) | 10.4 | 51% |
| **+ `--declick`** | **7.0** | **35%** |
| atten `none` instead | 10.2 | 51% |

*Regenerate with:* `./venv/bin/python scripts/readme_numbers.py SPEECH_VIDEO TICK_VIDEO`

The default strength is `--declick_nr 12`; it used to be 24, which caused a pumping, echo-like artifact on the first real recording it met.

**On quiet passages it does little — but no longer harms.** Earlier versions measured a
collapse in speech fidelity (0.935 → 0.655) on a quiet stretch. That turned out to be the
filter's uncompensated 25 ms delay, not the declicking itself (see *Audio/video sync*).
With the delay removed, the same stretch costs 0.013 fidelity (0.952 → 0.939). It also
removes almost no clicks there, because after a large normalisation boost the filter
cannot separate the ticks from the noise. So on quiet material `--declick` is harmless
but mostly pointless.

**Why there is no per-passage strength.** The obvious fix — lighter processing on quiet
passages, full strength on loud ones — was built and measured, and made things worse. On
quiet speech, processing mostly removes *noise*, so switching strength moves the noise
floor by up to 15 dB: audible pumping (fidelity 0.930 → 0.657 on the quiet passage). It
also turned out that ffmpeg's runtime controls corrupt `afftdn`: toggling it with
`enable=`, or changing its strength mid-stream, scrambles its state. The experiment is
kept on the `level-guard-experiment` branch.

Set expectations accordingly: this reduces the ticking, it does not eliminate it. Two
things that do **not** help, both measured rather than assumed:

- **`--atten_lim_db none`** removes no additional clicks (10.2/sec vs 10.4) and, as the
  hiss table shows, unlimited attenuation is what damages speech. Keep it at 20–30.
- **`--music_level_db off`** does not help either. The clicks stay in the Demucs *vocals*
  stem, not the music bed, so dropping the music leaves them behind.

If the ticking has to go completely, this needs dedicated spectral-repair software
(iZotope RX's De-crackle / Spectral Repair). Better still, if the tick was picked up by a
camcorder mic pointed at a projector screen, it was never on the film — a proper film
scan reading the magnetic stripe directly avoids it at the source.

### A note on `--normalize` and loudness

The tool reports the loudness it actually measured on the result, e.g.
`Final audio: -16.1 LUFS, true peak -5.6 dBTP (target -16)`, measured at the end of
the render — after the music bed and declick, on the audio you get. `loudnorm` caps its own gain to keep the
true peak under −1.5 dBTP, so quiet, peaky material can land short of the target — the
tool warns when it is more than 2 dB off. That behaviour is deliberate: staying below the
peak ceiling matters more than hitting a loudness number. Use `--audio_gain` if you need
a further, limited boost.

### Audition before committing: `--compare`

Processes only the first N seconds of your window and writes one WAV containing
**original → 0.5 s silence → processed**, so you can A/B the settings in seconds instead
of waiting for a full run.

```bash
./venv/bin/clean-audio "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" \
    --output_dir "/Users/ofir/Documents/video_processing_temp/cleaned" \
    --skip_first_n_sec 8m50s --compare 20 \
    --normalize --separate_music --atten_lim_db 30
```

`--compare` skips the video merge, so the preview starts exactly at the requested
timestamp with no keyframe snapping.

### Audio/video sync

Several of the filters used here (`afftdn` for profile hiss and declick, `anlmdn` for
patch hiss, and the limiter) look ahead, and ffmpeg delays their output instead of
compensating. Uncorrected, `--reduce_hiss --declick` put the audio about 50 ms behind
the picture. Each such filter's delay is now measured once with an impulse and removed,
so the cleaned audio lines up with the video to the sample; a slow test checks the whole
pipeline end to end.


Stream-copy seeking can only land on a keyframe, so the tool snaps the cut back to the
nearest keyframe at or before your requested start and uses that same point for both
audio and video. It reports the adjustment:

```
Requested start 530.00s → snapped to keyframe at 529.57s to keep the video lossless and in sync
```

The snap is usually well under a second — in `02-wedding.mp4`, keyframes near 8m50s sit
roughly 1–2 s apart, so asking for 530.00 s actually starts at 529.57 s.

Use `--precise_cut` if you need the cut exactly on the requested timestamp; the video is
re-encoded (slower, no longer bit-identical) in exchange.

### Interrupting a run

Ctrl+C at any point removes all temporary files, kills the child ffmpeg/Demucs
processes, and deletes any partially written output — the output directory never
contains a half-finished file. The tool exits with code 130.

## Examples

All examples assume your shell is in the repo root (`cd /Users/ofir/git/video_processing`).

**1. Quick 20-second preview to choose settings — run this first:**

```bash
./venv/bin/clean-audio "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" \
    --output_dir "/Users/ofir/Documents/video_processing_temp/cleaned" \
    --skip_first_n_sec 8m50s --compare 20 \
    --normalize --separate_music --atten_lim_db 30
```

Writes `cleaned/02-wedding_compare_08m50s-09m10s.wav` — about 40 s of audio: 20 s
original, 0.5 s silence, then the same 20 s processed.

**2. Once the preview sounds right, the full 3½-minute run (same flags, `--compare`
swapped for `--process_n_seconds`):**

```bash
./venv/bin/clean-audio "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" \
    --output_dir "/Users/ofir/Documents/video_processing_temp/cleaned" \
    --skip_first_n_sec 8m50s --process_n_seconds 3m30s \
    --normalize --separate_music --atten_lim_db 30 --declick
```

Writes `cleaned/02-wedding_clean_08m50s-12m20s.mp4`. Drop `--declick` if the source has
no mechanical tick.

**3. Speech only, music removed entirely, as a 24-bit WAV:**

```bash
./venv/bin/clean-audio "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" \
    --output_dir "/Users/ofir/Documents/video_processing_temp/cleaned" \
    --skip_first_n_sec 10m --process_n_seconds 1m \
    --filter_audio_only --separate_music --music_level_db off --normalize
```

**4. Gentle denoising for a quiet room with no music, keeping maximum naturalness:**

```bash
./venv/bin/clean-audio "/path/to/your-video.mp4" \
    --output_dir ~/Desktop/cleaned \
    --atten_lim_db 20 --normalize
```

## Performance on an M3 MacBook Air (16 GB)

For a 3m30s window, roughly: extraction a few seconds, DeepFilterNet ~20× realtime,
Demucs 1–3 minutes (MPS, automatic CPU fallback), normalisation and merge a few seconds
each. Demucs is by far the slowest stage — skip `--separate_music` when the recording has
no music.

---

## Development

Install the test dependencies into the same environment:

```bash
./venv/bin/pip install -e ".[separation,dev]"
```

Two test tiers:

```bash
./venv/bin/pytest
```

runs the fast tier (ffmpeg-only: parsing, naming, keyframes, merge sync, latency,
interrupt cleanup) in a few seconds.

```bash
./venv/bin/pytest -m slow
```

runs the slow tier against the real DeepFilterNet and Demucs models (well under a
minute on an M3). Add `-m "slow and not demucs"` to skip the slowest test.

Test material is synthesised on the fly by `tests/fixtures.py` — speech from macOS
`say`, plus seeded hiss, a jittered click train and a music bed — so no recordings are
needed and results are reproducible. The suite skips itself on machines without `say`.

Known bugs are pinned as `xfail(strict=True)` tests until fixed: a strict xfail that
starts passing fails the run, so a fix can't land silently.

### Measuring audio

Every number quoted in this README comes from `video_audio_cleaner/metrics.py`:

```bash
./venv/bin/python -m video_audio_cleaner.metrics processed.wav
```

```bash
./venv/bin/python -m video_audio_cleaner.metrics original.wav processed.wav
```

The first prints speech-to-hiss, warble and clicks/sec; the second adds speech
fidelity against the original. All four are level-independent, so a gain change alone
does not move them.

---

# 2. Video Enhancement (`improve_video.py`)

Uses Real-ESRGAN (via Vulkan/Metal on macOS) and generative models to upscale and enhance
old or low-resolution footage such as VHS rips.

## Installation

```bash
./setup_video_tool.sh
```

```bash
./setup_ai.sh
```

The first configures the portable Real-ESRGAN binary and models; the second creates the
`venv_ai` environment (PyTorch, Diffusers, GFP-GAN). This is a **separate** environment
from the audio tool's `venv`.

## Usage

```bash
python3 improve_video.py [input_video] [options]
```

For the generative flags (`--face_restoration`, `--generative_upscale`), activate the AI
environment first: `source venv_ai/bin/activate`

| Flag | Description |
|------|-------------|
| `-o, --output` | Path for the output file. |
| `--skip_first_n_sec` | Skip this much at the start (`1m30s`, `10m`). |
| `--process_n_seconds` | Process only this much (`1m30s`, `2m`). |
| `-s, --scale` | Scaling factor (2, 3, or 4; default 4). |
| `-m, --model` | Model name (default `realesrgan-x4plus`). |
| `--deinterlace` | De-interlace with yadif (critical for VHS). |
| `--denoise` | Temporal + spatial denoising before upscaling. |
| `--face_restoration` | GFP-GAN face restoration on upscaled frames. |
| `--generative_upscale` | Stable Diffusion + ControlNet Tile upscaling. |
| `--only_ai_process_n_frames N` | Process only N frames and write PNGs (for testing). |

### Examples

```bash
python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" --skip_first_n_sec 8m30s --process_n_seconds 4m
```

```bash
python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" --skip_first_n_sec 9m30s --process_n_seconds 10s --scale 2 --deinterlace --denoise
```

```bash
source venv_ai/bin/activate
python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-wedding.mp4" --skip_first_n_sec 9m30s --deinterlace --denoise --face_restoration --only_ai_process_n_frames 3
```
