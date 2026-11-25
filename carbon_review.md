# Carbon Code & Documentation Review — Video Processing (`video-audio-cleaner` / Video Master)

**Reviewed branch:** `editor-stitch-clean-budget` (`49515f9`)  
**Scope:** 100% of Python package modules (`video_audio_cleaner/`), FastAPI GUI backend (`video_audio_cleaner/gui/`), Svelte 5 + TypeScript frontend (`gui/`), standalone video restoration scripts (`improve_video.py`, `ai_restoration.py`, `extract_and_filter_audio.py`, `setup*.sh`), benchmark/utility scripts (`scripts/`), test suites (`tests/`, `gui/src/**/*.test.ts`), and project documentation (`README.md`, `CHANGELOG.md`, `docs/*.md`, root planning notes).

---

## Table of Contents

1. [Executive Summary & Codebase Strengths](#1-executive-summary--codebase-strengths)
2. [What Features & Bugs Should Be Fixed?](#2-what-features--bugs-should-be-fixed)
   - [2.1 Critical & High-Severity Bugs](#21-critical--high-severity-bugs)
   - [2.2 Medium-Severity Bugs & Half-Wired Features](#22-medium-severity-bugs--half-wired-features)
   - [2.3 Security, Concurrency & Resource-Lifecycle Gaps](#23-security-concurrency--resource-lifecycle-gaps)
   - [2.4 Standalone Video Upscaler (`improve_video.py` / `ai_restoration.py`) Fixes](#24-standalone-video-upscaler-improve_videopy--ai_restorationpy-fixes)
   - [2.5 Test Suite & Setup Portability Fixes](#25-test-suite--setup-portability-fixes)
3. [What Can Be Improved? (Architecture, Performance, UX & Docs)](#3-what-can-be-improved-architecture-performance-ux--docs)
   - [3.1 Core Pipeline & Performance Improvements](#31-core-pipeline--performance-improvements)
   - [3.2 Frontend Architecture & UX / Accessibility Improvements](#32-frontend-architecture--ux--accessibility-improvements)
   - [3.3 Documentation, Packaging & Repository Hygiene](#33-documentation-packaging--repository-hygiene)
4. [What Features Might Be Added?](#4-what-features-might-be-added)
   - [4.1 Tier 1 — High-ROI Completions (Already Planned or Half-Wired)](#41-tier-1--high-roi-completions-already-planned-or-half-wired)
   - [4.2 Tier 2 — Planned Editor & Media Workflow Capabilities](#42-tier-2--planned-editor--media-workflow-capabilities)
   - [4.3 Tier 3 — Strategic New Capabilities](#43-tier-3--strategic-new-capabilities)
5. [Questions for You](#5-questions-for-you)

---

## 1. Executive Summary & Codebase Strengths

Before listing issues, it is worth highlighting how well-engineered the core of this repository is:

- **Clear domain principles:** Keeping speech cleaning **discriminative (mask-based)** rather than generative preserves speaker identity on irreplaceable family/wedding recordings, and keeping inference **100% local** avoids privacy and cloud dependencies.
- **Clean CLI-as-contract architecture:** Every heavy GUI job shells out to a first-class CLI (`clean-audio`, `clean-audio-tune`, `clean-audio-export`, `clean-audio-subtitles`, `video_audio_cleaner.prep`) over newline-delimited `--progress json`. Every GUI action can be inspected and reproduced in a terminal.
- **Thoughtful signal-processing & media details:** Sample-accurate `afftdn` / `arnndn` latency trimming (`graph.py`, `postprocess.py`), `.partial` atomic output promotion (`cli.py`, `export.py`), stream-copy verification with automatic re-encode fallback (`export.verify_copy`), anamorphic SAR handling (`probe.py`, `export.py`), and shared cross-language JSON test vectors (`tests/data/joins.json`, `canvas.json`, `subtitle_mapping.json`) are standout engineering choices.

At the same time, rapid feature growth from `0.4.0` through the `Unreleased` **Video Master** work (multi-video projects, soft cuts, zoom/glides, size-budget exports, music gating, memory budgets, and Hebrew+English subtitles) has introduced **concrete sync/state bugs**, **half-wired UI/backend features**, **localhost cross-origin exposure on `/media/*` and SSE**, and **significant drift between the modern `video_audio_cleaner` package, `README.md`, and the legacy `improve_video.py` video restoration script**.

---

## 2. What Features & Bugs Should Be Fixed?

### 2.1 Critical & High-Severity Bugs

#### [FIX-01] Crossfade clamping mismatch between Python `export.clips()` and TypeScript `edit.place()` (Timeline / Audio / Video Desync)
- **Severity:** High
- **Where:** `video_audio_cleaner/export.py:55-68` vs. `video_audio_cleaner/export.py:150, 194, 676` and `gui/src/lib/edit.ts:131` (also noted in `docs/subtitles-plan.md:92`)
- **Problem:** When placing clips on the timeline:
  - TypeScript `place()` (`gui/src/lib/edit.ts:131`), Python `joins()` (`export.py:150`), `owned_spans()` (`export.py:194`), and `video_graph()` (`export.py:676`) clamp a crossfade to half of each neighbouring clip: `min(fade, prev_len / 2, cur_len / 2)`.
  - However, `export.clips()` (`export.py:62-63`) subtracts the **unclamped** `transition(c)` (`t -= fade`)!
  - Because `export.duration()`, `audio_graph()` music ducking/trimming, and `project_cues()` rely on `export.clips()`, trimming a clip shorter than `2 * transition_in.sec` causes **exported audio/subtitles/total duration to drift out of sync with exported video (`video_graph`) and the GUI timeline**.
- **Recommendation:** Clamp `fade = min(transition(c), prev_length / 2.0, length / 2.0)` inside `export.clips()` so `clips()`, `video_graph()`, `owned_spans()`, and `gui/src/lib/edit.ts` agree identically, and add a shared test case to `tests/data/`.

#### [FIX-02] Clicking "Re-run…" in Compare / Source Details inverts the "Remove background music" setting
- **Severity:** High
- **Where:** `gui/src/routes/Compare.svelte:217`, `gui/src/enhance/SourceDetails.svelte:63`, `gui/src/routes/NewRun.svelte:27`
- **Problem:**
  - `NewRun.svelte:27` initializes `removeMusic = $state(initial.music === "remove")`.
  - When `removeMusic` is `false` (default), `NewRun` submits `remove_music: false`, which translates to `--separate_music --music_level_db=0` (`params.separate_music = true` — music kept). When `removeMusic` is `true`, `params.separate_music` is `false` (music removed by denoiser).
  - However, `Compare.svelte:217` and `SourceDetails.svelte:63` build the Re-run URL with `music: output.params?.separate_music ? 1 : ""`. Neither `"1"` nor `""` equals `"remove"`!
  - Consequently, clicking **Re-run…** on a clip where background music was **removed** (`separate_music == false`) opens `NewRun` with **"Remove background music" unchecked**, silently flipping the user's setting on re-run.
- **Recommendation:** Pass `music: output.params?.separate_music === false ? "remove" : "keep"` in `Compare.svelte` and `SourceDetails.svelte` (handling `undefined` sensibly for legacy outputs).

#### [FIX-03] Subtitle Editor undo of `merge` deletes the second cue instead of restoring it
- **Severity:** High
- **Where:** `gui/src/edit/subtitles.svelte.ts:78-114` (`docs/subtitles-plan.md:94` partially notes translation loss, actual behavior is worse)
- **Problem:**
  - In `SubtitlesState.edit()`, `before` snapshots cues whose ID appears in `touched` (`for (const o of ops) if (o.id) touched.add(o.id)`).
  - For `{ op: "merge", id }`, `o.id` is only the **first** cue's ID; `subs.merge()` absorbs and deletes `doc.cues[i + 1]` (the **second** cue).
  - Because the second cue's ID was never added to `touched`, `before` does not record it. When the user presses `⌘Z` to undo a merge, `inverse` only restores the first cue—**permanently losing the second cue and its translations**.
- **Recommendation:** Before applying `{ op: "merge", id }`, locate `i = doc.cues.findIndex(c => c.id === o.id)` and snapshot `doc.cues[i + 1]` into `before` as well so `inverse` emits `{ op: "put", cue: secondCue }` alongside restoring the first cue.

#### [FIX-04] `clean-audio-export` CLI `--subtitles_srt` / `--subtitle_tracks` wipes unmentioned subtitle settings
- **Severity:** High
- **Where:** `video_audio_cleaner/export.py:1121-1123`
- **Problem:**
  - `overrides_of(args)` builds `subtitles = {"srt": pick(args.subtitles_srt), "tracks": pick(args.subtitle_tracks)}` whenever either flag is passed, and `run()` replaces `proj["export"]["subtitles"]` wholesale (`export.py:1007-1009`).
  - Passing `--subtitles_srt mixed` alone sets `tracks: []` and drops `default_track`, clobbering `tracks` and `default_track` saved in `project.json`.
- **Recommendation:** Only include `"srt"` or `"tracks"` in the override dict when the corresponding CLI flag is not `None`, and merge into `proj["export"].get("subtitles", {})` rather than overwriting the whole sub-object.

#### [FIX-05] `improve_video.py` runs GFP-GAN *before* Real-ESRGAN, contradicting `ai_restoration.py` and `README.md`
- **Severity:** High
- **Where:** `improve_video.py:619-650` vs. `ai_restoration.py:50-78` and `README.md:767`
- **Problem:**
  - `ai_restoration.py:52,70` initializes `GFPGANer(..., upscale=1, bg_upsampler=None)` specifically *"because we already upscaled the background using Real-ESRGAN"*, and `README.md:767` documents Real-ESRGAN 4× followed by GFP-GAN face enhancement.
  - Yet `improve_video.py:620-649` runs GFP-GAN **first** on tiny low-resolution VHS frames at `upscale=1`, and **then** runs Real-ESRGAN 4× over the output—wasting GFP-GAN's high-res facial detail synthesis and amplifying artifacts.
  - In addition, `improve_video.py:678-686` slices audio into `audio_slice.wav` using `-acodec copy`, which **fails outright in `ffmpeg`** whenever the input MP4 has AAC audio (cannot mux raw AAC into a WAV container).
- **Recommendation:** Swap the stage order in `improve_video.py` so Real-ESRGAN upscales first and GFP-GAN refines faces on the upscaled frames (or pass `realesrgan` as `bg_upsampler`), and change the temp audio slice container from `.wav` to `.mka`/`.m4a` (or decode PCM when writing `.wav`).

---

### 2.2 Medium-Severity Bugs & Half-Wired Features

#### [FIX-06] `project.export.level_clips` is written by the GUI (`StitchDialog`), documented in the Stitch Plan, and ignored by `export.py`
- **Severity:** Medium (Silent No-Op Feature)
- **Where:** `gui/src/components/StitchDialog.svelte:47, 97`, `gui/src/lib/edit.ts:70`, `docs/stitch-and-size-budget-plan.md:458-534`, `video_audio_cleaner/export.py`
- **Problem:** `StitchDialog.svelte` presents a checked-by-default **"Even out loudness across videos"** checkbox that writes `export.level_clips: true` into `project.json`. Neither `export.py` nor `gui/app.py` reads `level_clips` anywhere—the checkbox currently has **zero effect** on exported audio!
- **Recommendation:** Either implement per-clip speech-gated loudness levelling in `export.py` (as designed in `docs/stitch-and-size-budget-plan.md` Part 6) or hide/disable the checkbox until implemented so users aren't misled.

#### [FIX-07] `StorageView.svelte` omits `subtitles` bytes, causing "Added in all" total not to match visible columns
- **Severity:** Medium
- **Where:** `video_audio_cleaner/gui/storage.py:66-68, 92-93`, `gui/src/lib/api.ts:114-132`, `gui/src/components/StorageView.svelte:51-75`, `CHANGELOG.md:31`
- **Problem:** `storage.py` computes `subtitles` disk usage per video and adds `u["subtitles"]` into `totals.extra` ("Added in all"), and `CHANGELOG.md:31` states *"Storage counts subtitles per video"*. However, `StorageRow` in `api.ts` and the table in `StorageView.svelte` never added a `Subtitles` column. Whenever subtitles exist, `Added in all` is larger than the sum of `For editing + Cleaned + Tuning`.
- **Recommendation:** Add `subtitles: number` to `StorageRow` and `StorageUsage.totals` in `gui/src/lib/api.ts` and render the `Subtitles` column in `StorageView.svelte`.

#### [FIX-08] Subtitle Editor keyboard navigation & unsaved debounce bugs (`SubtitlesPanel.svelte`)
- **Severity:** Medium
- **Where:** `gui/src/edit/SubtitlesPanel.svelte:55-68, 112-124`
- **Problem:**
  1. **Broken `Enter`-to-next-cue navigation on translation columns:** `keys()` (`line 119`) runs `panelEl?.querySelectorAll<HTMLTextAreaElement>('textarea[data-lang="' + lang + '"]')[i + 1]?.focus()`, indexing by `rows` index `i`. In `"en"` or `"he"` columns, rows where the spoken language already matches render `<div class="same">same as spoken</div>` instead of a `<textarea>` (`line 278`), shifting all subsequent `<textarea>` DOM indices so `Enter` jumps to the wrong cue or does nothing!
  2. **Lost edits on unmount / tab switch:** `changeText()` debounces saves by `800 ms` in `timers`, with no `onDestroy` flush when switching from the `Subtitles` tab back to `Videos` or closing the project.
  3. **Documented shortcuts missing:** `docs/subtitles-plan.md` §7.4 specifies `⌘⌫` (delete cue) and `⌘⇧N` (add cue after), which are not wired in `keys()`.
- **Recommendation:** Tag each `<textarea>` with `data-row={i}` (or `data-cue-id={r.cue.id}`) and query the next rendered textarea with `row > i`; flush pending `timers` in `onDestroy` and `onblur`; add `⌘⌫` and `⌘⇧N`.

#### [FIX-09] Running subtitle jobs & subtitle coverage bars missing from `VideoStrips.svelte` and `PieceBar.svelte`
- **Severity:** Medium
- **Where:** `gui/src/components/VideoStrips.svelte:32-55`, `gui/src/media/PieceBar.svelte:11-31`, `docs/subtitles-plan.md:350, 428`
- **Problem:** `VideoStrips.svelte` (`pendingFor`) only inspects `clean` and `tune` jobs, ignoring running/queued `subtitles` and `subtitles_translate` jobs, and `PieceBar.svelte` only renders `cleaned`, `tuned`, and `used` lanes (missing `subtitles` coverage).
- **Recommendation:** Extend `pendingFor()` and `PieceBar.svelte` with a subtitle coverage lane so users can see which stretches of a long source video already have transcripts/translations at a glance.

#### [FIX-10] `commands.display()` doesn't recognize `subtitles_cli`, and `clean-audio-batch` lacks `--separate_music`
- **Severity:** Medium
- **Where:** `video_audio_cleaner/gui/commands.py:136-147`, `video_audio_cleaner/batch.py:29-54, 88-92`
- **Problem:**
  1. `commands.display()` maps `cli`, `tune`, `export`, and `prep` to their console entrypoints, omitting `"video_audio_cleaner.subtitles_cli": "clean-audio-subtitles"`. Subtitle jobs in the Jobs drawer show `/Users/.../venv/bin/python -m video_audio_cleaner.subtitles_cli ...`.
  2. `batch.py` (`clean-audio-batch`) has no `--separate_music` / `--music_levels` flags and calls `default_tune_dir(args.output_dir, args.video)` (`separate_music=False`), so CLI batch runs cannot create or auto-discover `_tuning/<stem>-with-music-gated/plan.json` created by the GUI's default mode.
- **Recommendation:** Add `subtitles_cli` to `commands.display()`, and add `--separate_music` / `--no_music_gate` / `--music_levels` to `batch.py`.

#### [FIX-11] `export.check_files()` skips checking `tracks.music` files
- **Severity:** Low–Medium
- **Where:** `video_audio_cleaner/export.py:833-842` (compare `video_audio_cleaner/gui/app.py:669-679`)
- **Problem:** `app.py:_missing()` checks video sources, clip replacement audio, **and** `tracks.music` files, whereas `export.check_files()` only checks video sources and clip audio. On CLI `clean-audio-export`, a missing music file passes `check_files()` and fails deep inside `ffmpeg` with an opaque filtergraph/input error instead of `ProjectError("missing file: ...")`.
- **Recommendation:** Include `for m in project.get("tracks", {}).get("music") or (): need.append(m.get("file"))` in `export.check_files()`.

#### [FIX-12] `projects.add_music()` silently reuses stale audio on filename collision
- **Severity:** Low–Medium
- **Where:** `video_audio_cleaner/gui/projects.py:103-110`
- **Problem:** `dst = folder / source.name; if not dst.exists(): shutil.copy2(source, dst)` — if a user adds `/folderA/track1.mp3` and later adds `/folderB/track1.mp3` to the same project, `dst.exists()` is `True`, so the second song is never copied and plays `/folderA/track1.mp3` instead!
- **Recommendation:** Compare file size + content hash (or suffix `_2`, `_3` when `dst.exists()` and differs from `source`).

---

### 2.3 Security, Concurrency & Resource-Lifecycle Gaps

#### [FIX-13] Cross-origin information & media leak on `/media/*` and `/api/events`
- **Severity:** High (Localhost Web Security)
- **Where:** `video_audio_cleaner/gui/app.py:114-125, 369-388, 827-880`
- **Problem:**
  - The middleware at `app.py:120` checks `request.headers.get("x-vac") != "1"` **only** when `path.startswith("/api/") and path != "/api/events"`.
  - When a user runs `clean-audio-gui` (bound to `127.0.0.1:8787`), any malicious or third-party website open in their browser can embed `<video src="http://127.0.0.1:8787/media/proxy?source=...">`, `<img src="http://127.0.0.1:8787/media/poster?...">`, or `new EventSource("http://127.0.0.1:8787/api/events")`. Browsers automatically send `Host: 127.0.0.1:8787` on cross-origin subresource/SSE requests, bypassing the `Host` check and exposing private home video streams, filenames, and live job events to external sites.
  - Furthermore, `POST /api/sources` (`library.py:257`) does not validate file extensions or home-directory containment before registering a path in `sources.json`, after which `/media/file` will serve it.
- **Recommendation:**
  1. Set an `HttpOnly; SameSite=Strict` random session cookie when serving `/` (`index.html`) — or pass a per-launch token in a meta tag — and require either `x-vac: 1` **or** that `SameSite=Strict` cookie on `/api/events` and `/media/*`, plus `Cross-Origin-Resource-Policy: same-origin` headers on `/media/*`.
  2. Validate `Sec-Fetch-Site` (`same-origin` / `none`) when present on browser requests.
  3. Restrict `POST /api/sources` to existing regular media files (matching `VIDEO_EXTS` or probing cleanly), just like `add_sources()` already does for folders.

#### [FIX-14] Unlocked concurrent JSON writes & autosave lost-update races on `project.json` and `plan.json`
- **Severity:** Medium
- **Where:** `video_audio_cleaner/gui/projects.py:86-93`, `video_audio_cleaner/gui/rename.py:92-111`, `video_audio_cleaner/tune.py:725-741`, `gui/src/routes/Editor.svelte:171-188`
- **Problem:**
  - `subs.py` uses `fcntl.flock` + revision history (`rev`) for safe atomic updates, whereas `projects.py`, `rename.py`, and `tune.py` perform unlocked read-modify-write cycles.
  - Worse, if `Editor.svelte` has a project open and a background/drawer operation (`delete_output` or `rename_output`) updates `project.json` on disk via `retarget_clips()`, the next `600 ms` autosave (`PUT /api/projects/{name}`) in `Editor.svelte` overwrites `project.json` with its stale in-memory copy, resurrecting deleted audio references.
- **Recommendation:** Add an integer `rev` field + `fcntl.flock` to `project.json` (rejecting stale `PUT` with `409 Conflict` so the client reloads or merges), and reload open project state in `Editor.svelte` when SSE broadcasts `library` changes affecting that project.

#### [FIX-15] Event-loop-bound global `asyncio.Semaphore` & unbounded `_locks` dict in `gui/media.py`
- **Severity:** Low–Medium
- **Where:** `video_audio_cleaner/gui/media.py:29-31, 105-110`
- **Problem:** `_slots` stores `asyncio.Semaphore` instances in a module-level dictionary lazily bound to whichever event loop runs first. Across app restarts or multi-loop test fixtures on Python 3.10+, reusing a semaphore bound to a closed loop raises `RuntimeError: ... bound to a different event loop`. Also `_locks` (`dict[Path, threading.Lock]`) never evicts entries.
- **Recommendation:** Store `_slots` on `app.state` (or replace with a `threading.BoundedSemaphore` inside `asyncio.to_thread`, since `ffmpeg` runs synchronously in worker threads anyway!), and use `weakref.WeakValueDictionary()` for `_locks`.

---

### 2.4 Standalone Video Upscaler (`improve_video.py` / `ai_restoration.py`) Fixes

Beyond **[FIX-05]** (GFP-GAN/Real-ESRGAN order & `-acodec copy` `.wav` bug), the standalone video tool has several issues left over from before `video_audio_cleaner` matured:

| ID | Location | Issue | Suggested Fix |
|---|---|---|---|
| **FIX-16** | `setup_ai.sh:12` | Installs PyTorch from `https://download.pytorch.org/whl/cpu`, forcing CPU-only wheels (or failing on macOS arm64) even though `ai_restoration.py:41` expects Apple Silicon `mps` acceleration! | Install standard PyPI `torch torchvision` on macOS arm64 so Metal (`mps`) is enabled out of the box. |
| **FIX-17** | `improve_video.py:343-371` | Duplicate legacy `parse_time_str` has the exact silent-truncation bugs fixed in `video_audio_cleaner/timeparse.py` (`"1h20m"` parses as `20m`, ignoring `1h`!; `"5m30"` ignores `30`!; negative values allowed). | Import `parse_time` from `video_audio_cleaner.timeparse` instead of duplicating it. |
| **FIX-18** | `improve_video.py:389` | Hardcoded personal default path `default="/Users/ofir/Documents/video_processing_temp/04-.mp4"`. | Make `--input_video` a required positional or flag argument without a personal path. |
| **FIX-19** | `improve_video.py:553-674` | Extracts the **entire video** to uncompressed PNG frames in `/tmp/video_improve_*` (`input_frames` + 4× `output_frames`). A 10-minute clip at 4× upscale easily writes **50–150+ GB** to `/tmp`, exhausting SSD space. | Stream raw RGB24 frames between an `ffmpeg` decoder pipe and an `ffmpeg` encoder pipe frame-by-frame (zero PNG disk bloat), writing output via `.partial` with `procs.py` process-group cleanup. |
| **FIX-20** | `improve_video.py:37` | Garbled mixed-encoding error string `破坏/Error: ffmpeg is not installed`. | Replace with clean English error output via `console.py`. |

---

### 2.5 Test Suite & Setup Portability Fixes

Running the test suites during review produced:
- **Frontend (`gui`):** `14/14` files (`105/105` tests) passed; `svelte-check` passed with `0 errors, 0 warnings`.
- **Python (`pytest`):** `279 passed`, `1 failed`, `101 fixture setup errors` in headless/sandboxed execution due to two small portability bugs:

1. **[FIX-21] `video_audio_cleaner/memory.py:68-82` (`_table` returns `{}` instead of `None` when `ps` fails):**
   - `memory.room_for()` (`line 111`) explicitly handles `our_usage() -> None` (*"unknown: never blocks"*, tested at `tests/test_memory.py:27-28`).
   - However, when `ps -axo` fails with non-zero exit code (e.g., restricted container/sandbox), `_table()` returns `{}` instead of `None`, so `usage_of()` returns `0` bytes rather than `None`, breaking `test_usage_counts_our_children` and silently reporting 0 B RSS.
   - **Fix:** Check `if r.returncode != 0: return None` in `_table()`, propagate `None` through `tree()` / `usage_of()`, and `pytest.skip("ps unavailable")` in `test_usage_counts_our_children` if `memory.our_usage() is None`.
2. **[FIX-22] `tests/fixtures.py:57-68` (`have_tools()` checks `shutil.which("say")` without verifying `say` can synthesize audio):**
   - In headless SSH/CI/sandboxed macOS environments without CoreAudio daemon access, `/usr/bin/say` exists on `PATH` but writes a `0`-sample AIFF, crashing `np.abs(sentence).max()` with `ValueError: zero-size array` across all 101 `fx`-dependent tests.
   - **Fix:** Either probe `say` on a 1-word temp file inside `have_tools()` **or** fall back to deterministic synthetic formant/harmonic speech-cadence audio in `fixtures.py` when `say` produces 0 samples so the entire integration suite can also run on Linux CI and headless macOS!
3. **[FIX-23] `setup.sh:86` omits `subtitles` extra, and `scripts/readme_numbers.py:49` leaks `/tmp/readme_numbers_*`:**
   - Update `setup.sh` to install `.[separation,subtitles,gui,dev]` (or offer `--with-subtitles`) so `silero-vad` is present for the GUI's Subtitles workspace, and wrap `mkdtemp` in `scripts/readme_numbers.py` with `tempfile.TemporaryDirectory`.

---

## 3. What Can Be Improved? (Architecture, Performance, UX & Docs)

### 3.1 Core Pipeline & Performance Improvements

1. **Cache DeepFilterNet and Silero VAD models across windows/chunks in a single process:**
   - **Where:** `video_audio_cleaner/denoise.py:180` (`denoise_file`), `video_audio_cleaner/tune.py:334` (`evaluate_window`), `video_audio_cleaner/transcribe.py:88` (`speech_regions`)
   - **Impact:** During `clean-audio-tune`, `evaluate_window()` calls `denoise_file()` **3 times per window** (`10 dB`, `20 dB`, `none`), and `denoise_file()` calls `init_df()` from disk every single time! For a 6-window tune, DeepFilterNet weights and state are reloaded **18 times**. Caching `(model, df_state)` in a module-level singleton (keyed by model path) and caching `load_silero_vad()` in `transcribe.py` will cut multi-window autotune and multi-part transcription overhead noticeably.
2. **Avoid redundant WAV copies/resampling in `tune.py` & `cli.py`:**
   - `evaluate_window()` (`tune.py:334`) runs DeepFilterNet at 48 kHz, writes WAVs to disk, reads them back, resamples to 16 kHz for metrics, and runs `ffmpeg` `afftdn` processes sequentially per candidate. Keeping the 48 kHz float array in memory for the candidate sweep when `--keep_candidates` is off avoids unnecessary disk churn.
3. **Adapt translation memory budget (`TRANSLATE_GB = 22`) to 16 GB Apple Silicon Macs:**
   - **Where:** `video_audio_cleaner/memory.py:33`, `video_audio_cleaner/translate.py:16`, `video_audio_cleaner/gui/jobs.py:182`
   - **Impact:** `README.md` highlights M3 MacBook Air (16 GB) support, while `docs/subtitles-plan.md` targets M5 Pro (48 GB) with `gemma3:27b` (`TRANSLATE_GB = 22`). On a 16 GB Mac, `budget_bytes()` is `8 GB`, so `memory.never_fits(22)` is `True` and `jobs.py` still runs it one-at-a-time, **failing or swapping heavily** if Ollama tries to load `gemma3:27b`! Auto-selecting or recommending a lighter model tier (e.g., `gemma3:12b` or `gemma3:4b`) when `memory.total_bytes() <= 24 * GB` would make subtitles work smoothly across both machines.
4. **Harden `ollama` lifecycle in `translate.py`:**
   - `translate.ensure_ollama()` (`translate.py:72-86`) spawns `ollama serve` with `start_new_session=True` if `127.0.0.1:11434` is closed, leaving the background daemon running indefinitely after translation finishes (eating 15–20 GB of unified memory until Ollama's idle timeout expires). Passing `OLLAMA_KEEP_ALIVE=2m` (or sending a post-run unload request `"keep_alive": 0` when Video Master started the server itself) reclaims RAM immediately for subsequent `clean` or `export` jobs.

### 3.2 Frontend Architecture & UX / Accessibility Improvements

1. **Decompose `gui/src/routes/Editor.svelte` (currently 1,450 lines):**
   - `Editor.svelte` currently owns project state, undo/redo stacks, autosave, dual `<video>` + Web Audio sync, source preview + zoom cropper, subtitle fetching & cue navigation, sound-choice popovers, keyboard shortcuts, and 9 modal dialogs.
   - Extracting focused modules (`SourceMonitor.svelte`, `ProgramMonitor.svelte`, `ClipInspector.svelte`, `SoundPickerMenu.svelte`, and a `projectState.svelte.ts` controller, matching `docs/gui-editor-redesign.md` Phase D) will make state transitions (and fixing race conditions like **[FIX-14]**) dramatically easier to reason about and test.
2. **Render audio waveforms on timeline clips using the `.peaks` data you already generate:**
   - `prep.py` already computes `VACP` min/max peak files and `/media/peaks` + `gui/src/lib/peaks.ts` already parse them—yet `Timeline.svelte` draws plain flat colored blocks for clips! Rendering the cached peaks canvas inside each clip block gives instant visual cueing for speech pauses, applause, and cut points with zero backend work.
3. **Unify the Enhance workspace around a single `ABDeck` (`docs/gui-editor-redesign.md` Phase B):**
   - `EnhanceWorkspace.svelte` currently embeds legacy full-page components (`routes/Compare.svelte`, `routes/Candidates.svelte`, `routes/NewRun.svelte`) inside `.page` wrappers, recreating separate `ABPlayer` instances when switching tabs instead of keeping playhead/loop/A-B state continuous.
4. **Accessibility & Keyboard Usability Fixes:**
   - **Modal focus management:** `Modal.svelte`, `StitchDialog.svelte`, `FolderBrowser.svelte`, and `AddVideos.svelte` lack `aria-modal="true"`, initial focus placement, focus trapping on `Tab`, and focus restoration to the trigger element on close.
   - **Keyboard-inaccessible controls:**
     - `Scatter.svelte` renders `<g class="dot">` SVG groups with `onclick` only (no `tabindex="0"`, `role="button"`, or `Enter`/`Space` key handler).
     - `Editor.svelte:1058` renders `<span class="clip-sound" role="button" tabindex="-1">` (unreachable via keyboard).
     - `Timeline.svelte` clip selection, reordering, and trim handles have pointer handlers only—adding arrow-key nudge (`[` / `]` or `Alt+←`/`Alt+→` to trim in/out by 1 frame / 0.1 s) would improve both accessibility and editing precision.

### 3.3 Documentation, Packaging & Repository Hygiene

1. **Sync `pyproject.toml` version and `README.md` with the current codebase:**
   - `pyproject.toml:7` is still `0.5.0`, while `CHANGELOG.md` has a huge `Unreleased` feature set (Video Master GUI, multi-video projects, soft cuts, zoom/zoom-glides, size-budget 2-pass HEVC/H.264 export, music gating, memory budgets, and subtitles).
   - `README.md`'s CLI options table (`lines 213-265`) is missing `--autotune`, `--source`, `--keep_candidates`, `--from_candidate`, `--autotune_dir`, `--no_music_gate`, and `--progress`.
   - `README.md:107` and GUI/Export sections don't document `clean-audio-subtitles`, size-budget flags (`--budget`, `--size`, `--codec`, `--height`, `--fast`, `--one_pass`, `--estimate`), zoom/glides, or soft cuts.
   - `README.md:709` states *"Known bugs are pinned as `xfail(strict=True)` tests until fixed"*, which is no longer true (zero `xfail` markers exist).
2. **Clean up root-level historical scratch docs & untracked test artifacts:**
   - Move or archive root planning/session notes (`fable_review_plan.md`, `fable_suggested_fixes.md`, `making-subtitles.md`, `making-subtitles_002.md`) into `docs/archive/` so contributors immediately know `docs/*.md` are the authoritative specs.
   - Add status headers (`Implemented` / `Partially Implemented` / `Superseded by ...`) to `docs/gui-design.md` and `docs/gui-editor-redesign.md` where newer plans (`stitch-and-size-budget-plan.md`, `subtitles-plan.md`) changed decisions.
   - Add `test_frame_*.png`, `output_test_*.png`, and `output_images_*/` patterns to `.gitignore` (or move ad-hoc upscaler test outputs out of repo root).
3. **Add CI workflow (`.github/workflows/ci.yml`):**
   - Run `npm test -- --run && npm run check` (fast, ~1.5 s) and `pytest -m "not slow"` (once **[FIX-21]** and **[FIX-22]** allow `fixtures.py` to synthesize fallback audio without macOS CoreAudio) on every push/PR.

---

## 4. What Features Might Be Added?

### 4.1 Tier 1 — High-ROI Completions (Already Planned or Half-Wired)

1. **Per-Clip Speech-Loudness Levelling on Export (`export.level_clips`):**
   - Complete the already-wired `level_clips` setting (**[FIX-06]**) using speech-active LUFS measurement per clip (`docs/stitch-and-size-budget-plan.md` Part 6) + `GET /api/projects/{name}/levels` so stitched tapes recorded at different camcorder gain levels play at one consistent conversational volume.
2. **Timeline Clip Waveforms + Peak Level Meter (`Meter.svelte`):**
   - Draw `.peaks` waveforms directly on `Timeline.svelte` clips and add a Web Audio `AnalyserNode` stereo peak meter (`-48..0 dBFS` with `-1.5 dBTP` ceiling tick) next to the program monitor (`docs/gui-editor-redesign.md` §7.4).
3. **Subtitle Coverage Bars, `StorageView` Subtitles Column & Offline Setup Check:**
   - Finish `PieceBar` / `VideoStrips` subtitle indicators (**[FIX-09]**), `StorageView` column (**[FIX-07]**), and `clean-audio-subtitles setup --offline_check` (`docs/subtitles-plan.md` §0.5).

### 4.2 Tier 2 — Planned Editor & Media Workflow Capabilities

4. **Source Compression & `_versions/` Slot (`clean-audio-compress`, `docs/gui-editor-redesign.md` §10):**
   - Let users shrink huge 4K/ProRes/DV archival captures into visually lossless HEVC/H.264 working copies in `OUTPUT_DIR/_versions/` with sidecar provenance, and toggle between original and compressed sources non-destructively.
5. **Editor Ergonomics Pack (J/K/L Shuttle, Blade `B`, Magnetic Snap `N`, Ripple Linked Audio):**
   - Standard NLE muscle-memory controls from `docs/gui-editor-redesign.md` §7: `J`/`K`/`L` variable-speed shuttle (`1×`, `2×`, `4×`), `B` blade mode, `N` snapping to clip edges/playhead/markers with a yellow snap line, and frame-step (`←`/`→`).
6. **Multi-Lane Audio Track Schema v2 (Music + Ambience/SFX + Per-Track Mute/Solo/Duck):**
   - Generalize `tracks.music` into named audio lanes (`Music`, `Sounds`) with draggable fade handles and visual ducking curves (`docs/gui-editor-redesign.md` §6–§7).
7. **SRT / WebVTT Import & Optional Burned-In Styled Subtitles:**
   - Import existing `.srt` / `.vtt` files into `_subtitles/<sourceId>/subtitles.json` (preserving bidirectional marks via `subs.from_srt`, which already exists!).
   - Optional burned-in open captions on export for sharing clips to WhatsApp/Instagram/TVs that ignore `mov_text` soft tracks (using ASS rendering when `libass` is available or an `ffmpeg` overlay pass rendered via browser/Pillow/CoreText so stock Homebrew `ffmpeg` works without `libass`!).

### 4.3 Tier 3 — Strategic New Capabilities

8. **Unified Streaming `clean-video-enhance` CLI + Video Master "Picture Enhance" Tab:**
   - Bring `improve_video.py` / `ai_restoration.py` into `video_audio_cleaner` as a proper streaming CLI (`clean-video-enhance`) with `--progress json`, Apple Silicon `mps`/CoreML acceleration, `.partial` output safety, and **split-screen before/after wipe preview** inside the GUI's **Enhance** workspace (`docs/gui-editor-redesign.md` §12).
   - Add VHS-specific pre-filters (optional `bwdif` deinterlacing for interlaced PAL/NTSC captures, chroma noise reduction `hqdn3d`/chroma bleed fix, and stabilization `vidstab`) before upscaling—critical for real camcorder/VHS tapes!
9. **Automatic Scene-Cut / Tape-Break Detection (`clean-audio-scenes`):**
   - Home videos and digitized VHS tapes often merge dozens of separate events/scenes into one 2-hour `.mp4`. Using `ffmpeg`'s `scdet` filter + silence/black-frame detection to auto-propose chapter markers or pre-split timeline clips (without re-encoding) saves hours of manual scrubbing.
10. **Apple Silicon `mlx-whisper` / Persistent `whisper-server` + Hardware-Aware Translation Presets:**
    - Offer `mlx-whisper` (or `whisper.cpp`'s `whisper-server` keep-warm mode) so mixed-language Hebrew/English retries don't reload a 3 GB model per retry pass, and add hardware-tiered translation presets (`Fast / 16 GB Mac` vs. `Max Quality / 48 GB Mac`).
11. **Chapter Markers on Export:**
    - Allow naming markers on the timeline (e.g., *"Ceremony"*, *"Speeches"*, *"First Dance"*) and write standard MP4 chapter metadata (`FFMETADATA1`) during `clean-audio-export` so QuickTime, VLC, and Apple TV show navigable chapters for long wedding/family videos.

---

## 5. Questions for You

To prioritize and scope what we tackle first (once you want changes made), could you share your thoughts on:

1. **Hardware target:** Are you now running exclusively on a high-memory Mac (e.g., 48 GB M5 Pro from `docs/subtitles-plan.md`), or do you still actively use/support 16 GB Apple Silicon Macs (from `README.md`)? *(This affects default model tiers for subtitle translation and memory budgets.)*
2. **Fate of `improve_video.py` & `ai_restoration.py`:** Do you still use the standalone video upscaling/GFP-GAN scripts? Would you like to **(a)** fix their bugs in-place, **(b)** fold them into `video_audio_cleaner` + the Video Master GUI as a streaming `clean-video-enhance` pipeline, or **(c)** archive/deprecate them?
3. **Loudness levelling (`level_clips`):** Since `StitchDialog` already writes `export.level_clips: true` into `project.json`, should completing per-clip speech loudness levelling in `export.py` be near the top of the fix list?
4. **GUI Security Model:** Do you ever expose `clean-audio-gui --browser` over LAN/Tailscale, or is it strictly `127.0.0.1` on your own Mac? *(Even on `127.0.0.1`, adding a `SameSite=Strict` session cookie for `/media/*` and `/api/events` is lightweight and closes cross-origin browser leaks without changing your workflow.)*
5. **Release & Doc Cleanup:** Would you like to cut `v0.6.0` (updating `pyproject.toml`, `README.md`, and archiving root scratch files like `fable_*.md` and `making-subtitles*.md`) right after fixing the high-priority bugs, or keep iterating on `Unreleased` first?
