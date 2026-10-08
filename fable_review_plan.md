# Review Fix Plan — `video_audio_cleaner`

**Audience:** this document is an execution plan for Claude Opus. Everything in it has
been decided with the project owner; do not re-litigate the decisions in the
"Settled decisions" section. Where a choice is still open it is marked **DECIDE** with a
recommended default — take the default unless it proves wrong in practice, and record
what you did in the commit message.

**Baseline commit:** `fa3587a` on `main` ("Rebuild audio cleaner as a package with
hiss/click reduction"). Start from a clean tree at or after that commit.

**Repository:** `/Users/ofir/git/video_processing`. The tool is the `video_audio_cleaner`
package, entry point `clean-audio`, installed editable into `./venv` (Python 3.11).
`improve_video.py`, `setup_ai.sh`, `setup_video_tool.sh` and `venv_ai/` are a separate
tool — **do not touch them**.

---

## 0. Ground rules

1. **One commit per phase**, in the order below. Each phase must leave the tool working
   and the test suite green before you commit. Use the commit attribution line your
   harness tells you to; do not copy one from this file.
2. **Tests first.** Phase 1 builds the safety net. Every later phase adds or extends
   tests for the behaviour it changes. Never commit a phase whose tests you have not run.
3. **Preserve the public CLI.** Every existing flag keeps its name and meaning except
   where this plan says otherwise (defaults in Phase 3). Output filenames do not change.
4. **Keep the output honest.** The tool prints measured results, not predictions
   (see Phase 5: the loudness figure must be measured on the *final* audio). If you
   cannot measure something, say so in the output rather than printing a target.
5. **Run commands via `./venv/bin/…`** — the system `python3` is 3.14 and has none of
   the dependencies. `./venv/bin/pytest`, `./venv/bin/clean-audio`, `./venv/bin/python`.
6. **Real-file spot checks are allowed but not required.** The owner's test material is
   under `/Users/ofir/Documents/video_processing_temp/` (`02-wedding.mp4`,
   `04-random_chat_w_dad.mp4`). Never write outputs next to those files; use
   `--output_dir` pointing at a scratch directory, and never run the long full-length
   renders — 20–60 s windows are enough.
7. **Do not add a dependency** without a line in `pyproject.toml` and a reason in the
   commit. `pytest` goes in a new `[project.optional-dependencies] dev` extra.
8. **No magic numbers in stage logic.** Every numeric threshold, window length, level or
   tolerance that affects the audio or a warning is a CLI flag with a sensible default,
   listed in Appendix C. If you introduce a new one, add it to that table and to the
   README's options table in the same commit. Pure implementation constants (FFT sizes,
   buffer sizes) are exempt.
9. **Progress must stay legible.** At every moment a user watching the terminal can tell
   (a) which stages are enabled for this run, (b) which pass is executing now,
   (c) how far along it is with speed and ETA, and (d) what has already completed. Any
   change that merges stages into one pass must satisfy this (see 5.3) — saving passes
   is not worth losing that.

---

## Settled decisions (do not reopen)

| Question | Decision |
|---|---|
| Scope | All of: confirmed bugs, defaults + hygiene, single-ffmpeg-graph refactor, metrics + tests, adaptive guard, torch unpin shim |
| Adaptive mode | **Simple level guard**, not per-segment tuning: on quiet blocks, disable `--declick` and lower hiss reduction. Implemented inside the single ffmpeg graph via `enable=` timelines and `asendcmd`, so there is no segmentation and no seams. |
| Tests | **Two tiers.** Fast ffmpeg-only tests always run. Model-dependent tests are marked `@pytest.mark.slow` and skipped by default (`-m slow` runs them). |
| torch pin | **Lift it** with a shim restoring `torchaudio.backend.common`, guarded by a test that imports DeepFilterNet. |
| `5m30` | Parse as 330 s (digits after `m` with no unit are seconds). Anything else unparsed is an error. |
| Defaults | `--atten_lim_db` 30 → **20**; `--declick_nr` 24 → **12**. |

---

## Phase 1 — Metrics module, fixture, and test suite (the safety net)

**Why first:** every later phase changes audio behaviour. Without reproducible numbers
you cannot tell a refactor from a regression. The README already quotes measurements
(speech fidelity, speech-to-hiss, warble, clicks/sec, Demucs SNR) that currently cannot
be reproduced from anything in the repo.

### 1.1 `video_audio_cleaner/metrics.py`

Pure functions over `(mono float32 ndarray, sr)`. Each takes already-loaded audio; a
thin `load_mono(path) -> (ndarray, sr)` helper using `soundfile` sits alongside. All
four were used throughout the review; implement them exactly as specified so the
numbers in the README stay comparable.

