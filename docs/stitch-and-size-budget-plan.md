# Stitching cleaned videos and exporting to a size budget

Status: **in progress** — parts 1–5, 7 and 8 done, 6 not started (see §0.5). Written 2026-11-11, against commit
`6bc8cf4`.
Self-contained: an implementer who has not seen the discussion can execute it. It is a
slice cut across `docs/gui-editor-redesign.md` (it takes parts of its phases G and H and
is built **before** its phases C–H); where the two disagree about anything listed here,
this document wins. §13 says what the redesign's later phases must carry over.

---

## 0. How to use this document

### 0.1 Read first

| File | Why |
|---|---|
| `docs/gui-editor-redesign.md` §0 | Working rules, what the redesign has built so far |
| `video_audio_cleaner/export.py` | The project model, `plan()`, the audio and video graphs, `run()` — most of this plan extends it |
| `video_audio_cleaner/probe.py`, `gui/library.py` (`probe_source`, `cached_probe`) | Source facts and their cache |
| `video_audio_cleaner/gui/{app,commands,jobs,projects,media}.py` | Job types, project endpoints, audio serving |
| `gui/src/lib/{edit,program,audio}.ts` | Edit model, the timeline player, the decode cache |
| `gui/src/routes/{Editor,Compare}.svelte`, `gui/src/components/{Timeline,ABDeck}.svelte` | Where the new controls go |
| `gui/src/workspaces/{Media,Deliver}.svelte`, `gui/src/media/{SourceDetails,pieces}.ts*` | Entry points for stitching, the Deliver page |
| `tests/test_export.py`, `tests/test_gui_server.py`, `tests/fixtures.py` | Test conventions |

### 0.2 Working rules

Those of `docs/gui-editor-redesign.md` §0.2 apply unchanged. In short: `./venv/bin/…`;
fast tests `./venv/bin/pytest`, frontend `cd gui && npm run check && npm test && npm run
build`; **never run long jobs on the user's real videos unasked** and never write test
output next to source videos; browser checks on a scratch project built from
`tests/fixtures.py`; every long operation is a CLI with `--progress json` first, a job
type second; commit and push only when the user asks; plain-language labels.

### 0.3 What the user asked for

1. Clean the audio of several big files (exists).
2. Stitch them into one longer video and watch the whole thing in the tool.
3. Compress the result to a size budget, e.g. "2 GB per hour": the tool finds the best
   quality the budget allows, exploring settings on **small samples**, never by encoding
   the whole 80 minutes repeatedly.
4. Between clips, the sound should pass softly from one to the next.

Picture enhancement (denoise, upscaling, faces) is **out of scope**: this plan never
alters the picture beyond scaling to a common frame and compression.

### 0.4 Facts about the user's material (read from the probe cache, 2026-11-11)

- The tape transfers are 1280×720, 30 fps, progressive H.264 at about 2 Mbit/s:
  **about 1 GB per hour already**, 30–90 minutes each.
- One camera file is 4096×2160, 29.97 fps, 24 Mbit/s: about 11 GB per hour.

Consequences that shaped the design:

- For the tapes a budget of 2 GB/hour is *more* than the files contain. Re-encoding them
  cannot add quality and can only lose some; the best stitch is a **lossless one**
  (video packets copied, §9). The budget machinery must recognise this instead of
  inflating the file (§10.3, "source ceiling").
- For the 4K file the budget bites hard (4.4 Mbit/s for 4K); resolution matters there
  (§10.6).
- Whole-file cleans are long. Anything that decodes a whole file's audio into memory
  (the timeline player, Compare) breaks at this length: 60 minutes of 48 kHz stereo
  float is 1.4 GB per buffer (§5, §6).

### 0.5 Implementation notes

Where the implementation differs from the text below, this list wins.

**Part 1 (groundwork)** — done.
- The user's tape transfers **do** store 16:15 pixels (`sample_aspect_ratio` of a real
  output read on 2026-11-11), so they display 1366×720, not 1280×720.
- Old embedded probes are refreshed by `export.ensure_probes(project)` (in memory,
  called by `run()`, `--plan` and the GUI's plan endpoint) rather than by the GUI
  rewriting the project file. The GUI's library re-probes lazily as before (cached
  probes below `PROBE_VERSION` count as missing).
- Previews are squared too (`prep.SQUARE_PIXELS`): the editing proxy, thumbnails,
  posters and filmstrip frames. Previews made before this keep their old shape until
  made again (delete `_gui/cache/` entries, or re-prepare).
- Fixtures: `fx_twin`, `fx_other`, `fx_anamorphic` session fixtures in
  `tests/conftest.py`.

**Part 2 (lossless stitch)** — done.
- The user's four tape files have identical video formats (same `extradata_md5`,
  1280×720 16:15, 30 fps, High@4.0), so stitching them can be lossless. The 4K camera
  file differs (4096×2160, 29.97 fps) and forces an encode when mixed in.
- Extra beyond the plan: `export.picture_runs()` treats back-to-back clips of the same
  video (no crossfade) as one continuous picture, so a sound change inside a file
  (cleaned stretch → original stretch) does not need a keyframe and does not break
  copying. `concat_list()` writes one entry per run; `verify_copy()` checks the joins
  between runs only.
- The GUI's plan endpoint swaps old embedded probes for the library's cached ones
  (`_fresh_probes` in `gui/app.py`) instead of probing on every call.
- `fx_twin` must come from the same lavfi source as `fx.video` (`testsrc2` + a hue
  turn): another pattern source changes the colour metadata and so the decoder setup.

**Part 3 (soft sound)** — done.
- `export.joins()` returns a list aligned with `clips()` (index 0 is always
  `{"kind": "cut", "sec": 0}`), and so does `joinsOf()`; `tests/data/joins.json` holds
  that full list per case.
