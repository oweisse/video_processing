# Changelog

## 0.3.0

Review fixes (`fable_review_plan.md`). Every change below is covered by the test suite
(`./venv/bin/pytest`, and `-m slow` for the model tests).

**Fixed**
- **Audio/video sync.** `afftdn` (profile hiss, declick), `anlmdn` (patch hiss) and the
  limiter delayed the audio by up to ~50 ms combined while the video was copied untouched.
  Each filter's delay is now measured and removed; the output is aligned to the sample.
  This also explains several earlier measurements of "damage" that were really delay.
- **Time parsing.** `1h20m` was read as 20 minutes, `5m30` as 5 minutes, and `-5` was
  accepted. Times now use a strict h/m/s grammar; windows past an hour are named in hours.
- **Interrupts.** Child processes are stopped before temporary files are copied or deleted.
- **End-of-file windows** always pass an explicit duration to the merge (no silent video
  tail).
- **Output names** `.`, `..` and blanks are rejected; `--noise_profile_at` is relative to
  the requested start and validated.
- **Loudness reporting** is measured on the final audio, after the music bed and declick.

**Changed**
- Everything after DeepFilterNet runs as one ffmpeg filter graph: one analysis pass, one
  render pass (was up to ~9 passes, each writing a full-length WAV).
- Defaults: `--atten_lim_db` 30 → 20, `--declick_nr` 24 → 12.
- Every threshold is a flag (*Advanced thresholds* in `--help`).
- Progress: the stage chain is printed up front, every bar shows an ETA, the render pass
  reports one line per stage, and `--verbose` shows ffmpeg command lines.
- The "no clear pause" warning for profile hiss compares the sample with the window's
  noise floor (`--noise_profile_max_excess_db`), so quiet recordings and noise-only
  stretches no longer trigger false alarms.
- PyTorch is no longer pinned below 2.2 (now 2.14). Python is capped at 3.11, the newest
  version DeepFilterNet's Rust core ships wheels for; `setup.sh` picks 3.11 or 3.10.
- Level analysis streams the audio instead of loading the whole window.

**Added**
- `video_audio_cleaner/metrics.py` and a two-tier pytest suite on synthetic fixtures.
- `scripts/readme_numbers.py` regenerates every table in the README.

**Not included**
- A per-passage "level guard" was built and measured, and made audio worse (noise-floor
  pumping; ffmpeg's runtime controls also corrupt `afftdn`). It is on the
  `level-guard-experiment` branch.

## 0.2.0

The rebuild: package layout with `pyproject.toml` and `setup.sh`; processing order fixed
(extract raw → clean → normalise last); DeepFilterNet attenuation limit; crossfaded chunks;
optional Demucs separation; keyframe-aligned cuts; `--compare`; hiss and click reduction;
live progress; required `--output_dir`; interrupt-safe cleanup.