- `speech_fidelity(ref, test, sr) -> float` in `[−1, 1]`, 1.0 = structure fully
  preserved. Level-normalise both signals by RMS. 25 ms Hann frames, 50 % hop. Bands
  (Hz): `[300,500,800,1200,1800,2600,3400,4500,5800,7000]`. Per-band log envelope in dB.
  **Speech-active frames only:** frames whose full-band RMS in `ref` is above the 55th
  percentile. For each band, Pearson correlation between ref and test envelopes over
  those frames; return the mean across bands. (Restricting to active frames is what
  stops the metric from penalising legitimate noise removal in the gaps.)
- `speech_to_hiss_db(x, sr) -> float`, higher = cleaner gaps. 50 ms frames, RMS per
  frame. `20·log10( median(RMS of loudest 25 % frames) / median(RMS of quietest 20 %) )`.
  Scale-invariant by construction.
- `warble(x, sr) -> float`, the "pets-pets-pets" musical-noise index; ≈5.6 on the
  owner's untouched material. RMS-normalise. 25 ms Hann frames, 50 % hop, magnitude
  spectrum per frame. Take the quietest 20 % of frames (by frame RMS), restrict bins to
  1–8 kHz, convert to dB, and return the mean over bins of the **standard deviation
  across frames**. Steady hiss → low; bins popping in and out → high.
- `clicks_per_sec(x, sr, lo=2000, hi=10000) -> float`. Band-pass via FFT zeroing
  outside `[lo, hi)`. 1 ms RMS envelope. Positive first difference (onset flux).
  Threshold = mean + 3·std. Count local maxima above threshold; divide by duration.

Also add `snr_db(ref, test)` with optimal scalar gain (used for the Demucs test):
`a = <test,ref>/<test,test>`, then `10·log10(Σref² / Σ(a·test − ref)²)`.

### 1.2 `clean-audio measure` subcommand (optional but cheap)

`clean-audio measure A.wav [B.wav]` prints the four metrics for A, and for B relative
to A when given. Implement as a sub-parser only if it does not complicate `main()`;
otherwise expose `python -m video_audio_cleaner.metrics A B`. **DECIDE:** default to the
`python -m` form — fewer moving parts in the CLI.

### 1.3 Fixture generator `tests/fixtures.py`

Deterministic synthetic material, generated into `tmp_path` by a session-scoped
pytest fixture. Must run on a stock Mac without network:

- **Speech:** `say -v Samantha -o speech.aiff "<fixed sentence, ~15 s>"`. Skip the whole
  suite with a clear message if `say` is missing (non-mac). Resample to 48 kHz mono.
- **Music bed:** 82 Hz sine with `tremolo=f=2:d=0.9` (bass), white noise `highpass=2500`
  with `tremolo=f=4:d=1` (drums), 330 Hz sine at low level (pad). This is the mix that
  gave Demucs a +14 dB SNR gain in the review; pure sine chords do **not** separate
  (they sit in the vocal range) — do not "simplify" it to tones.
- **Hiss:** white noise at −35 dBFS.
- **Clicks:** an impulse train at ~25 Hz with ±8 % random jitter on each interval
  (the real projector tick is *not* perfectly periodic — a perfectly periodic train
  would make the declick tests unrealistically easy), each impulse a 1 ms decaying
  burst of band-limited noise.
- **Video wrapper:** `testsrc2=size=640x480:rate=24`, `libx264 -g 48` (keyframe every
  2 s), audio as AAC 192k, 60 s. Keyframes every 2 s are what make the snap test
  deterministic.
- Produce: `clean_speech.wav`, `mix_noisy.wav` (speech+hiss+clicks),
  `mix_music.wav` (speech+music+hiss), `fixture.mp4` (video + `mix_noisy`), and the
  ground-truth stems. Return a small dataclass of paths.

### 1.4 Tests — fast tier (`tests/test_*.py`, no marker)

- **timeparse:** table-driven. Includes the three confirmed misparses from the review,
  which must become errors or correct values after Phase 2:
  `"1h20m" → 4800`, `"5m30" → 330`, `"-5" → error`, `"2m 30s" → 150`, `"90" → 90`,
  `"1.5" → 1.5`, `"abc" → error`, `"" → error`, `"5x" → error`.
- **output naming:** given flags, assert the derived filename
  (`<base>_<start>-<end>.mp4`, `_compare` suffix retained, `.wav` for audio-only) and
  that `-o ".."`, `-o "."`, `-o "a/b"` are rejected, and that an output path equal to
  the input path is refused.