- The preview's fades come from two pure functions in `lib/edit.ts`: `voiceShape()`
  (how far a clip's sound starts early / ends late, and its fades, in timeline time)
  and `gainAt()`; `Program` turns them into `setValueCurveAtTime` curves. Crossfades go
  through the same path (linear shape), replacing the old ramp code.
- The resolver's `clipAudio(clip, pre, post)` asks for original sound with the spare
  around it (`media.original(source, in − pre, length + pre + post)`, offset `pre`);
  cleaned sound already has it (the whole output is decoded).
- Timeline marker: a white diamond on the cut line, drawn outside the clip box (which
  clips its contents).
- **Changed from §7.1 after trying it:** a blend no longer needs half its length on
  *both* sides. The overlap is centred when it can be, else shifted toward the side
  with spare sound (`blend_lead()` / `blendLead()`; joins carry `"lead"`: seconds of
  the overlap before the cut). It dips only when the two sides together have less than
  its length. Reason: in a whole-video stitch, a cleaned piece ends exactly where the
  original sound resumes; under the old rule every such join dipped (near-silence
  mid-conversation), now it blends inside the original sound. A dip remains for the
  end of one file meeting the start of the next.

**Part 4 (stitch entry)** — done.
- `stretches(source)` / `stitchClips(items, soft)` in `lib/edit.ts`: newest cleaned
  output per stretch (elementary segments between all window edges), slivers under
  0.5 s folded into a neighbour whose sound covers them.
- Media: multi-select (`media/selection.ts`; ⌘-click, ⇧-click, ⌘A, Esc; arrows with ⇧
  extend), details panel for several shows "Stitch into a new timeline…";
  `media/StitchDialog.svelte` (order by drag or arrows, whole/pieces per partly cleaned
  video, sound between clips, format notes, preparation note).
- `media/AddToTimeline.svelte`: a menu used for a whole video (Editing section) and
  for each cleaned piece; queues preparation when the video is not prepared.
- New stitched timelines get `export.level_clips: true` (used from part 6).

**Picking clips (added after part 4, at the user's request)** — done.
- The user wants to choose clips from the videos directly, anywhere (cleaned or not),
  and for a clip inside cleaned output(s) to choose which version's sound to use.
- Edit has a **Videos** panel (`edit/VideoStrips.svelte`) above the timeline: one
  compact `PieceBar` per video of the timeline (the keys of `project.sources`, which may
  now hold videos without clips). Click a strip: that moment in the Source viewer;
  drag: In/Out marks on that video. `PieceBar` gained `compact` (no labels, no tuned
  lane) and `openPieces` (false: a click on a piece seeks instead of opening it).
- With marks set, a **Sound** select lists every cleaned output covering the whole
  clip (`coveringOutputs()`, newest first) plus "original sound"; tuned windows holding
  the clip with kept candidates get an "other candidates…" link to their Candidates
  view (render one there, then it appears in the list). New clips get a 1 s soft cut
  when the timeline is not empty.
- Clicking a version's bar (`PieceBar` `onpiece`; it does not seek) sets `hearing` in
  the editor: the Source viewer mutes the proxy inside that output's window and plays
  the output's sound in a hidden `<audio>`, following the picture (no correction under
  60 ms, rate 0.97–1.03 above it, a jump above 250 ms: the video clock moves in frame
  steps, and `driftAction`'s tighter thresholds hunted). The version heard is outlined
  and becomes the default Sound for the marks when it covers them. A bar of another
  video switches to that video at the click.
- Names: `versionName()` (`media/pieces.ts`): the tag, or "first version". Used on the
  bars (always labelled; taller rows in compact mode), the Sound menu, the "hearing"
  label and the timeline clip labels ("cleaned · v2" / "original sound").
- **Add videos…** (`edit/AddVideos.svelte`) adds project videos to the list and
  prepares unprepared ones; a video without clips can be removed from the list.
- The stitch dialog (now "New timeline from N videos", Media's button "Make a timeline
  from these…") has **Start with**: nothing (default: pick clips in Edit) / each video
  whole / each video's cleaned pieces. The per-video whole/pieces select is gone.

**More from the user's use (after part 5)** — done.
- Adding many videos: `Library.add_sources(paths, folder)` (one rescan), `POST
  /api/sources/many`, `NativeDialogs.pick(kind, start, multiple)` returns a list,
  `FolderBrowser` gained `onpickmany` (tick files, or the whole folder), drops add every
  video file and stay put.
- Edit layout: timeline above the Videos panel; `project.video_order` (source ids)
  orders the panel; rows drag by their name or go *to top*.
- Clean from Edit: `components/Modal.svelte` hosts `NewRun` (new `onqueued` prop) and
  `Candidates` over the editor; the editor's keys are off while one is open and both
  players pause. `PieceBar` gained `tunedLane` (named "N candidates" bars, which open the
  Candidates panel through `onpiece`) and `pending` (striped placeholders for queued or
  running cleans of that video, from the job list).

**Projects (the user's word for a set of videos and their stitch)** — done.
- UI naming only (code and files unchanged): timeline → *project*, output folder →
  *library folder*. Read "timeline" in this document as "project" where it means the
  saved edit; "the project's timeline" is its sequence of clips.
- `projects.summary()` adds `videos` (`projects.video_ids()`: `video_order` first, then
  the rest of `sources`). Media has a bin per project; `media/AddToProject.svelte`
  replaces `AddToTimeline` (videos join `sources` + `video_order` without clips; a piece
  becomes a clip); `routes/Projects.svelte` is the Projects page with posters.

**Faster estimate (after the user's first real estimate, 2026-11-20)** — done.
- The first estimate on "Wedding re-edited" (4K canvas) ran 8 min 51 s and then failed:
  `size_words()` called itself for sizes ≥ 1 GB (a bad search-and-replace). Fixed, tested.
- The user wants an estimate in 30–60 s. **Changed from §10.4:** samples are 6 × 2 s above
  1080 lines, else 10 × 3 s (plus up to 2 for long clips); no CRF search: one round at the
  ceiling, then (size mode) one at the CRF a straight line on (CRF, log bitrate) predicts,
  whose result refines the CRF without encoding again (`crf_for`). If that trial is still
  25 % over the budget (CRF clamped at 40), a trial at the budget's bitrate is scored
  instead. Quality is scored with both pictures at ≤ 1080 lines (VMAF's model; twice as
  fast), only for the round used. Two samples run at once; references are always kept.
  The analysis pass is no longer timed: measured on ffmpeg 8.1 it takes about as long as
  the encode (x265 and x264, fast and medium). The height suggestion encodes the same
  references scaled down, at the bitrate when the main round used it.
- Measured on a synthetic 4K-canvas stitch (720p tapes + a 4K clip, 5 min): 21.7 s at
  2 GB/h, 34 s at 0.25 GB/h (with the height round); a 1-minute export predicted 32.96 MB /
  1.5 min, wrote 32.94 MB in about 1.2 min. Estimates record `estimate_sec`.
- After the user's first real estimate (4K canvas, mostly 720p tapes, 2 GB/h: VMAF 72,
  1440p scored 80 but was not suggested): the smaller frame is now tried at **1080** for
  any taller canvas (720 for 1080) and always at the budget's bitrate, no overshoot rule.
  Estimates also carry `audio`, `footage` (the height carrying the most time; a note when
  it is enlarged) and `quality_low_video`. The card lists Picture / Weakest / Sound / Time
  in words, and refreshes when any estimate or export of the project finishes (also
  "Run again" in the queue).

**Part 8 (Deliver)** — done.
- The settings are one component, `gui/src/deliver/ExportSettings.svelte` (helpers in
  `deliver/budget.ts`), shown on Deliver and, **unlike §11**, also kept in the editor as
  `edit/ExportDialog.svelte` (Export… opens it in place rather than navigating away).
- The estimate card shows sizing.py's notes (they carry the mode sentence) rather than a
  second sentence; the estimate job is watched by its id, no change to `state.svelte.ts`.
- `stale` is also true when the saved project has no budget any more.
- Verified on a scratch server (synthetic 2-minute stitch, 12 MB): estimate in 27 s,
  predicted 11.87 MB, exported 11.95 MB, two passes.

**Part 7 (budget CLI)** — done (engine and CLI only; GUI is part 8).
- §10.7 verified on ffmpeg 8.1.2: `libvmaf` present (8 s of 720p scores in ~1 s); x265
  two-pass at 1500k gave 1489k (−0.8 %), x264 1499k. QuickTime/Google Photos/YouTube not tried.
- Codec: absent `export.codec` means HEVC **under a budget**, H.264 without (§1 vs §2).
- Sidecar `results.mode` stays copy/encode; the budget's mode is `results.budget_mode`.
- Sample weights: a long clip's own sample takes its time from the parts it overlaps, so
  weights always sum to T. SSIM thresholds 0.98/0.96/0.93; "weaker" gap 0.02 SSIM.
- A copy whose join check fails re-estimates as an encode (never top quality unchecked).
- Measured on a 4-minute synthetic 720p stitch: predicted 66.10 MB / actual 65.91 MB
  (source-capped), 29.67 MB / 29.79 MB for a 30 MB budget; time predicted 1.6 min, ~1.4 actual.
- Found, not fixed (part 2): sources with B-frames make the concat join emit non-monotonic
  DTS warnings, so `verify_copy` rejects lossless joins of such files and they get encoded.

**Part 5 (long preview)** — done.
- `lib/voices.ts`: `StreamVoice` (an `<audio>` element through
  `createMediaElementSource` into a gain node that takes the same fade curves) and
  `driftAction()`. `Program` streams any clip whose media is longer than
  `STREAM_AFTER` (180 s), keeps at most the current and next streamed voices
  (`PRELOAD` 3 s ahead), releases played ones, and rebuilds them on seek.
  The resolver's `clipAudio` now also returns `seconds`; long original sound streams
  from the editing proxy (which carries it) when the video is prepared, else from
  `media.original(…)` as an element source (still not decoded).
- Verified in Chromium (Browser pane) on two 12-minute synthetic videos: no decode
  (36 MB heap for a 24-minute timeline), sound within 12 ms of the clock at the join,
  the next voice preloaded, the played one released. **Not verified in WKWebView**
  (the pywebview window); `StreamVoice` falls back to element volume if
  `createMediaElementSource` throws. Opt-in console hook: `__vacVoices = new Set()`
  before playing collects the live voices.
- Compare: `lib/sections.ts` (`SECTION_SEC` 300, `SECTIONED_AFTER` 420,
  `sectionFor()`); `/media/audio` takes `start`/`duration` for `output` and
  `original_for` (`media.output_audio_section()`, re-encoded with an exact cut). Section
  keys are PageUp/PageDown (`⇧[`/`⇧]` are Enhance's previous/next video).
- Found while measuring: `loudness.measure()` made two filtered copies and a running sum
  of every channel (≈ 230 MB per channel for 5 minutes). It now filters sample by sample
  and keeps one energy per 100 ms; results unchanged (tests). A long Compare section went
  from 1.67 GB to 0.56 GB of heap.

---

## 1. Decisions at a glance

| Topic | Decision |
|---|---|
| What a stitched video is | An ordinary timeline (`project.json`, schema 1). No new container. |
| Schema | Stays **1**. All additions are optional fields whose absence means today's behaviour (§2). |
| Getting pieces in | Multi-select in Media → "Stitch into a new timeline…"; "Add to timeline" on any cleaned piece (§4). |
| Picture size of a new stitch | Set explicitly at creation: the largest source frame, the frame rate carrying the most minutes (§3.2). |
| Long preview | The timeline player streams long audio through media elements instead of decoding it (§5). |
| Long Compare | Compare loads a section of a long output, not all of it (§6). |
| Soft sound | New transition type `soft_cut`: the picture cuts, the sound blends. True overlap when both sides have spare sound beyond the cut, else a short fade out/in (§7). |
| Loudness between clips | Optional per-clip levelling to the common target before the final gain (§8). |
| Lossless stitch | Clips from several files are stream-copied when the files' video streams are identical in format (§9). |
| Budget | `clean-audio-export --budget 2GB/h` (or `--size`). Samples decide; the full encode runs once: constant quality when that fits, else two-pass at the budget's bitrate (§10). |
| Codec under a budget | HEVC (`libx265`, tagged `hvc1`) by default; H.264 optional (§10.5). |
| Quality report | VMAF on the samples, shown as words; SSIM fallback (§10.4). |
| One generation | Stitch and compress are one encode from the originals. Never export, then compress the export. |
| Where it lives in the GUI | Deliver gets real settings (keep quality / fit a size), an Estimate and an Export button; the editor's export dialog goes (§11). |
| Version | Entries under "Unreleased" in `CHANGELOG.md`; README updated per part. |

---

## 2. Project format additions (schema stays 1)

```jsonc
{
  "canvas": {"width": 1280, "height": 720, "fps": 30},   // set at creation for stitches (§3.2)
  "tracks": {"video": [
    {"id": "c2", "source": "…", "in_sec": 0, "out_sec": 1791.08,
     "audio": {"file": "/abs/out.mp4", "output": "out.mp4", "start_sec": 0,
               "duration_sec": 1791.08,                  // NEW: length of that audio (§7.2)
               "gain_db": 0},
     "transition_in": {"type": "soft_cut", "sec": 1.0}}  // NEW type beside "crossfade"
  ]},
  "export": {
    "level_clips": true,                                 // NEW (§8); absent = false
    "budget": {"per_hour_gb": 2.0},                      // NEW (§10); or {"total_gb": 2.5}; absent/null = none
    "codec": "hevc",                                     // NEW: "h264" | "hevc"; absent = "h264"
    "height": null,                                      // NEW: scale the canvas down to this height; null = canvas
    "fast": false                                        // NEW: hardware encoder under a budget (§10.5)
    // existing: encoder, crf, preset, audio_bitrate, target_lufs, target_tp, output
  }
}
```

Rules:

- `export.transition(clip)` (the timeline **overlap**) stays "seconds when
  `type == "crossfade"`, else 0". A `soft_cut` never changes clip positions or the
  timeline's length.
- Unknown `transition_in.type` → `ProjectError` (never silently a cut).
- `gui/src/lib/edit.ts` mirrors every addition (`Clip.transition_in` becomes
  `{ type: "crossfade" | "soft_cut"; sec: number } | null`; `ClipAudio.duration_sec?`;
  `Project.export` gains the new keys). `transitionOf()` keeps returning the overlap
  (crossfade only); add `softCutOf(clip)`.
- `projects.new()` is unchanged (old defaults). The stitch flow (§4) writes the new
  fields.

---

## 3. Groundwork

### 3.1 Probe additions (`video_audio_cleaner/probe.py`)

Add to the `video` dict:

| Field | From | Used by |
|---|---|---|
| `sar` | `sample_aspect_ratio` as a float (`"16:15"` → 1.0667; missing, `0:1`, `1:1` → 1.0) | §3.3 |
| `profile`, `level` | stream `profile`, `level` | §9 |
| `extradata_md5` | `ffprobe -show_data_hash md5 -show_entries stream=extradata_hash` (strip the `MD5:` prefix) | §9 |
| `time_base` | stream `time_base` (string) | §9 |

Add at top level: `creation_time` (format tag `creation_time`, ISO string or null) and
`audio.bit_rate`.

The probe cache (`library.probe_source`) has no version. Add `"probe_version": 2` to
the dict `probe()` returns; `cached_probe()` treats a cached dict with a lower or missing
version as absent, so sources are probed again lazily. Projects embed probes in
`sources[id].probe`; `export` must tolerate old embedded probes (every new field is
read with `.get`) and the GUI refreshes a project's embedded probes from the library
when it opens a timeline (replace `sources[id].probe` when the library's probe has a
higher `probe_version`; this is a silent save).

Tests: fields present for the fixture; `sar` parsing table; an old cached probe is
re-probed.

### 3.2 Canvas suggestion

Pure function, in both languages with one shared test table (see §12.1):

`suggest_canvas(clips) -> {width, height, fps}` where each item is
`{display_width, height, fps, seconds}` and `display_width = round(width × sar / 2) × 2`:

- size: the item with the largest `display_width × height` (ties: the one with more
  seconds);
- fps: the fps value with the most total seconds (values within 0.01 are the same;
  29.97 and 30 are different).

Python: `export.suggest_canvas(project)`; TS: `suggestCanvas(...)` in `lib/edit.ts`.
`export.canvas()` keeps its fallback (first clip) for projects with `canvas: null`, but
uses `display_width` instead of `width` there too.

The stitch dialog (§4.2) shows a note when sources differ: "2 of 5 videos are 4:3 and
get side bars" / "1 video is 25 fps and is converted to 30".

### 3.3 Non-square pixels

In `export.video_graph`, when the clip's source probe has `sar` not within 0.001 of 1,
insert `scale=trunc(iw*sar/2)*2:ih,setsar=1` right after deinterlacing (before
`video_filters`). In `plan()`, such a source can never be copied in a *mixed* timeline
whose canvas differs; for a single-source or all-identical timeline the copy path keeps
the stream untouched (its own SAR is preserved by copying).

`prep.proxy` keeps its filter; check on a synthetic anamorphic fixture
(`-vf setsar=16/15`) that the editor's `<video>` shows the proxy at the right shape; if
it does not, add the same `scale … setsar=1` to the proxy filter and bump nothing (old
proxies of anamorphic sources would just be re-made when the user re-prepares).

Tests: graph contains the scale for `sar=1.0667`, not for 1.0; an encoded export of the
anamorphic fixture has the display aspect of the source.

### 3.4 Test fixtures (`tests/fixtures.py`)

Add, built lazily and cached like `build()`:

- `build_twin(root)`: `twin.mp4`, same encoder settings as `fixture.mp4` (same size,
  rate, GOP, preset) but `testsrc2` replaced by `smptebars` and different audio: for
  lossless-stitch tests (identical stream format, different content).
- `build_other(root)`: `other.mp4`, 320×240, 25 fps, mono 44.1 kHz audio, 20 s: for
  mixed-format tests.
- `build_anamorphic(root)`: 360×288 with `setsar=16/15`, 10 s.

Keep each a few seconds of work.

---

## 4. Getting pieces into a timeline

### 4.1 Multi-select in Media

`workspaces/Media.svelte`: `selectedId` becomes a set plus an anchor. Click selects one;
`⌘`-click toggles; `⇧`-click selects the range in the shown order; `⌘A` selects all
shown. The details panel shows the single selected video, or for several: a count, their
total length, and **Stitch into a new timeline…**. Persist only the single selection
(`vac.media.selected`), as today.

### 4.2 The stitch dialog (`media/StitchDialog.svelte`)

- **Name** (required, validated like `projects.check_name`; refuse an existing name).
- **Order**: the videos as rows, drag to reorder (default: by name), each with its
  length and a "Use" choice:
  - *Whole video* (default when a cleaned output covers the whole video, or nothing is
    cleaned),
  - *Only the cleaned pieces* (offered when some but not all of it is cleaned).
- **Sound between clips**: Soft, 1 s (default) / Cut.
- The format note of §3.2, the total length, and "Videos not yet prepared for preview
  are prepared now" when any is unprepared.
- Create → `api.createProject(name)`, build the project, `api.saveProject`, queue
  `prep` jobs for unprepared sources, `go(["edit", name])`.

### 4.3 Building the clips (`stitchClips` in `lib/edit.ts`, pure, tested)

Input: sources in order, each with its mode. For one source of length `D`:

- Take its video outputs with a window, existing on disk. Where several cover the same
  stretch, keep the newest (`created`), the rule `coveringOutput` already uses.
- *Whole video*: cut `[0, D]` at the boundaries of the kept outputs. Each resulting
  stretch is one clip: with `audioFor(output)` where one covers it, else `audio: null`
  (original sound). A stretch shorter than 0.5 s merges into its neighbour (the
  neighbour's audio must still cover it, else it stays original).
- *Only the cleaned pieces*: one clip per kept output, in time order, trimmed so clips
  do not overlap (a later output starts where the previous clip ended).
- `audioFor` also sets `duration_sec` from the output's window (§2).
- `transition_in`: `null` on the very first clip; between two clips that are contiguous
  in the same source with the **same** audio (same file, or both original): `null`;
  otherwise the dialog's choice (`{"type": "soft_cut", "sec": 1}` or `null`).
- `project.sources[id] = {path, probe}` for each source; `project.canvas =
  suggestCanvas(...)`; `project.export.level_clips = true`.

### 4.4 "Add to timeline" for one piece

In `media/SourceDetails.svelte` each output row gets an "Add to timeline" action (the
select of timelines that exists under *Editing* moves into a small popover used by both
the whole-video and the per-piece action). It appends one clip `[window start, window
end]` with that output's audio, `transition_in: {"type": "soft_cut", "sec": 1}` unless
the timeline is empty. The whole-video action uses `stitchClips` for that one source
(mode *Whole video*).

### 4.5 Editor

- The inspector's "Start" select (`Editor.svelte` ~line 527) becomes: cut · soft sound
  0.5 s / 1 s / 2 s · crossfade 0.5 s / 1 s / 2 s. Under it one line says what will
  happen at this join (from `joinsOf`, §7.3): "The two sounds overlap around the cut" /
  "No spare sound past the cut on one side: fades out, then in".
- `Timeline.svelte`: a small marker on each join that is not a plain cut (a diamond on
  the cut line for `soft_cut`, the existing overlap look for crossfades); clicking it
  selects the incoming clip.
- `split()` keeps giving the right half `transition_in: null` (the sound is continuous).
- Everything else in the editor is untouched by this plan.

Accept (§4): on the scratch project with `fixture.mp4`, `twin.mp4` and `other.mp4`
added — select the three in Media, stitch, land in Edit with three or more clips in the
chosen order, soft joins between different files, canvas 640×480 at 24 fps, the format
note shown; Vitest covers `stitchClips` (whole / pieces / overlapping outputs / tiny
gaps / contiguity) and the selection logic.

---

## 5. Long timelines in the preview (`gui/src/lib/program.ts`)

Today `Program.setEdit` decodes every clip's audio (`decode(url)`), and a cleaned clip's
URL is the *whole* cleaned output. That cannot hold hours of audio.

### 5.1 Two kinds of voice

Introduce `interface Voice { start(when: number, into: number): void; stop(): void;
readonly gain: GainNode }` with two implementations in `lib/voices.ts`:

- `BufferVoice`: today's code (decoded buffer, `AudioBufferSourceNode`), sample-accurate.
- `StreamVoice`: an `HTMLAudioElement` routed through
  `ctx.createMediaElementSource(el)` into the gain node. `start(when, into)`: set
  `el.currentTime = offset + into`, call `el.play()` when the context clock reaches
  `when` (a timeout aimed 20 ms early, then a check each animation frame). While
  playing, `Program.frame()` calls `voice.follow(expected)`: drift above 0.25 s →
  reseat `currentTime`; above 0.04 s → `playbackRate` 0.95–1.05 until within 0.015 s
  (the picture already does the same). `stop()`: pause, and when the voice is released,
  `removeAttribute("src"); load()`.

Gains, fades and the future meter work the same for both, because both end in a
`GainNode` scheduled on the context clock.

### 5.2 Which voice

`Resolver.clipAudio(clip)` returns `{ url, offset, seconds }` where `seconds` is the
length of the audio behind the URL. Streamed when `seconds > 180`, else buffered.

Resolver changes in `Editor.svelte`:

- cleaned: `{ url: media.outputAudio(file), offset: in − start_sec, seconds:
  audio.duration_sec ?? Infinity }` (the cached `.m4a`, served by `FileResponse`, which
  supports byte ranges in the installed Starlette);
- original, clip longer than 180 s: `{ url: "/media/proxy?source=…", offset: in_sec,
  seconds: source duration }` — the proxy already carries the original sound, so no
  extraction is needed;
- original, shorter: as today (`media.original(source, in, length)`).

### 5.3 Loading

- `setEdit` decodes only buffered voices.
- Live stream voices: the clip under the playhead and the next one only. Create the next
  one about 2 s before its start (the same moment the next picture is preloaded); release
  a voice when its clip is more than one clip behind.
- Seeking while playing rebuilds the voices for the new position (as `startNodes` does
  for buffers today).

### 5.4 Checks the implementer must make first

Before building: in the pywebview window (WKWebView) and in the Browser pane, confirm on
a 20-minute synthetic file that (a) `createMediaElementSource` on an `<audio>` of
`/media/audio?kind=output…` plays through the context, (b) seeking is immediate (range
requests in the network log), (c) two elements can be alive at once. If (a) fails in
WKWebView, fall back to plain element volume (`el.volume` ramps via a per-frame
function) and say so in the doc's status notes; do not drop the feature.

Tests (Vitest, mocked `AudioContext` and a fake element): voice choice by length; drift
handling thresholds; only two stream voices alive; seek rebuilds. Browser: a scratch
timeline of two 20-minute synthetic clips plays across the join with the picture within
80 ms of the sound (`video.dataset`-style drift readout, as Compare has), memory flat.

---

## 6. Long cleaned files in Compare

`routes/Compare.svelte` decodes the whole original and the whole cleaned audio. For a
whole-file clean this is two buffers of over a gigabyte each.

- New constant `SECTION_SEC = 300`. When the output's window is longer than 420 s,
  Compare works on a **section** `[s, s + 300]` of the window; shorter outputs are
  unchanged.
- Server: `/media/audio` gains `start` and `duration` for `kind=output` and
  `kind=original_for` (relative to the output's window). Both are produced by
  **re-encoding** that stretch to AAC with an exact `-ss`/`-t` (not a packet copy), so
  A and B stay sample-aligned; cached like `original_audio` (key includes start and
  duration). Round `start` to whole seconds client-side so sections are reused.
- UI: above the deck, a slim bar of the whole output with its filmstrip (reuse
  `Filmstrip` with `start`/`end`) and the current section highlighted; click or drag
  moves the section (snapped to 10 s); `⇧[` / `⇧]` step a section back/forward. The
  deck's own filmstrip and times cover the section only; `picture.start` passed to
  `ABDeck` becomes window start + section start, so `lib/playhead.ts` keeps working
  across versions and now also restores the right section: on open, if `resumeFor`
  gives a time, choose the section containing it.
- The picture (`<video>` of the output file) is offset by the section start through
  `followClock`'s `offset` argument.

Tests: server test for the two new windowed kinds (duration of the result, cache file
names); Vitest for "which section contains t". Browser: a 20-minute synthetic output
opens in under a few seconds, sections switch, A/B stay in sync.

---

## 7. Soft sound between clips (`soft_cut`)

### 7.1 Meaning

For a clip with `transition_in = {"type": "soft_cut", "sec": S}` following clip *P*:

- `d = min(S, length(P), length(this))`; `h = d / 2`.
- **Contiguous** (same source, same audio — same `audio.file`, or both original — and
  `|P.out_sec − this.in_sec| < 0.02`): plain cut. The sound is already continuous.
- **Blend**, when *P* has at least `h` of audio after its out point **and** this clip
  has at least `h` before its in point (its "spare sound"): *P*'s sound runs `h` past
  the cut while fading out, this clip's sound starts `h` before the cut while fading in.
  Equal-power curves. The cross-fade is centred on the cut.
- **Dip**, otherwise: *P* fades out over its last `h`, this clip fades in over its first
  `h`. No overlap.
- A muted clip on either side: dip (the other side still fades).

Spare sound (seconds):

| Clip sound | after the out point | before the in point |
|---|---|---|
| cleaned (`audio.file`) | `audio.start_sec + audio.duration_sec − out_sec` | `in_sec − audio.start_sec` |
| original (`audio: null` or only `gain_db`) | `source duration − out_sec` | `in_sec` |
| muted | 0 | 0 |

`audio.duration_sec` missing (older projects): the exporter probes the file's duration
once per run (`ffprobe format=duration`, memoised); the GUI falls back to the library
output's window, else treats spare as 0.

### 7.2 One function decides, in both languages

`export.joins(project, durations=None)` and `joinsOf(clips, info)` in `lib/edit.ts`
return, for every clip index `i ≥ 1`: `{"kind": "cut" | "crossfade" | "blend" | "dip",
"sec": d}`. Both are tested against the same JSON table (§12.1). Everything below
consumes that list.

### 7.3 Export (`export.audio_graph`)

Keep the present structure (per-clip chains, then pairwise joining). Changes:

- Per clip `i`, with `pre = h` if join `i` is a blend else 0, and `post = h` if join
  `i+1` is a blend else 0:
  - input: `inputs.add(file, offset − pre, length + pre + post)` (for original sound
    with `pre + post > 0` add a separate audio input instead of reusing the picture's
    input, which is cut exactly at the clip);
  - chain: `…,apad,atrim=duration={length + pre + post}` then `volume`;
  - dip fades inside the clip: `afade=t=in:st=0:d=h:curve=qsin` when join `i` is a dip,
    `afade=t=out:st={length − h}:d=h:curve=qsin` when join `i+1` is a dip.
- Joining clip `i` to the running stream:
  - blend: `acrossfade=d={d}:c1=qsin:c2=qsin` (the two extended streams overlap by
    exactly `d`, so the total length is unchanged);
  - crossfade: as today (`tri`);
  - cut, dip: `concat`.
- The final `atrim=duration={total}` stays.

`plan()`: a `soft_cut` does **not** prevent copying the video (only the sound is
touched). Only `crossfade` does.

### 7.4 Preview (`program.ts`)

Per voice, from `joinsOf`:

- blend: start the voice `h` early at `offset − h`, play `length + pre + post`;
  schedule an equal-power fade-in over `[start − h, start + h]` and fade-out over
  `[end − h, end + h]` with `gain.setValueCurveAtTime` (a 33-point sine/cosine quarter
  wave; when starting inside a fade, pass the remaining part of the curve);
- dip: the same curves over `[start, start + h]` and `[end − h, end]`;
- crossfade: unchanged.

For stream voices the early start means the next element must be ready `h` sooner;
fold `h` into the 2 s preload lead.

### 7.5 Tests

- Shared table for `joins` (blend, dip from a file start, dip from a file end,
  contiguous, crossfade untouched, `d` clamped by a short clip, missing duration).
- Python: the graph has `acrossfade…qsin` for a blend and `afade` pairs for a dip;
  exported length equals the timeline length to within a frame for a mixed timeline; a
  rendered blend has no silence at the cut (RMS over ±0.2 s of the cut is above the dip
  case's); `plan()` still says copy for a keyframe-cut single-source timeline with
  `soft_cut`.
- Vitest: scheduled gain curves for both kinds, including a start inside a fade.

---

## 8. Even loudness between clips (`export.level_clips`)

> Built (2026-11-22) as below, with two changes: back-to-back pieces of one recording (same
> video, same sound, no gap: a split shot) are measured together and get one gain, so a
> split never jumps; and the editor sends its current clips (`POST /api/projects/{name}/levels`)
> instead of the endpoint reading the saved file. The levelling is in `export.level()`,
> used by both `export.run()` and the budgeted `sizing.run()`.

Cleaned outputs are each at the target (−16 LUFS) over *their whole window*; a clip may
be a part of one, and original-sound clips are wherever the tape was. With
`export.level_clips: true`:

- `export.clip_levels(project, cache=None)` → `[{"lufs": float | None, "gain_db":
  float}]` per clip. Each clip's own range (no spare sound) is measured with one
  audio-only ffmpeg (`-ss/-t`, `ebur128`), `gain = clamp(target_lufs − lufs, −12, +12)`;
  `0` when the clip is muted, shorter than 3 s, or near silent (`lufs` None or below
  −50).
- The gain is added to the clip's `volume` in `audio_graph` (on top of `audio.gain_db`,
  which stays the user's own trim). The overall measure-then-gain pass and the limiter
  are unchanged and run after.
- Cache: `levels.json` beside `project.json`, a map from `"<abs path>|<mtime>|<start
  to 0.01>|<length to 0.01>"` to LUFS; `run()` reads and updates it. Measuring an hour
  of audio takes well under a minute; with the cache a second export is instant.
- `run()` gets a step "Levelling the clips" (only when enabled) between "Checking the
  cuts" and "Measuring loudness"; `total_steps` becomes dynamic.
- GUI: `GET /api/projects/{name}/levels` computes missing entries in a thread and
  returns `{clip_id: {lufs, gain_db}}`; the editor fetches it after loading and after
  edits settle (debounced 1 s), `Program` applies `gain_db` per clip, and the inspector
  shows "Auto level +3.2 dB" under the clip's Gain. A checkbox "Even out loudness
  between clips" lives in Deliver (§11) and writes `export.level_clips`.

Tests: two clips at −30 and −16 LUFS export within 1.5 LU of each other with the flag
and stay apart without it; clamp; cache hit avoids ffmpeg (inject the measurer); the
endpoint.

---

## 9. Lossless stitch across files (`export.plan`, `copy_commands`)

Today `plan()` refuses to copy as soon as clips come from more than one video.

### 9.1 When copying is allowed

Replace the "more than one video" reason by a format comparison. All clips' sources must
agree on: `codec`, `width`, `height`, `pix_fmt`, `fps` (within 0.01), `sar`, `profile`,
`level`, `time_base` and `extradata_md5`, and the canvas must equal that frame and rate.
When they do not: reason "the videos were recorded with different settings (…the first
differing field, in plain words…)". When a probe lacks `extradata_md5` (old probe):
reason "the videos' formats have not been compared yet" — the GUI refreshes probes
(§3.1) so this is transient.

The remaining rules are today's: no crossfade, no visual edits, no deinterlacing, every
clip's `in_sec` on a keyframe of its own source (`keyframe_at(path, start)` per clip —
it already takes the path; pass each clip's).

### 9.2 Doing it

`concat_list()` already writes one `file`/`inpoint`/`outpoint` entry per clip with the
clip's own path; nothing else changes in `copy_commands`.

After the mux, **verify**: for each join time `t`, decode `[t − 1, t + 2]` of the
result (`ffmpeg -v error -xerror -ss … -t 3 -i out -map 0:v:0 -f null -`), and compare
the result's video duration with the timeline's (within two frames). On any failure:
delete the partial, print a warning ("Lossless joining did not hold at 12:31; encoding
instead"), and run the encode path with that as the reason. The sidecar's `results`
record `copy_verified: true/false`.

### 9.3 Tests

`fixture.mp4` + `twin.mp4`: `plan()` says copy; the export's video packets are
bit-identical to the sources' (compare `ffprobe -show_entries packet=size` runs per
segment, or frame MD5s via `-f framemd5` against the sources' ranges); duration right;
the verify step passes. `fixture.mp4` + `other.mp4`: encode, with the reason naming the
size. A doctored probe (different `extradata_md5`): encode. A verify failure (inject a
failing checker): falls back to encode.

---

## 10. Export to a size budget

### 10.1 CLI

```
clean-audio-export PROJECT.json [--budget 2GB/h | --size 2.5GB] [--codec h264|hevc]
                   [--height N] [--fast] [--one_pass] [--estimate] [--progress json]
```

- `--budget` accepts `2GB/h`, `2 GB/hour`, `1500MB/h`; `--size` accepts `2.5GB`,
  `900MB`. 1 GB = 10⁹ bytes. They are exclusive. Each overrides `export.budget`; the
  other flags override their `export.*` keys. Without a budget (flag or project) the
  export behaves exactly as today, plus `--codec hevc` / `--height` being honoured in
  the plain encode path (CRF 20 for `libx265`, `-tag:v hvc1`).
- `--estimate`: run stage A only, print the estimate, emit it as the `result` event
  (`output: null`, `results: {"estimate": …}`), write `estimate.json` beside the
  project. No output file.
- New module `video_audio_cleaner/sizing.py` holds everything in this section;
  `export.run()` calls it.

### 10.2 Bitrate arithmetic

- `T` = timeline seconds; `bytes` = `per_hour_gb × 10⁹ × T / 3600` or `total_gb × 10⁹`.
- `audio_bps` = the AAC bitrate (`export.audio_bitrate`).
- `video_bps_budget = (bytes × 8 × 0.985) / T − audio_bps` (1.5 % for the container).
  Below 150 kbit/s → `ProjectError("the budget is too small for this length")`.

### 10.3 Source ceiling

Spending more bits than the originals contain buys nothing.

- Per clip: `src_bps × max(1, canvas_pixels / source_pixels)` where `src_bps` is the
  source's `video.bit_rate` (when unknown: file size × 8 / duration − audio bitrate);
  when the output codec is HEVC and the source is H.264 the figure is used as is (the
  same bitrate in a better codec is transparent to the source).
- `source_bps` = the length-weighted mean over clips.
- `cap_bps = min(video_bps_budget, source_bps)`.

If `plan()` allows a lossless copy (§9) and the copy's size (sum of clip lengths ×
source bitrates, plus audio) is within the budget: **copy**, and say so: "The videos are
already about 1.0 GB per hour, under your 2 GB per hour: joined without re-encoding."
`--reencode` still forces an encode.

### 10.4 Stage A: samples

1. **Pick samples.** Sample length `L = 8 s` when the output height ≤ 1080, else 4 s.
   Count `n = clamp(round(T / 600), 6, 12)` (max 8 above 1080). Cut the timeline into
   `n` equal parts and take `L` seconds from the middle of each; then add one sample in
   the middle of any clip of at least 60 s that has none, up to 16 in all. Each sample's
   weight is the timeline time it stands for (its part, or its clip). `T < 2 × n × L`:
   use the whole timeline as one sample.
2. **Reference.** For one sample at a time: `export.excerpt(project, t0, t1)` (a pure
   function returning a project whose video clips are trimmed to the window, music
   dropped, crossfades inside the window treated as cuts) is rendered through the real
   `video_graph` (at the output size, §10.6) to a lossless file in the work dir:
   `-an -c:v libx264 -qp 0 -preset ultrafast -pix_fmt yuv420p`. Delete it when the
   sample's trials are done, so only one reference is on disk at a time.
3. **Ceiling trial.** Encode every reference at the quality ceiling — `libx265 -crf 20`
   or `libx264 -crf 18`, the final preset — and record bits per second and VMAF.
   `top_bps` = weighted mean bitrate.
4. **Decide.**
   - `top_bps ≤ cap_bps` → mode **`quality`**: the final encode is one pass at the
     ceiling CRF. Predicted size from `top_bps`.
   - else → mode **`size`**: find the CRF `q*` at which the weighted sample bitrate
     equals `cap_bps`: start from `q = ceiling + 6 × log2(top_bps / cap_bps)`, then
     secant steps on (CRF, log bitrate), fractional CRFs allowed, within `[ceiling, 40]`,
     until within 5 % or four more rounds. Record VMAF at `q*`. The final encode is two
     passes at `cap_bps` (§10.5); `q*` is what that will look like.
   - Because references are deleted per sample, run steps 3–4 **per round over all
     samples**: keep the references instead when their total is under 4 GB (check from
     the first one's size × count), else re-render each round. Decide once, at the
     start, and log which.
5. **Speed.** Record encode frames per second of the trials (and of one pass-1 trial in
   `size` mode) → predicted minutes for the full encode.
6. **Quality words** from the weighted mean VMAF and the lowest sample:

   | mean VMAF | words |
   |---|---|
   | ≥ 93 | Excellent: hard to tell from the original |
   | 85–93 | Good |
   | 75–85 | Fair: fine detail softens |
   | < 75 | Soft |

   Add "one part is noticeably weaker (at 41:10)" when the lowest sample is more than
   10 below the mean. VMAF: `libvmaf` with `n_threads=8:n_subsample=3`, the default
   model; distorted first, reference second, both at the output size. When this ffmpeg
   has no `libvmaf` (check `ffmpeg -filters` once), use `ssim` (≥ 0.98 / 0.96 / 0.93 for
   the same words) and mark the estimate `"metric": "ssim"`.
7. **Result** (`estimate.json`, also the `--estimate` result):

   ```json
   {"key": "<sha1 of what the estimate depends on>", "created": "…",
    "mode": "copy" | "quality" | "size",
    "budget_bytes": 2670000000, "predicted_bytes": 1310000000,
    "video_bps": 2000000, "source_bps": 1990000, "cap": "source" | "budget",
    "codec": "hevc", "height": 720, "crf": 20.0, "crf_equivalent": null,
    "metric": "vmaf", "quality_mean": 95.1, "quality_low": 91.0, "quality_low_at_sec": 2470,
    "words": "Excellent: hard to tell from the original",
    "encode_minutes": 21, "samples": [{"at_sec": 300, "weight": 600, "bps": 1810000, "quality": 95.4}],
    "suggest_height": null, "notes": ["The videos are 1.0 GB per hour; the budget allows 2.0."]}
   ```

   `key` hashes: the video clips (source, in, out, crossfade seconds), the sources'
   probe ids, canvas, codec, height, fast, preset, and the budget in bytes. `run()`
   reuses a saved estimate whose key matches and skips stage A.

Stage A must respect cancellation like every job here (`procs` registration of the work
dir; nothing left behind) and report progress as `step` events ("Trying sample 3 of 8").

### 10.5 Stage B: the one real encode

- `copy`: the copy path.
- `quality`: `encode_command` with the ceiling CRF.
- `size`, default: two passes with the same graph —
  - `libx265`: `-b:v {cap_bps} -x265-params pass=1:stats={work}/x265.log` writing to
    `-f null -` with `-an`, then `pass=2:stats=…` with audio to the partial file;
  - `libx264`: `-b:v {cap_bps} -pass 1 -passlogfile {work}/x264 … -f null -`, then
    `-pass 2`.
  Progress: two steps ("Analysing the picture", "Encoding").
- `size` with `--one_pass`: one pass at CRF `q*` (faster; the size can miss by about
  10 %, said in the help and in the GUI).
- `--fast` (`export.fast`): `hevc_videotoolbox` / `h264_videotoolbox` with
  `-b:v {cap_bps}`, one pass; stage A then skips the CRF search and measures the
  samples at that bitrate directly.
- Common encoder arguments: `-pix_fmt yuv420p`, `-g round(2 × fps)`, HEVC `-tag:v hvc1`,
  `+faststart`, `-metadata creation_time=<earliest source creation_time>` when any
  source has one. H.264 keeps `-profile:v high`.
- After writing: if the file exceeds the budget by more than 2 %, warn (do not fail) and
  record it. The sidecar's `results` gain `budget_bytes`, `size_bytes`, `mode`,
  `passes`, and the estimate.

Refactor `encoder_args(s, cv)` into `encoder_args(s, cv, rate=None)` returning the
codec-specific list for CRF, bitrate and pass variants; keep today's output for today's
settings byte for byte (existing tests assert the commands' effects, not their strings,
but keep it anyway).

### 10.6 Output size

`export.height` (or `--height`): scale the canvas down to that height, width from the
canvas aspect rounded to even, never up. `video_graph`'s per-clip `scale`/`pad` then
target the reduced frame; the references of §10.4 are rendered at it.

When the estimate is `size` mode with mean VMAF under 85 and the output is taller than
720: repeat steps 2–4 once at the next standard height down (2160 → 1440 → 1080 → 720),
measuring VMAF against the **full-size** reference (scale the trial up with `bicubic`
inside the VMAF graph). If the smaller frame scores at least 2 higher, set
`suggest_height` and add the note "At this size 1080p would look better than 4K". The
tool only suggests; the user (or `--height`) decides.

### 10.7 Things to verify before relying on them

On the fixture, at the start of this part: that `-x265-params pass=1/2` produces a file
within 2 % of the requested bitrate in this ffmpeg (8.1); that `libvmaf` runs and how
long 8 s takes; that QuickTime plays the `hvc1` result. Acceptance by Google Photos and
YouTube cannot be tested here: ask the user to try one short export of each codec
before long ones, and record the outcome in this document.

### 10.8 Tests

Fast tier, on the fixture (seconds long, so samples are tiny; make sample length and
count injectable):

- budget parsing; bitrate arithmetic; "too small" error;
- sample picking (counts, every long clip covered, weights sum to `T`);
- `excerpt()` (clips trimmed, positions, crossfade dropped);
- the CRF search against a fake encoder following `bps = k × 2^(−crf/6)` converges in
  ≤ 3 rounds; clamps at 40;
- source ceiling: budget above the source bitrate → `cap: "source"`; copy chosen when
  §9 allows it and it fits;
- a real tiny run: `--budget` forcing `size` mode writes an HEVC `hvc1` file within
  10 % of the budget (short files are overhead-heavy; assert loosely), sidecar fields
  present; `--estimate` writes `estimate.json` and no video; a second run reuses the
  estimate (inject a counter);
- words table; SSIM fallback when the filter list lacks `libvmaf` (injected).

---

## 11. GUI: Deliver

`workspaces/Deliver.svelte` becomes the place exports start. Layout (Split panes, as
Media):

```
┌──────────────────────────┬────────────────────────────────┬──────────────────┐
│ Timelines (list)         │ Export settings for <timeline> │ Render queue     │
│ ▸ Wedding 1994   80 min  │ ○ Keep the original quality    │ (JobList, export │
│   Trip           12 min  │ ○ Fit a size  [ 2 ] GB per hour│  and estimate    │
│                          │      = 2.67 GB for 80 min      │  jobs)           │
│                          │ Format: HEVC (smaller) ▾       │                  │
│                          │ ☑ Even out loudness            │                  │
│                          │ [Estimate]  [Export]           │                  │
│                          │ ── estimate card ──            │                  │
└──────────────────────────┴────────────────────────────────┴──────────────────┘
```

- **Keep the original quality** (`budget: null`): today's behaviour — lossless when
  `plan` allows (now also across matching files), else visually lossless. The plan's
  reasons are listed as today.
- **Fit a size**: number + unit select (GB per hour / GB in total) → `export.budget`.
  Format select → `export.codec` ("HEVC — smaller files" / "H.264 — plays
  everywhere"). "Faster, lower quality (hardware)" checkbox → `export.fast`. A size
  select (Same as the timeline / 1080p / 720p; only heights below the canvas) →
  `export.height`.
- **Estimate** queues a job of type `export_estimate`; **Export** queues `export`
  (which estimates first if no fresh estimate exists). Both save the project first.
- **Estimate card**, from `GET /api/projects/{name}/estimate` (`{estimate, stale}`;
  stale when the key no longer matches): predicted size against the budget, the quality
  words with the score in small type, the time, one sentence for the mode ("Joined
  without re-encoding" / "Encoded at top quality: smaller than your budget" / "Encoded
  to fit 2.67 GB"), the notes, and the height suggestion with an "Use 1080p" button.
  Stale → greyed with "Settings changed: estimate again".
- A link "Preview in Edit" (the timeline player is not embedded here until the
  redesign's phase D extracts it).
- Missing source files are listed in red with the Export button disabled
  (`GET /api/projects/{name}/plan` gains `missing: [paths]`; `export.run` already
  refuses).
- The editor's Export… button navigates to `#/deliver/<name>`; delete its export dialog
  and the code only it used.

Server:

- `commands.export_argv(project_path, reencode=False, estimate=False)` (budget, codec
  and the rest travel in the project file, so the command shown stays short and
  reproducible: `clean-audio-export …/project.json`).
- Job types `export` (unchanged name) and `export_estimate`, both in the heavy lane
  (`ML_TYPES`); titles "Export <name>" / "Estimate <name>".
- `GET /api/projects/{name}/estimate`, `GET /api/projects/{name}/levels` (§8); the
  estimate route recomputes `key` from the saved project to set `stale`.
- `state.svelte.ts`: when an `export_estimate` job finishes, Deliver refetches the
  estimate.

Accept (§11): on the scratch project — keep-quality export of the fixture+twin stitch
is a copy; fit-a-size shows an estimate with words, then exports within tolerance;
changing the budget marks the estimate stale; a renamed-away source disables Export
with its path shown; the queue shows both job kinds with progress, Cancel and Remove.

---

## 12. Order of work

Each part ends with all tests green, a browser pass where it has UI, CHANGELOG and
README updated, and this document's status line and a "§0.5 Implementation notes" list
(deviations, verified facts from §5.4, §10.7) kept current.

| Part | Contains | Depends on |
|---|---|---|
| **1. Groundwork** | §3 (probe fields and cache version, canvas suggestion, non-square pixels, fixtures) | — |
| **2. Lossless stitch** | §9 | 1 |
| **3. Soft sound** | §2 (`soft_cut`, `duration_sec`), §7, the editor controls of §4.5 | 1 |
| **4. Stitch entry** | §4.1–4.4 | 1, 3 |
| **5. Long preview** | §5, then §6 | — (independent; may run in parallel with 2–4) |
| **6. Levelling** | §8 | 3 |
| **7. Budget CLI** | §10 | 1, 2 |
| **8. Deliver** | §11 | 6, 7 |

After part 4 the user can already stitch, watch short stitches and export without loss;
after 5, long ones; 7–8 add the budget. If time is short, part 6 is the one to drop.

### 12.1 Shared test tables

`joins` (§7.2) and `suggest_canvas` (§3.2) exist in Python and TypeScript and must
agree. Put their cases in `tests/data/joins.json` and `tests/data/canvas.json` (input,
expected output); `tests/test_export.py` and `gui/src/lib/edit.test.ts` both load them
(Vitest reads the files through a relative `import … with { type: "json" }` or
`fs.readFileSync`; whichever the present Vite config allows without new dependencies).

---

## 13. What the redesign's later phases must carry over

When `docs/gui-editor-redesign.md` phase C introduces schema 2, `export.migrate()` must
keep: `transition_in.type == "soft_cut"`, `audio.duration_sec`, and `export.budget`,
`codec`, `height`, `fast`, `level_clips`. Its §9 (Deliver presets) builds on §11 here:
the presets become shortcuts that fill these same fields ("Smaller file" = fit a size
with HEVC). Its §10 (`clean-audio-compress` for single sources) should reuse
`sizing.py` (a source is a one-clip timeline) rather than fixed quality numbers. Its
phase D extracts the timeline player; Deliver then embeds it in place of the "Preview
in Edit" link.

---

## 14. Not in this plan

- Any change to the picture beyond scaling, padding, deinterlacing and compression.
- More than one video track, titles, speed changes.
- AV1 (`libsvtav1` is installed, but Google Photos/YouTube ingest and local playback
  are less certain; revisit later).
- Two-pass for the hardware encoders.
- Uploading anywhere.

## 15. Left to the implementer

- Exact look of the stitch dialog, the join markers and the estimate card.
- The stream-voice start strategy if WKWebView behaves differently from Chromium
  (§5.4), within the stated sync tolerance.
- Sample counts/lengths and the VMAF word thresholds are starting values: adjust after
  §10.7's checks, and write the final values here.

One observed oddity to leave alone unless the user asks: the editing proxies (540p at
2.5 Mbit/s) are larger than the user's 720p tape files (2 Mbit/s). They still earn
their keep through dense keyframes (instant seeking), but "prepare" costs more disk than
the sources themselves for this material.
