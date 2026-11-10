# Changelog

## 0.5.0

**Added**
- `clean-audio-gui`: a desktop GUI (local web UI in a native window, `--browser` for a
  browser tab) over an output folder. Library of sources and outputs, a synced A/B
  player to compare original and cleaned (instant gapless switching, level-matched,
  loops), audition of every tuning candidate, a run form that shows the command it
  runs, and a job queue with progress, cancel and logs. See the README and
  `docs/gui-design.md`.
- `--progress json` (clean-audio, clean-audio-tune): one JSON event per line on stdout
  (steps, progress, messages, tuning candidates, the result); everything else goes to
  stderr. Not recorded in `.params.json`.
- `clean-audio-tune --keep_candidates` and `clean-audio --autotune --keep_candidates`
  keep every candidate's audio (AAC) and its loudness in the tuning cache. A window
  tuned earlier without it is measured again when the tool is asked to keep them.
- `clean-audio --from_candidate PLAN WINDOW INDEX` renders one tuned candidate instead
  of autotune's pick; the sidecar records `chosen_by: user` and autotune's own pick.
- A window's pick can be set by hand (GUI *Make it the pick*); it survives rescoring
  and is what `--autotune` and `clean-audio-batch` use for that window.
- `python -m video_audio_cleaner.probe FILE`: codecs, size, frame rate, keyframe spacing.
- Editing (GUI *Edit*): projects of clips from one or more videos, with cleaned sound
  where it exists, crossfades, and a music bed ducked under speech. Timeline with
  thumbnails, trim, reorder, split, undo; a program player over 540p proxies.
- `clean-audio-export PROJECT.json`: renders an edit to one MP4 (H.264 + AAC, faststart).
  Stream-copies the video when every cut is on a keyframe of one source; otherwise
  scales, pads and re-encodes (libx264, or `h264_videotoolbox`). Loudness by a measured
  gain and a true-peak limiter. `--plan` says whether it would be lossless.
- `python -m video_audio_cleaner.prep proxy|thumbs|peaks`: the editing proxy,
  timeline thumbnails and waveform peaks for a source.

## 0.4.0

**Added**
- `--tag LABEL` adds a label to the automatic output name, after the window
  (`video_09m10s-09m20s_no-music.mp4`).
- Tuning tries declick strengths (`--declick off,12,20` by default; was `off,on`), and
  autotune applies the chosen `--declick_nr`.
- With `--separate_music`, tuning and `--autotune` choose the music bed level: the
  quietest that masks Demucs's residue (`--music_levels`, `--max_music_warble_increase`).
  Dropping the music entirely left a watery, echo-like residue on loud music. Candidates
  are scored against speech + bed. Tuning method 4.
- `--source tape|digital` (clean-audio with `--autotune`, clean-audio-tune,
  clean-audio-batch). `digital` leaves hiss reduction and declick out of tuning: on
  modern recordings they only strip room tone, and the scores cannot tell. Asked
  interactively when tuning without it.
- A run that will replace an existing output (and its `.params.json`) says so up front.
- `clean-audio --autotune`: picks attenuation, hiss reduction and declick for the window
  automatically, renders with the pick, and records the scores in `.params.json`. Uses
  and extends the per-video tuning cache, so a window is never tuned twice.
- `--target_lufs auto`: the loudest loudness target reachable by a plain volume change
  (between `--target_lufs_max` −16 and `--target_lufs_min` −24), so `loudnorm` does not
  compress the voices. Default with `--autotune` and in tuning plans. The chosen value
  is recorded in `.params.json`.
- `clean-audio-tune`: tries a grid of settings on each window, scores every candidate
  against the source, and writes a report, per-window A/B listening files and a
  `plan.json` with the picked flags. Saved after every window and resumed on rerun:
  windows already tuned are kept, only new ones are tuned (`--retune` to redo).
- Tuning cache is per video, at `OUTPUT_DIR/_tuning/<video name>/`; tuning that cannot
  be merged (another video, another grid, `--retune`) is archived, never overwritten.
- `clean-audio-batch`: renders a tuned plan, or with `--video` reuses existing tuning
  for those windows (tuning only if missing or `--retune`). Prints the plan and every
  command line before running any; records the tuning in each output's
  `.params.json`; skips existing outputs; `--dry-run`, `--only`, `--force`.
- Tuning judges each window against its own gentlest and least-warbly candidates (no
  absolute fidelity bar), and measures candidates before loudness normalisation.
  Changing only the criteria re-scores saved measurements without rendering.
- `<output>.params.json` next to every output: all parameters including defaults, a
  reproduce command (tested to round-trip exactly), the actual window after keyframe
  snapping, measured results, and tool, git and library versions.

**Fixed**
- `-o` with a time window in the name that differs from the window processed (e.g.
  `_09m10s-12m20s` on a 10 s run) is refused, so stale names can no longer label the
  wrong audio. Use `--tag` for labels instead.
- Tuning's warble bar came from the calmest candidate overall, even one that failed
  fidelity. It now comes from the calmest candidate that keeps the voices. On the
  wedding tape this had rejected declick by 0.02 warble.
- With `--separate_music`, tuning scored candidates against the music-laden source, so
  removing the music counted as lost fidelity. It now scores against the speech stem,
  rendered without the music bed. Tuning method 3: older caches are archived and
  re-tuned on first use.
- The loudness report said "linear" even when `loudnorm` had fallen back to dynamic
  (compressing) mode, which it does on quiet material. It now reports the mode loudnorm
  actually used, warns when it compressed, suggests the target for a plain volume
  change, and records the mode in `.params.json`.

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