- **keyframe probe:** on `fixture.mp4`, `probe_keyframe_at_or_before(path, 9.0) == 8.0`
  (within 1 ms); for `0.0` returns `0.0`.
- **merge sync + duration:** run the real CLI on `fixture.mp4` with
  `--skip_first_n_sec 9s --process_n_seconds 10s` (fast: DeepFilterNet on 11 s — *this
  one is slow-tier*, see below). For the fast tier instead test `_merge_video` directly
  with a pre-made WAV: assert video and audio `start_time == 0` and durations within
  one frame (0.045 s). Add the **end-of-file** case (no `-t`): durations must still
  match — this is bug B3.
- **loudnorm JSON parsing:** feed `_parse_loudnorm_json` a captured stderr blob with
  the JSON block plus a decoy `{...}` after it → must still return loudnorm's values
  (Phase 2 anchors on `"input_i"`).
- **interrupt cleanup:** spawn `clean-audio` as a subprocess on `fixture.mp4` with a
  long window, wait until the log shows stage 2, send `SIGINT`, assert: exit code 130,
  no `video_audio_cleaner_*` directories under `tempfile.gettempdir()`, no `*.partial.*`
  in the output dir, and `pgrep -f <output_dir>` finds nothing. Also assert the
  intermediates copied by `--keep_temp` are not truncated (bug B2).
- **progress rendering:** `ProgressReporter` with a `StringIO` stream (non-TTY) emits
  at most one line per `non_tty_interval` and does not print 100 % twice.
- **graph builder (Phase 5):** the pure function that assembles the filter graph
  string returns the expected chain for a handful of flag combinations.

### 1.5 Tests — slow tier (`@pytest.mark.slow`)

Register the marker in `pyproject.toml` (`[tool.pytest.ini_options]`,
`addopts = "-m 'not slow'"`, so plain `pytest` skips them; `pytest -m slow` runs them).

- **denoise reduces hiss, atten is monotonic:** `denoise_file` on `mix_noisy.wav` at
  `atten_lim_db` 10 / 30 / None. Assert `speech_to_hiss_db` increases monotonically and
  `speech_fidelity ≥ 0.90` at 10 and `≥ 0.80` at 30 against `clean_speech.wav`.
- **crossfade is not worse than unchunked:** `enhance_chunked` with
  `chunk_len_sec=1000` as reference; `crossfade_sec=0.5` must be at least as close to
  the reference (RMS error) as `crossfade_sec=0`.
- **profile hiss beats patch hiss:** on `mix_noisy.wav` after denoising,
  `speech_to_hiss_db(profile) > speech_to_hiss_db(patch)` and
  `warble(profile) ≤ warble(patch) + 0.1`.
- **adaptive guard (Phase 6):** on a fixture whose second half is 25 dB quieter, with
  `--declick`, assert the quiet half's `speech_fidelity` is within 0.03 of the
  no-declick run, while the loud half's `clicks_per_sec` dropped.
- **Demucs:** `separate_to_files` on `mix_music.wav`; `snr_db(clean_speech, vocals)`
  ≥ `snr_db(clean_speech, mix) + 8`. Mark additionally with `@pytest.mark.demucs`
  so it can be excluded separately (it is the slowest).
- **DeepFilterNet imports** (Phase 4 guard): `import df.enhance` succeeds under the
  installed torch/torchaudio.

### 1.6 Wiring

- `pyproject.toml`: `dev = ["pytest>=8"]`; pytest config as above.
- `README.md`: a "Development" section: how to install the dev extra, run fast tests,
  run slow tests, regenerate the numbers in the README with the metrics module.
- **Acceptance:** `./venv/bin/pytest` green in well under a minute;
  `./venv/bin/pytest -m slow` green (minutes).

---

## Phase 2 — Confirmed bugs

All five were reproduced during review. Each gets a test (several already specified in
Phase 1; make them pass here).

### B1 — `timeparse.py`: silent misparses

Current regexes search for `(\d+)m` and `(\d+)s` anywhere, so `1h20m` → 1200 (hour
ignored), `5m30` → 300 (trailing 30 dropped), `-5` → −5.0 (accepted).

Replace with a full-match grammar. Accepted forms (case-insensitive, optional spaces):

- plain number → seconds (`"90"`, `"1.5"`)
- `[Hh][Mm][S(s)]` components in order, each optional, at least one present:
  `1h`, `1h20m`, `1h20m5s`, `20m`, `5m30s`, `5m30` (trailing bare number after `m` is
  seconds), `45s`.
- Reject: negatives, empty, unknown units, out-of-order units, leftover characters.
  Error message must show the offending input and the accepted forms.

Also validate in `build_parser`/`_run`: `--process_n_seconds` and `--compare` must be
`> 0`; `--skip_first_n_sec ≥ 0`. Update `format_time_filename` to emit hours when
`seconds ≥ 3600` (`01h20m00s`), so an 89-minute file names sensibly.

### B2 — `cli.py` `_run` `finally`: temp dir removed before children are killed

The `finally` at the end of `_run` runs `_copy_intermediates` and `rmtree` while a
child ffmpeg may still be writing; `procs.terminate_all()` only runs later in `main()`.
It "worked" only because POSIX allows unlinking open files; `--keep_temp` could copy a
half-written WAV.

Fix: first statement of that `finally` is `procs.terminate_all()`. Keep the
`cleanup_all()` calls in `main()` (idempotent). Interrupt test asserts intermediates are
complete.

### B3 — `_merge_video`: `-t` only added when a duration was requested

When processing to end-of-file, only `-shortest` is passed, and with stream-copied video
that overshoots (3.4 s of silent video tail was observed). Fix: in `_run`, when
`actual_duration is None` and `file_duration is not None`, set
`actual_duration = max(0, file_duration - actual_start)`. Then `_merge_video` always
has `-t`. Keep `-shortest` as a belt-and-braces. Test: end-of-file merge, durations
match within one frame.

### B4 — `_validate_output_name`: `..` and `.` accepted

Reject when `name in (".", "..")` or `os.path.basename(name) != name` or `name` is
empty/whitespace. Keep the existing separator message.

### B5 — `--noise_profile_at` ignores the keyframe snap

The user's time is relative to their *requested* start, but the extracted audio begins
`snap_delta` earlier. Add `snap_delta` to `noise_at` before use, and validate that
`noise_at + NOISE_LEN_SEC ≤ work_duration`, else error with the valid range.

### Also in this phase (small, same area)

- `_parse_loudnorm_json`: choose the JSON block that contains `"input_i"`, not the last
  `{…}` in stderr.
- `denoise.py` top-level import: catch `(ImportError, OSError)` so a broken torch dylib
  still produces the setup hint instead of a traceback.
- `_merge_video`: add `-avoid_negative_ts make_zero` (B-frame sources).
- Warn when the auto-picked noise sample is not clearly quieter than the rest (its RMS
  less than `--noise_profile_min_gap_db` below the file's median frame RMS; default
  **10**): "no clear gap found — consider `--noise_profile_at`".
- Make the noise sample length a flag: `--noise_profile_len` (seconds, default **1.0**),
  replacing the `NOISE_LEN_SEC` constant. The `asendcmd` stop time is derived from it
  (`len − 0.1`, floor 0.05).

**Commit:** "Fix time parsing, cleanup ordering, end-of-file merge, and output-name
edge cases".

---

## Phase 3 — Defaults and hygiene

- `--atten_lim_db` default **20** (was 30). Every real test in the review preferred
  10–20; the README's "30 is the default" rationale must be rewritten to say 20 removes
  most noise while keeping voices natural, and that 30 is the setting for genuinely
  noisy material.
- `--declick_nr` default **12** (was 24). README: it was too hot at 24.
- Replace the hard-coded `nf=-40` in `hiss.reduce_hiss_profile` with a
  `--hiss_nf` flag (default −40), mirroring `--declick_nf`.
- Expose the remaining constants in `cli.py` as flags with their current values as
  defaults: `--target_tp` (−1.5 dBTP), `--target_lra` (11), `--limiter_ceiling` (0.97),
  `--silence_warn_lufs` (−45, the "close to silence" warning),
  `--loudness_tolerance_db` (2.0, the "off target" warning), and
  `--separation_auto_segment_sec` (600, above which Demucs gets `segment=7`). Keep the
  module-level constants only as the `default=` values. Grouping these under an
  `argparse` argument group "Advanced thresholds" keeps `--help` readable.
- Delete `audioio.read_wav` (no callers). If `write_wav` is the only thing left,
  keep the module — `denoise.py` and `separation.py` use it.
- Single version source: remove `__version__` from `__init__.py` or derive it via
  `importlib.metadata.version("video-audio-cleaner")`. Bump `pyproject.toml` to
  `0.3.0` at the end of the whole plan (Phase 7), not here.
- `git rm readme.txt` — `README.md` carries all of it. Confirm by diffing the
  `improve_video.py` section against the old file before removing.
- `git rm --cached` the tracked test images that `.gitignore` now lists
  (`test_frame_*.png`, `output_test_t256.png`); they are 20 MB of scratch from the
  other tool. Confirm with the owner's `.gitignore` intent — they are listed there.
- `_run`: stop mutating `args.filter_audio_only`; use a local `audio_only` variable.

**Commit:** "Tune defaults from measured results; remove dead code and stale files".

---

## Phase 4 — Lift the torch pin

`deepfilternet==0.5.6` (latest, 2023) does
`from torchaudio.backend.common import AudioMetaData` in `df/io.py`. torchaudio ≥ 2.2
removed that module, which is why `pyproject.toml` pins `torch,torchaudio <2.2`, which
in turn caps Python at 3.12.

### 4.1 Shim

In `denoise.py`, **before** the `df` imports and inside the same `try`:

```python
import sys, types, torchaudio
if "torchaudio.backend.common" not in sys.modules:
    try:
        import torchaudio.backend.common  # noqa: F401  (old torchaudio: real module)
    except ImportError:
        shim = types.ModuleType("torchaudio.backend.common")
        shim.AudioMetaData = torchaudio.AudioMetaData
        backend = sys.modules.setdefault("torchaudio.backend",
                                         types.ModuleType("torchaudio.backend"))
        backend.common = shim
        sys.modules["torchaudio.backend.common"] = shim
```

Put the same shim in `separation.py`'s import path only if Demucs needs it (it does
not today; verify and leave it out if so). Keep the existing warning filter.

### 4.2 Pins

`torch>=2.1`, `torchaudio>=2.1` (drop the `<2.2`), `requires-python = ">=3.10,<3.14"`
(3.13 only if `deepfilterlib` wheels exist for it — check `pip download` first; if not,
keep `<3.13` and say so in the commit). Keep `numpy<2.0` unless you verify DeepFilterNet
imports and produces identical output under numpy 2 — do not relax it on a hunch.

### 4.3 Verify

- Fresh venv via `./setup.sh` → the smoke test in setup.sh passes.
- `pytest -m slow -k "denoise"` passes under the newest torch that installs.
- Run `denoise_file` on `mix_noisy.wav` before and after the pin change; the
  `speech_to_hiss_db` must agree within 0.2 dB (DeepFilterNet's output should not
  depend on the torch minor version; if it does, report the numbers and keep the pin).
- Update `setup.sh`'s Python-selection comment and the README "Installation" paragraph.

**Commit:** "Lift torch/torchaudio pin via torchaudio.backend.common shim".

---

## Phase 5 — Single ffmpeg graph for everything after DeepFilterNet

### Why

After DeepFilterNet, every stage is pure ffmpeg, yet today each is its own process
writing a full-length 32-bit float WAV: hiss profile = 4 passes, normalise = 3
(measure, apply, verify), mix = 1, declick = 1, output = 1. Up to ~10 passes and ~2 GB
per intermediate for a 90-minute window. It also causes a correctness bug: the
"Loudness normalised: −16.0 LUFS" line is measured *before* the music bed and the
declick are applied, so it describes an intermediate, not the file the user gets.

### Target shape

```
extract (ffmpeg)  →  [Demucs]  →  DeepFilterNet  →  ONE analysis pass  →  ONE render pass  →  merge/write
```

Two ffmpeg invocations instead of ~9, and the loudness figure is measured on the final
audio.

### 5.1 A pure graph builder

New module `video_audio_cleaner/graph.py` with
`build_graph(opts) -> (inputs: list[str], filter_complex: str, out_label: str)`, where
`opts` is a small dataclass carrying everything the stages need (flags, noise sample
time, measured loudnorm values or None, quiet-block timeline from Phase 6, sample
rate). **No subprocess calls in this module** — it only builds strings, so it is
unit-testable (fast tier).

Inputs and chain, in order (omit stages whose flag is off):

1. Inputs: `-i denoised.wav` as `[0:a]`; for profile hiss also
   `-ss {noise_at} -t 1 -i denoised.wav` as `[1:a]` (same file, no separate sample
   file needed); for music mix `-i music.wav` as `[2:a]` (renumber as needed).
2. **Profile hiss:** `[1:a][0:a]concat=n=2:v=0:a=1[j]; [j]asendcmd=c='0.0 afftdn sn
   start; 0.9 afftdn sn stop',afftdn=nr=…:nf=…:gs=…[h]; [h]atrim=start=1,asetpts=PTS-STARTPTS[a]`.
   The 1 s prepend/trim lives entirely inside the graph. `asendcmd` targets the filter
   by name; if two `afftdn` instances are in the graph (hiss + declick) give them
   explicit instance names (`afftdn@hiss`, `afftdn@click`) and address those.
   **Patch hiss:** `anlmdn=s=…:p=0.002:r=…`.
3. **loudnorm:** `loudnorm=I=…:TP=-1.5:LRA=11[:measured_…:linear=true]` followed by
   **`aresample=48000`** — loudnorm internally runs at 192 kHz and will otherwise change
   the output rate. Then `--audio_gain`: `volume=…,alimiter=limit=0.97:level=false`.
4. **Music mix:** `[2:a]volume={db}dB[m]; [a][m]amix=inputs=2:duration=first:normalize=0,alimiter=…`.
5. **Declick:** `afftdn@click=nr=…:nf=…` with `enable='…'` from the Phase 6 timeline
   (omit `enable` when the guard is off).
6. **Final measurement in the same pass:** append `ebur128=peak=true` (audio
   pass-through) and parse the `Integrated loudness: I:` and `True peak:` lines from
   the Summary block in stderr (`run_ffmpeg` already returns stderr). This replaces the
   separate "Verifying loudness" pass and measures the *final* audio.

### 5.2 The two passes

- **Analysis pass** (only when `--normalize`): same graph **up to and including the hiss
  stage**, then `loudnorm=…:print_format=json`, output `-f null -`. Parse the JSON.
  This is what two-pass loudnorm requires: measurement of the exact signal it will be
  applied to.
- **Render pass:** full graph with measured values, `-acodec pcm_f32le -ar 48000` to one
  intermediate `final.wav`. Then merge/write/compare as today.

### 5.3 Progress display — must stay clear (owner requirement)

Merging stages into one pass must not make the run harder to follow. The pipeline now
has these *passes*: extract → [separate] → denoise → [analyse] → render → merge. Each
pass still gets its own `[N/M]` header, bar and ✔ line exactly as today. What changes
is that the render pass covers several *stages* at once, and the display must say so.

Requirements:

1. **Plan summary before work starts.** After the input/window lines, print the enabled
   stage chain once, e.g.
   `Stages: extract → denoise (atten 20) → hiss (profile, nr 12) → loudnorm (−16 LUFS) → declick (nr 12, level-guarded) → merge`.
   Disabled stages are not listed. This is (a) in rule 9.
2. **Pass headers name their stages.** The render pass header reads
   `[5/6] Rendering: hiss → loudnorm → mix → declick` rather than a generic
   "Rendering". The analysis pass header reads `[4/6] Analysing loudness (hiss → loudnorm measure)`.
3. **One bar per pass is accurate, and the label says why.** In a single ffmpeg graph
   every filter is at the same timeline position, so 40 % on the render bar genuinely
   means every stage in it is 40 % through the audio. Label the bar with the chain
   (`Rendering hiss→loudnorm→declick: [███---] 40.0% (24.0s/60.0s) 18.3 sec/sec, ETA 0:02`).
4. **Add ETA** to `ProgressReporter` for every pass (derive from processed/elapsed; show
   `ETA m:ss`; omit when speed is unknown). This applies to extract, denoise and Demucs
   too, not only the render pass.
5. **Completion lines stay per stage.** After the render pass finishes, print one ✔ line
   per stage it contained, with the measured facts that stage produced, e.g.
   `✔ Hiss reduction (profile) — sampled at 3.70s`, `✔ Loudness: −16.1 LUFS, peak −1.4 dBFS (target −16)`,
   `✔ Declick — active on 71 % of the window (level guard)`. The user still sees what
   each stage did; it is just printed after the shared pass instead of between
   separate passes.
6. **`--verbose`** (new flag, default off) keeps today's `Running command: …` lines; by
   default they are suppressed because a single graph command is several hundred
   characters and drowns the progress. Errors always include the failing command.
7. **Non-TTY** output keeps the periodic plain lines, now prefixed with the pass name,
   plus the per-stage ✔ lines.

Acceptance: run `--normalize --reduce_hiss --declick` piped through `cat` and on a TTY;
in both, a reader can answer the four questions in rule 9 from the output alone.
Document the new layout in the README with a sample transcript.
- `--keep_temp` now yields fewer intermediates: `01_raw`, `02_vocals/music`,
  `03_denoised`, `final`, plus `guard_timeline.json` (Phase 6). Say so in the flag's
  help text.
- The near-silence warning (input < −45 LUFS) stays, driven by the analysis pass.
- The "N dB off target" warning stays, now driven by the `ebur128` figure.

### 5.4 Verify

- Fast: graph-builder unit tests for ~6 flag combinations (strings compared exactly).
- Slow: run the old binary (git stash / worktree at `fa3587a`) and the new one on
  `fixture.mp4` with `--normalize --reduce_hiss --declick --separate_music`; compare
  `speech_fidelity`, `speech_to_hiss_db`, `warble`, `clicks_per_sec`, integrated LUFS,
  true peak. Tolerances: metrics within 0.02 / 0.3 dB / 0.1 / 0.5 clicks; LUFS within
  0.3; peak ≤ −1.0 dBFS. The new LUFS should now match the *merged file's* ebur128
  reading within 0.2 dB (today it does not — that is the point).
- Check `/tmp` footprint during a 60 s run: at most `01_raw`, `03_denoised`, `final`
  (plus stems) exist at once.

**Commit:** "Render all post-denoise stages in one ffmpeg graph; measure loudness on
the final audio".

---

## Phase 6 — Adaptive level guard

### Problem it solves

The same `--declick` setting removed 50 % of clicks at a loud passage and destroyed
speech fidelity (0.935 → 0.655) at a quiet one while removing *no* clicks there.
Similarly hiss reduction tuned on loud speech over-processes quiet speech. One global
setting cannot serve a file whose level moves by 10 dB.

### Design (settled: simple guard, inside the graph)

1. **Level timeline.** After DeepFilterNet, compute per-block RMS of `denoised.wav`
   (**stream it** with `soundfile.blocks` — do not load the whole file; this also fixes
   the whole-file load in `find_quietest_region`, which should move onto the same
   streaming pass). Block length `--guard_block_sec` default **2.0**. A block is *quiet*
   when its RMS is below `--guard_quiet_dbfs`, default **−38 dBFS** (the review's quiet
   passage measured −44, the loud one −33). Merge adjacent quiet blocks into spans;
   drop spans shorter than one block.
2. **Declick:** `enable='not(between(t,a1,b1)+between(t,a2,b2)+…)'` on `afftdn@click`,
   i.e. declick runs only on non-quiet spans. If more than ~200 spans would make the
   expression unwieldy, fall back to `asendcmd` toggling `enable` instead — same effect.
3. **Hiss:** use `asendcmd` to set `afftdn@hiss nr` to `--hiss_nr_quiet` (default: half
   of `--hiss_nr`) at the start of each quiet span and back to `--hiss_nr` at its end.
   For patch mode, `anlmdn s` is also runtime-settable (flag `T`); apply the same
   halving. Verify both with `ffmpeg -h filter=…` before relying on them.
4. **Reporting.** Print: "Level guard: N quiet spans, X % of the window (threshold
   −38 dBFS). Declick disabled and hiss reduced there." With `--keep_temp`, write the
   timeline as `guard_timeline.json`.
5. **Flags:** `--level_guard` default **on**; `--no_level_guard` to disable;
   `--guard_quiet_dbfs` (−38), `--guard_block_sec` (2.0), `--guard_min_span_sec`
   (one block; shorter quiet spans are ignored), `--hiss_nr_quiet` (default: half of
   `--hiss_nr`; for patch mode `--hiss_strength_quiet`, half of `--hiss_strength`).
   The guard is a no-op when neither `--declick` nor `--reduce_hiss` is set. All of
   these go in Appendix C.

### Verify

- Slow test from 1.5 (two-level fixture). Also run on the owner's
  `04-random_chat_w_dad.mp4` windows `2m25s/20s` (quiet) and `1m20s/30s` (loud) with
  `--declick --reduce_hiss`: quiet window `speech_fidelity` must be ≥ 0.93 (it was 0.655
  without the guard); loud window `clicks_per_sec` must still drop by ≥ 30 %.
- README: a "Level guard" section with the before/after numbers and the thresholds.

**Commit:** "Add level guard: skip declick and soften hiss reduction on quiet passages".

---

## Phase 7 — Wrap-up

- Bump `pyproject.toml` version to `0.3.0`.
- README: re-run the metrics on the fixture and on the owner's two reference windows,
  replace every quoted number with the regenerated ones, and add a one-line
  "regenerate with: …" note under each table.
- Update `fable_suggested_fixes.md`? **No** — leave it as history. Add a short
  `CHANGELOG.md` with 0.2.0 (the rebuild) and 0.3.0 (this plan).
- Final full run: `./venv/bin/pytest && ./venv/bin/pytest -m slow`, then one real
  60 s render on each reference file, listen-check not required but durations, LUFS and
  peak must be sane.

**Commit:** "Release 0.3.0".

---

## Appendix A — Reference numbers from the review (for regression checks)

Measured on `04-random_chat_w_dad.mp4`, window 7m00s/60s, DeepFilterNet at atten 10,
then loudnorm to −16 LUFS. Reproduce with the Phase 1 metrics before changing anything
in Phases 5–6, so you have a baseline from the *current* code.

| Variant | speech fidelity | speech/hiss dB | warble |
|---|---|---|---|
| source | 1.000 | 13.3 | 5.6 |
| atten 10, no hiss stage | 0.980 | 12.7 | 5.6 |
| atten 30 | 0.841 | 11.1 | — |
| + patch hiss (s .0003, r .0025) | 0.926 | 16.7 | 6.3 |
| + profile hiss nr12 (final file) | — | 22.0 | 6.1 |

Clicks/sec, window 1m20s/30s: source 17.9; atten 10 = 18.1; atten 30 = 12.0;
declick nr12 after atten 20 = 10.0. Window 2m25s/20s with declick: fidelity 0.655 vs
0.935 without — the failure the guard must prevent.

## Appendix B — Known ffmpeg facts relied upon (verified on ffmpeg 8.1.2)

- `afftdn` options: `nr` (0.01–97), `nf` (−80…−20), `gs` (0–50), `sn` start/stop —
  all runtime-settable (`T` flag), so `asendcmd` and `enable=` work.
- `anlmdn`: `s`, `p`, `r` runtime-settable; `m` (smooth) has **no measurable effect** —
  do not spend time on it.
- `loudnorm` resamples to 192 kHz internally; always follow it with `aresample=48000`
  or `-ar 48000`.
- `-shortest` with `-c:v copy` overshoots; always pass `-t`.
- Case-insensitive filesystem: never create two scratch files whose names differ only
  by case (this bit the review once).
- `ffprobe … -show_entries packet=pts_time,flags -read_intervals A%B` lists keyframes
  with a `K` flag; the fixture's are every 2.000 s.

## Appendix C — Every threshold, its flag, and its default

Rule 8 in force: if it changes the audio or triggers a warning, it is here and it is a
flag. Opus: keep this table in sync with `build_parser()` — a fast test should assert
that every flag named here exists in the parser.

| Threshold | Flag | Default | Introduced |
|---|---|---|---|
| DeepFilterNet attenuation cap | `--atten_lim_db` | 20 dB (`none` = unlimited) | exists; default changes in Phase 3 |
| Denoiser chunk length | `--chunk_len_sec` | 10 s | exists |
| Denoiser chunk crossfade | `--crossfade_sec` | 0.5 s | exists |
| Loudness target | `--target_lufs` | −16 LUFS | exists |
| True-peak ceiling for loudnorm | `--target_tp` | −1.5 dBTP | Phase 3 |
| Loudness range target | `--target_lra` | 11 LU | Phase 3 |
| Limiter ceiling (gain / mix) | `--limiter_ceiling` | 0.97 | Phase 3 |
| "Close to silence" warning | `--silence_warn_lufs` | −45 LUFS | Phase 3 |
| "Off target" warning | `--loudness_tolerance_db` | 2.0 dB | Phase 3 |
| Profile-hiss strength | `--hiss_nr` | 12 dB | exists |
| Profile-hiss noise floor | `--hiss_nf` | −40 dB | Phase 3 |
| Profile-hiss gain smoothing | `--hiss_gain_smooth` | 0 | exists |
| Noise sample position | `--noise_profile_at` | auto (quietest) | exists; snap fix Phase 2 |
| Noise sample length | `--noise_profile_len` | 1.0 s | Phase 2 |
| "No clear gap" warning margin | `--noise_profile_min_gap_db` | 10 dB | Phase 2 |
| Patch-hiss strength / window | `--hiss_strength` / `--hiss_window` | 0.0003 / 0.0025 s | exist |
| Declick strength / floor | `--declick_nr` / `--declick_nf` | 12 dB / −28 dB | exist; nr default changes in Phase 3 |
| Music bed level | `--music_level_db` | −12 dB (`off`) | exists |
| Demucs auto-segment trigger | `--separation_auto_segment_sec` | 600 s | Phase 3 |
| Demucs segment length | `--separation_segment` | model default (7 s when triggered) | exists |
| Level guard on/off | `--level_guard` / `--no_level_guard` | on | Phase 6 |
| Quiet-block level | `--guard_quiet_dbfs` | −38 dBFS | Phase 6 |
| Guard block length | `--guard_block_sec` | 2.0 s | Phase 6 |
| Minimum quiet span | `--guard_min_span_sec` | = block length | Phase 6 |
| Hiss strength on quiet spans | `--hiss_nr_quiet` / `--hiss_strength_quiet` | half of the loud value | Phase 6 |
| Video re-encode quality | `--video_crf` | 18 | exists |
| Merged audio bitrate | `--audio_bitrate` | 192k | exists |
| Preview length | `--compare` | 20 s | exists |

Not thresholds, but new switches from this plan: `--verbose` (Phase 5), `--hiss_method`
(exists).
