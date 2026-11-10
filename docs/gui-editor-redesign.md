# GUI redesign: from audio cleaner to home-video editor

Status: **phase A implemented, phase B partly** (unreleased); phases C–H not started. Written 2026-11-10,
against 0.5.0, commit `bd6ff94`. See §0.4 for what the implementation changed.
This document is self-contained: an implementer who has not seen the discussion that led
to it can execute it. It builds on `docs/gui-design.md` (the 0.5.0 design); where the two
disagree about anything listed here, this document wins.

---

## 0. How to use this document

### 0.1 Read first

| File | Why |
|---|---|
| `docs/gui-design.md` §0 | What 0.5.0 actually built and where it deviates from its own design |
| `video_audio_cleaner/export.py` | The project model and the export graphs this plan extends |
| `video_audio_cleaner/gui/{app,library,jobs,commands,media,projects}.py` | Server |
| `gui/src/App.svelte`, `gui/src/app.css` | Shell and tokens being replaced |
| `gui/src/routes/Editor.svelte`, `gui/src/components/Timeline.svelte` | The editor being restructured |
| `gui/src/lib/{edit,program,abplayer,state.svelte,router.svelte}.ts` | Edit model, playback engines, state |
| `tests/test_gui_server.py`, `tests/test_export.py`, `tests/fixtures.py` | How the server and export are tested |

### 0.2 Working rules (unchanged from 0.5.0)

- Use `./venv/bin/…` for Python. Fast tests: `./venv/bin/pytest` (the slow tier is opt-in
  with `-m slow`). Frontend: `cd gui && npm run check && npm test && npm run build`.
- **Never run long jobs on the user's real videos unasked**, and never write test output
  next to source videos. Verify in the browser on a scratch project folder built from
  `tests/fixtures.py` (`fixtures.build(root)`; `tests/test_gui_server.py::project` shows
  how a project folder with sidecars, a plan and candidates is assembled). Reading the
  real output folder (`~/Documents/video_processing_output`) in the Library is fine.
- Every long operation stays a CLI subprocess with `--progress json`; the GUI never does
  what the CLI cannot. New operations get a CLI first, then a job type.
- Commit and push only when the user asks. One commit per phase is the natural unit;
  each phase below leaves the app working and all tests green.
- Keep the plain-language labels ("Original", "Cleaned", "Pick this one"). The redesign
  borrows DaVinci Resolve's *structure and behaviour*, not its density or jargon.

### 0.3 Where the product is going (context for every decision)

The user restores old home videos on an M3 MacBook Air. Audio cleaning was the first
step. The app is becoming a small video editor: cut and stitch, background music and
sounds, zoom into the picture, compress large files, and later machine-learning picture
enhancement (upscaling, face reconstruction). Outputs remain single MP4 files that
Google Photos and YouTube accept. The user asked for the GUI to feel "a bit like DaVinci
video editing".

So: design for several tracks, per-clip effects and per-source "versions" now, even
where only one of each exists yet.

### 0.4 Implementation progress and deviations

Where the implementation differs from the text below, this list wins.

**Phase A (shell, look, Media)** — done.
- Icons: `@lucide/svelte` pinned at 1.64.0; `components/Icon.svelte` imports each icon by
  path, so add a name there before using it.
- Posters: the default set keeps its 0.5.0 cache name (`posters/<id>_<i>.jpg`); only the
  12-frame set uses `<id>_12_<i>.jpg`. The grid's resting poster is frame 1 of the
  default four, the same instant as frame 4 of the twelve.
- `library.scan` already returns `exports` (§11) — the Media "Exports" bin needs it.
- The top bar shows the timeline name only; undo/redo and the save state stay in the
  editor's own header until phase D.
- `routes/Library.svelte` is gone (Media replaces it); `routes/Jobs.svelte` became
  `components/JobList.svelte`, used by the Jobs drawer and by Deliver's render queue.
  Workspaces live in `src/workspaces/`, Media's parts in `src/media/`.
- Enhance hosts the 0.5.0 Compare / Candidates / New run pages, chosen by `out`,
  `tab=candidates` + `plan` + `win`, and `tab=new`; with none of those it shows a source
  list and the source's details (the Media details component). `links.*` in
  `lib/router.svelte.ts` builds these URLs; use them instead of hand-built hashes.
- Deliver lists timelines (export still starts from the editor) and the export jobs.
- Extra: in Media, ←/→ move the selection and Enter opens the video in Enhance.

**Phase B (Enhance)** — navigation done, merge of the panels not yet. Built after the
user found it hard to move between a video's pieces and back to all videos:
- A video's *pieces* are its cleaned outputs and tuned windows in time order
  (`src/media/pieces.ts`). Enhance is a three-level hierarchy: All videos (an overview of
  every video with all its pieces) › a video (its details) › a piece (Compare for an
  output, Candidates for a tuned window). Breadcrumb on top; `[`/`]` previous/next piece,
  `{`/`}` previous/next video, Esc up a level.
- The left panel is a tree of videos and their pieces (the current one open) instead of
  a flat list. URLs are built by `src/enhance/nav.ts` and always carry the source id.
- The whole-video bar (`enhance/PieceBar.svelte`, also in Media's details instead of the
  old coverage bar) has a **filmstrip** of frames along the video above the Cleaned and
  Tuned lanes; hovering a piece shows a frame from its middle and dims the strip outside
  it. New endpoint `GET /media/frame?source=&t=&w=160|320` (cached in
  `_gui/cache/frames/`, `t` kept to 0.1 s).
- `ABDeck` takes `picture: {source, start}` and draws a filmstrip of that stretch above
  the two waveforms (playhead through it; clicking it seeks like the waveform).
- The position in the source survives switching pieces (`lib/playhead.ts`, used by
  `ABDeck`): a piece that covers the last moment heard opens there, on the same side,
  and keeps playing if the switch was within 3 s of the last frame.
- A video's page plays the whole original (`enhance/SourcePlayer.svelte`: proxy when
  prepared, else `/media/file`, with a message when the browser cannot play the file).
  The piece bar gets the player's time, a selection, `onseek` and `onrange`: a click
  jumps, a click-and-drag anywhere (even starting on a piece) chooses a span while the
  player shows the frame under the mouse, then parks at the span's start; a click on a
  piece still opens it. `I`/`O` mark a span at the playhead; *Clean this span…* /
  `Enter` open New clean with it. The span is meant to serve later actions too (cut it
  into a timeline, enhance it), not only cleaning. On other pages a click
  on the bar opens the video page at `?t=`.
- Names shown in the UI pass through `ltr()` (`lib/bidi.ts`), which isolates
  right-to-left runs so mixed Hebrew/digit file names read in order. Display only.
- Candidates shows the source picture for the window (large, above the A/B deck) on the
  A/B clock via `lib/videosync.ts` (`followClock`, shared with Compare), offset by the
  window start; `↑`/`↓` step through candidates in table order, and the candidate card
  (top right) carries its measurements and verdict so the table need not be in view.
- Still to do for phase B: the right-hand Result / Candidates / New / Versions tabs
  around one `ABDeck`. Compare, Candidates and New run are still the 0.5.0 pages (without
  their own breadcrumbs) inside the workspace.

---

## 1. Goals and non-goals

### 1.1 Goals

1. A fixed, full-window workspace of panels instead of scrolling pages, with four
   workspaces switched from a bar at the bottom: **Media, Enhance, Edit, Deliver**.
2. One **Enhance** workspace that merges today's Compare, Candidates and New run pages.
3. A project file format (schema 2) with a list of tracks, typed per-clip effects and
   per-clip picture versions; automatic migration of schema 1.
4. An **Edit** workspace in the classic layout: media pool, viewer, inspector, timeline
   with track headers; snapping, blade tool, real J/K/L shuttle, timecode, audio meters.
5. **Zoom/crop** as the first real per-clip effect, live in the preview and in export.
6. A **Deliver** workspace: export presets beside the render queue.
7. **Compress** as a job that makes a smaller *version* of a source, usable before
   editing, without ever touching the original.
8. Hooks (format and UI slots only) for later upscaling and face reconstruction.

### 1.2 Non-goals (do not build)

- More than one video track (overlays, picture-in-picture). The format allows a list;
  validation accepts exactly one video track.
- Keyframed/animated effects, colour correction, titles, speed changes.
- Upscaling and face reconstruction themselves (§12 designs the slot only).
- A light theme. The app becomes dark-only.
- Dockable/floating panels. Panels are fixed in place and resizable by dragging dividers.
- Reverse playback with sound.

---

## 2. Decisions at a glance

| Topic | Decision |
|---|---|
| Navigation | Bottom page bar: Media · Enhance · Edit · Deliver. No top nav. |
| Page scroll | None. `body` is `100vh`, `overflow: hidden`; panels scroll inside. |
| Words | The folder (`OUTPUT_DIR`) is the **project**; an edit is a **timeline**. Code and files keep `project.json` / `_projects/`. |
| Jobs page | Becomes a drawer opened from the status area of the page bar; the render queue also appears in Deliver. |
| Project format | Schema 2: `tracks` is a list; clips get `effects` and `version`; `music_duck` → `duck`. Migration in `export.migrate()`. |
| Video tracks | Exactly one (V1), magnetic as today (positions derived from order). |
| Audio tracks | Any number, free-positioned clips; per-track mute, gain and "lower under speech". |
| Solo | Preview-only UI state; never saved, never exported. Mute is saved and exported. |
| Viewers | Source and Timeline viewers; mode `Source` / `Timeline` / `Both`. `Both` by default when there is room, else one that switches automatically. |
| Effects | Typed objects (`{"type": "transform", …}`), not raw ffmpeg strings. |
| Versions | A full or windowed alternative picture file for a source (`compress` now; `upscale`, `faces` later), in `OUTPUT_DIR/_versions/`, with a sidecar `kind: "version"`. |
| Cleaning input | Clean/tune jobs always read the **original**, never a version. |
| Originals | The app never deletes or modifies an original. If the user removes one, its versions keep the source usable for editing. |
| Compression codec | HEVC in MP4 tagged `hvc1`; hardware encoder by default, `libx265` for "smallest". |
| Icons | `@lucide/svelte` (pinned exact version, like the other dev dependencies). No emoji in controls. |
| Theme | Dark only; neutral greys; 12 px UI text; 3 px radii. |
| Layout memory | Panel sizes, viewer mode, last timeline: `localStorage` (keys `vac.*`), wrapped in try/catch. |
| Version | 0.6.0; CHANGELOG entry per phase. |

---

## 3. App shell

### 3.1 Structure

```
┌────────────────────────────────────────────────────────────────────────┐
│ top bar (32 px): project folder ▾ │ timeline name · saved │ undo redo  │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                      workspace (fills the rest)                        │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│ page bar (44 px): [job status ▴]   Media  Enhance  Edit  Deliver   v…  │
└────────────────────────────────────────────────────────────────────────┘
```

- **Top bar**: project-folder button (today's "📁 folder" switcher, same behaviour);
  in Edit and Deliver the timeline name with the save state ("saved" / "saving…" /
  "not saved") and Undo/Redo. Nothing else.
- **Page bar**: four buttons, icon above an 11 px label, the active one highlighted.
  Left: the current job ("Tuning candidate 12/36 · 33 %") — click opens the **Jobs
  drawer**, a panel sliding up over the workspace (max 60 % height) with today's
  `Jobs.svelte` content. A count badge shows queued + running jobs. Right: app version.
- Keys: `⇧1`…`⇧4` switch workspaces (not while typing in a field).
- Minimum window 1180 × 700 (`body { min-width: 1180px; min-height: 700px }`); the
  pywebview window opens at 1440 × 900 or the screen size, whichever is smaller
  (`video_audio_cleaner/gui/main.py`).
- Toasts stay, moved to bottom-right above the page bar.

### 3.2 Routes

Hash routing stays (`gui/src/lib/router.svelte.ts`).

| Route | Workspace |
|---|---|
| `#/media` | Media |
| `#/enhance` · `#/enhance/<sourceId>?out=<file>&plan=<planPath>&win=<label>&tab=result\|candidates\|new\|versions` | Enhance |
| `#/edit` (timeline list) · `#/edit/<timeline>` | Edit |
| `#/deliver` · `#/deliver/<timeline>` | Deliver |

Old routes redirect (keep them working — the user may have them open):
`#/` → `#/media`; `#/compare/<file>` → Enhance on that output's source with `out`;
`#/candidates/<plan>/<label>` → Enhance with `plan`, `win`, `tab=candidates`;
`#/new?source=…` → Enhance with `tab=new`; `#/jobs` → current workspace with the drawer
open.

Edit and Deliver with no timeline in the route open the last one (`vac.lastTimeline`),
else show the timeline list (today's `Projects.svelte`) centred in the workspace.

### 3.3 Shared building blocks (new components)

| Component | Purpose |
|---|---|
| `components/Split.svelte` | Two panes with a draggable 4 px divider. Props: `direction` (`row`/`column`), `key` (persisted size), `initial`, `min`, `max` (px or fraction), `fixed` (`first`/`second`: which pane keeps its pixel size on window resize). Double-click the divider resets. |
| `components/Panel.svelte` | A panel: 28 px header (title, optional tabs, optional right-side actions snippet) and a scrolling body. |
| `components/Icon.svelte` | Thin wrapper choosing a lucide icon by name, 16 px default, `currentColor`. |
| `components/JobsDrawer.svelte` | The drawer of §3.1; wraps the job list extracted from `Jobs.svelte`. |
| `components/Timecode.svelte` | Large mono timecode; click to type a time and jump (accepts everything `lib/time.ts` parses, plus `HH:MM:SS:FF`). |
| `components/Meter.svelte` | Stereo peak meter (§7.7). |

### 3.4 Visual tokens (`gui/src/app.css`)

Replace the token block; delete the light-theme media query.

```css
:root {
  --bg: #1c1c1f;          /* workspace */
  --bg-raised: #252528;   /* panels */
  --bg-header: #2c2c30;   /* panel headers, bars */
  --bg-sunken: #151517;   /* viewers, timeline lanes */
  --bg-hover: #34343a;
  --gap: #0d0d0f;         /* the dark seams between panels (1 px) */
  --border: #3a3a40;
  --text: #dcdce0;
  --text-dim: #96969e;
  --text-faint: #68686f;
  --accent: #4d9cf5;      /* focus, links, selected controls */
  --playhead: #e5484d;
  --select: #f2f2f4;      /* selected-clip outline */
  --clip-video: #35607f;
  --clip-speech: #2f6b4d; /* cleaned sound; original sound uses --clip-speech-raw */
  --clip-speech-raw: #6b5a2f;
  --clip-music: #5b4d8a;
  --clip-sound: #7f5a35;
  --a-color: #f2a65a;     /* original (unchanged) */
  --b-color: #5ad1a0;     /* cleaned (unchanged) */
  --warn: #e8c15a; --danger: #ef6b6b; --ok: #5ad1a0;
  --radius: 3px;
  --mono: ui-monospace, "SF Mono", Menlo, monospace;
  color-scheme: dark;
}
body { font: 12px/1.4 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
```

Panel headers: 11 px, uppercase, `letter-spacing: .04em`, `--text-dim`. Buttons 24 px
tall; icon buttons 24 × 24 with a `title` and `aria-label`. Focus ring: 1 px `--accent`.
Check contrast of `--text-dim` on `--bg-raised` stays ≥ 4.5:1.

---

## 4. Project format, schema 2

### 4.1 The file

`OUTPUT_DIR/_projects/<name>/project.json`:

```jsonc
{
  "schema": 2,
  "name": "Wedding highlights",
  "canvas": null,                       // or {"width": 1920, "height": 1080, "fps": 25}
  "sources": {
    "<sourceId>": {
      "path": "/abs/original.mp4",
      "probe": { … },                   // as today
      "versions": {                     // only versions some clip uses
        "<versionId>": {"path": "/abs/_versions/x.compressed.mp4", "type": "compress",
                        "same_picture": true, "window": null, "probe": { … }}
      }
    }
  },
  "tracks": [
    {"id": "V1", "kind": "video", "name": "Video", "locked": false,
     "audio_muted": false,              // mutes the clips' own (speech) sound
     "clips": [
       {"id": "c1", "source": "<sourceId>", "version": null,   // null = the original
        "in_sec": 12.0, "out_sec": 31.5,
        "audio": {"file": "/abs/out.mp4", "output": "out.mp4", "start_sec": 0, "gain_db": 0},
        "transition_in": null,          // or {"type": "crossfade", "sec": 0.5}
        "effects": [{"type": "transform", "zoom": 1.5, "x": 0.1, "y": -0.05}]}
     ]},
    {"id": "A1", "kind": "audio", "name": "Music", "locked": false, "muted": false,
     "gain_db": 0, "duck": true,        // lowered under speech
     "clips": [
       {"id": "m1", "file": "/abs/_projects/<name>/audio/song.mp3", "name": "song.mp3",
        "at_sec": 0, "in_sec": 0, "out_sec": 95.0, "file_duration_sec": 212.4,
        "gain_db": -18, "fade_in_sec": 1, "fade_out_sec": 3}
     ]},
    {"id": "A2", "kind": "audio", "name": "Sounds", "locked": false, "muted": false,
     "gain_db": 0, "duck": false, "clips": []}
  ],
  "duck": {"threshold_db": -35, "ratio": 6, "attack_ms": 20, "release_ms": 600},
  "ripple_audio": true,                 // §7.4: audio follows ripple edits on V1
  "export": {"preset": "best", "codec": "h264", "encoder": "libx264", "crf": 18,
             "preset_speed": "medium", "height": null, "audio_bitrate": "192k",
             "target_lufs": -16, "target_tp": -1.5, "output": null},
  "created": "…", "modified": "…"
}
```

Rules:

- Exactly one track with `kind: "video"`; it is first. Its clips are **sequential**
  (position = order, crossfades overlap the previous clip) exactly as schema 1.
- Audio tracks hold free-positioned clips (`at_sec`). Clips on one track may not overlap
  in the UI (a drop that would overlap is refused, the ghost turns red); the exporter
  tolerates overlaps by mixing.
- Track ids are `V1`, `A1`, `A2`, … and never reused inside a project.
- The video clip's `audio` object is unchanged from schema 1.
- `effects` is an ordered list; unknown types are an error at validation (never silently
  dropped). Types in this plan: `transform` (§8) and `ffmpeg` (`{"type": "ffmpeg",
  "filter": "hflip"}` — export-only, no preview, exists so schema-1 `video_filters`
  survive migration; no UI creates it).
- `export.preset` names the Deliver preset that produced the concrete settings (§9);
  the concrete settings are what `export.py` reads. `export.preset` (x264's speed preset
  in schema 1) is renamed `preset_speed`.

### 4.2 Migration (`export.migrate(project) -> project`)

Pure function, schema 1 → 2, idempotent on schema 2:

- `tracks.video` → track `V1` (`audio_muted: false`); each clip gains `version: null`
  and `effects` = `[{"type": "ffmpeg", "filter": f} for f in video_filters]`;
  `video_filters` removed.
- `tracks.music` → track `A1` named "Music", `duck = music_duck.enabled` (default true),
  `gain_db: 0`. Always also create an empty `A2` "Sounds" with `duck: false`.
- `tracks.music_duck` minus `enabled` → top-level `duck`.
- `export.preset` → `export.preset_speed`; add `codec: "h264"`, `height: null`,
  `preset: "best"` (or `"fast"` when the encoder is `h264_videotoolbox`).
- `ripple_audio: true`.

Called by `export.load()` and `gui/projects.load()`; the frontend only ever sees
schema 2. `projects.save()` writes schema 2 and, the first time it overwrites a schema-1
file, first copies it to `project.v1.json` beside it. `export.SCHEMA = 2`;
`projects.new()` creates V1, A1 "Music" (duck) and A2 "Sounds".

### 4.3 Model helpers to change

- `export.clips(project)` reads the video track's clips (helper
  `export.video_track(project)`, `export.audio_tracks(project)`).
- `export.media_of(project, clip)` → `{"path", "probe"}`: the clip's version when
  `clip.version` is set, else the source. Used wherever `source_of(...)["path"]` /
  `["probe"]` is used today for picture (`canvas`, `plan`, `_deinterlace`, `video_graph`).
  A clip with `version: null` whose original file is missing raises `ProjectError`
  ("the original of <name> is missing; choose a version for this clip").
- `gui/src/lib/edit.ts`: new types `Track`, `VideoTrack`, `AudioTrack`, `AudioClip`
  (today's `MusicClip`), `Effect`, `Project` (schema 2); helpers `videoTrack(p)`,
  `audioTracks(p)`, `transformOf(clip)`; `place`, `clipAt`, `split`, `coveringOutput`,
  `audioFor` unchanged in behaviour.

---

## 5. Media workspace (replaces Library)

```
┌───────────┬──────────────────────────────────────────┬──────────────┐
│ Bins      │ grid of videos                           │ Details      │
│ All (5)   │ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      │ (selected)   │
│ To clean  │ │poster│ │      │ │      │ │      │      │              │
│ Cleaned   │ └──────┘ └──────┘ └──────┘ └──────┘      │              │
│ Timelines │  name     …                              │              │
│ Exports   │  12:31 · ●cleaned 40 % · compressed      │              │
│ Other     │                                          │              │
└───────────┴──────────────────────────────────────────┴──────────────┘
```

- **Bins** (left, 180 px): All videos · To clean (no outputs) · Cleaned · Timelines
  (the timeline list, opening Edit) · Exports (files with `kind: "export"` sidecars) ·
  Other files (today's "unknown" list). Counts beside each.
- **Grid**: cards 220 px wide, poster on top (16:9 box, letterboxed), then name (ellipsis,
  full path in `title`), then one line: duration · resolution · badges. Badges: cleaned
  coverage (share of the duration covered by existing video outputs), `compressed`,
  `ready to edit` (prepared), `original missing`. A size slider in the panel header
  (160–320 px). Toolbar: "Add video…" (existing add-source flow), filter-by-name box.
- **Hover scrub**: moving the mouse across a poster shows the frame at that horizontal
  position, from 12 frames across the video; leaving returns to the default frame
  (index 4 of 12). Endpoint change: `/media/poster?source=&i=&n=` with `n` ∈ {4, 12}
  (default 4); cache file `posters/<id>_<n>_<i>.jpg`; 12-frame posters are 240 px wide.
  Load the 12 lazily on first hover of that card, not for the whole grid.
- **Details** (right, 320 px) for the selected card: the 4-frame `SourceStrip`, file
  facts from the probe (duration, size, resolution, frame rate, codec, interlaced),
  then sections:
  - *Cleaned sound*: each output (window label, tag, date) → opens Enhance on it; a
    `CoverageBar`. Button **Clean…** → Enhance, `tab=new`.
  - *Versions*: each version (type, size, saving vs the original) with Reveal and
    Delete (to `_trash/`); button **Compress…** (§10.3).
  - *Editing*: "Prepare for editing" (today's prep job) or "Ready"; **Add to
    timeline…** (pick a timeline or create one; appends the whole video).
  - Reveal in Finder.
- Double-click a card → Enhance on that source. Dropping files/folders on the window
  behaves as today.
- Everything `Library.svelte` did (delete output, rescan, etc.) lives in the Details
  panel or the Enhance workspace; nothing is lost.

---

## 6. Enhance workspace (merges Compare, Candidates, New run)

```
┌───────────┬────────────────────────────────────────┬──────────────────┐
│ Videos    │ viewer + A/B deck                      │ Result │Cand.│New│Ver. │
│ (compact  │ (ABDeck: picture, A/B switch, waveform,│                  │
│  list,    │  loop, level match)                    │  tab content     │
│  posters) ├────────────────────────────────────────┤                  │
│           │ whole-video bar: cleaned windows,      │                  │
│           │ tuning windows; click one to load it   │                  │
└───────────┴────────────────────────────────────────┴──────────────────┘
```

- **Left** (220 px): compact source list (small poster, name, badges). Selecting sets
  `<sourceId>` in the route.
- **Centre top**: today's `ABDeck` unchanged in behaviour, fed by the current selection:
  an output (`out`) → original vs cleaned for that window; a tuning window with
  candidates (`plan` + `win`) → original vs the chosen candidate. With nothing selected:
  the source poster and "Pick a cleaned window below, or start a new clean".
- **Centre bottom** (96 px): the whole source as a bar (reuse/extend `CoverageBar`):
  one lane of cleaned outputs (green; overlapping windows stack into sub-lanes), one
  lane of tuning windows (blue). Click a segment to load it; the loaded one is
  outlined. Hover shows label, tag and date. Dragging on empty bar space selects a
  range and opens `tab=new` with that start/duration.
- **Right** (360 px), tabs:
  - **Result** — what `Compare.svelte` shows beside the player: settings used, autotune
    record, loudness, reproduce command, Reveal, Delete, "Use in a timeline".
  - **Candidates** — `Candidates.svelte`'s scatter and list for the tuning window;
    "Pick this one" and "Render this candidate" as today. Selecting a candidate swaps
    side B of the deck.
  - **New** — the `NewRun.svelte` form (window, tape/digital, autotune, music
    separation, tag, advanced overrides, command preview, Start).
  - **Versions** — list and **Compress…** as in Media details; later the place for
    upscaling and face reconstruction (§12).
- Implementation: split each old route into a body component without page chrome
  (`enhance/ResultPanel.svelte`, `CandidatesPanel.svelte`, `NewRunPanel.svelte`,
  `VersionsPanel.svelte`) and one `routes/Enhance.svelte` that owns the single `ABDeck`
  and the selection. Delete `Compare.svelte`, `Candidates.svelte`, `NewRun.svelte`,
  `Library.svelte` once nothing references them. Keyboard shortcuts of `ABDeck` are
  unchanged.

---

## 7. Edit workspace

### 7.1 Layout

```
┌───────────┬───────────────────────────────────────────┬─────────────┐
│ Media pool│  Source viewer      │  Timeline viewer    │ Inspector   │
│ Videos│Audio                  │                     │             │
│ (list)    │  transport          │  transport          │             │
├───────────┴───────────────────────────────────────────┴─────────────┤
│ toolbar: ↖ select  ✂ blade │ snap  link │ zoom ──●── fit │ 00:01:23:10 │
├──────────────┬──────────────────────────────────────────────┬───────┤
│ V1  Video  🔒│ ruler                                        │ meter │
│ A1  Speech MS│ ▓▓▓▓▓▓▓▓▓▓▓│▓▓▓▓▓▓▓▓│▓▓▓▓▓▓                    │  ▮▮   │
│ A2  Music  MS│ ░░░░░░░░░░░░░░░░░░░░░                        │  ▮▮   │
│ A3  Sounds MS│      ░░░        ░░                           │       │
│ + audio track│                                              │       │
└──────────────┴──────────────────────────────────────────────┴───────┘
```

`Split` panes: top/bottom (timeline height, default 300 px, min 180), media pool width
(240), inspector width (300). Break `Editor.svelte` (639 lines) up:

| New file | Holds |
|---|---|
| `routes/Edit.svelte` | Layout, project load/save/undo (the existing `commit`/`undoStep`/`flush` logic), selection, keyboard map |
| `edit/MediaPool.svelte` | §7.2 |
| `edit/SourceViewer.svelte` | Today's source viewer (proxy video, scrub, In/Out, add) |
| `edit/TimelineViewer.svelte` | The two alternating `<video>`s, `Program` wiring, transport |
| `edit/Inspector.svelte` (+ small section components) | §7.6 |
| `edit/Toolbar.svelte`, `edit/TrackHeaders.svelte` | §7.3–7.4 |
| `components/Timeline.svelte` | Lanes only; generalised to N audio tracks |

Rows shown: V1 picture; **A1 "Speech"** — a *linked* row drawn under V1 from the same
clips (it is not a track in the file; its mute button is `V1.audio_muted`); then the
audio tracks, displayed as A2, A3… (display numbering = file index + 1; the file ids
stay `A1`, `A2`).

### 7.2 Media pool

- Tab **Videos**: the project's sources as rows (48 px poster, name, duration,
  "prepare" state). Click → loads in the Source viewer. Unprepared sources show
  "Prepare" (the existing prep job) instead of loading. Sources whose original is
  missing are usable when a version exists (§10.5).
- Tab **Audio**: audio files in `_projects/<name>/audio/` (and legacy `music/`), with
  duration; "Import…" (existing music picker; new files are copied to `audio/`).
- Drag a video row onto the timeline's V1 → inserts the marked range (or the whole
  video) at the drop position between clips (existing insert-indicator logic). Drag an
  audio row onto an audio track → places it at the drop time (snapped).

### 7.3 Viewers and transport

- Viewer mode button group in the viewer header: Source · Timeline · Both. Stored in
  `vac.viewer.mode`. In single mode the viewer switches automatically: to Source when a
  pool item is clicked or `I`/`O` is pressed in the source; to Timeline on any timeline
  click, play, or seek.
- **Timecode**: `HH:MM:SS:FF`, where `HH:MM:SS` is real time and `FF =
  floor(fraction × fps)` (no drop-frame arithmetic). `formatTimecode(t, fps)` and
  `parseTimecode(text, fps)` in `lib/time.ts`, with tests. The big one sits in the
  toolbar (timeline time); each viewer shows its own time small. `fps` = the canvas's.
- Transport buttons under each viewer: first frame, previous cut, play/pause, next cut,
  last frame, loop (timeline viewer only: loops the whole edit).
- **Shuttle** (timeline viewer and source viewer):
  - `L`: play forward; each further `L` doubles: 1× → 2× → 4× → 8×.
  - `J`: the same backwards.
  - `K` or `Space`: stop, rate back to 1×.
  - Forward up to 2× plays sound (pitch rises; acceptable for scanning), above that
    the sound is muted.
  - Backwards is silent and done by seeking: a `requestAnimationFrame` loop moves the
    time by `rate × elapsed` and seeks the visible video at most every 80 ms (proxies
    have a 15-frame GOP, so seeks are quick).
  - `Program` gains `setRate(rate)` (reschedules audio nodes with
    `playbackRate = rate`, start times and durations divided by `rate`; time becomes
    `startOffset + (ctx.currentTime − startedAt) × rate`; video `playbackRate` nominal
    = `rate`, the ±10 % drift nudging multiplies it) and `shuttle(rate)` for negative
    rates. The rate shows beside the timecode (`2×`, `−4×`).

### 7.4 Timeline behaviour

Kept from 0.5.0: drag to reorder V1 clips, drag edges to trim, drafted while dragging
and committed on release; thumbnails in V1 clips; ⌘-scroll zoom; insert indicator;
auto-switch of a clip's sound when a trim leaves its cleaned output.

New:

- **Track headers** (132 px column, fixed while lanes scroll horizontally): display
  name, **M** (mute, saved), **S** (solo, preview only — a small "preview only" tooltip),
  lock. Locked tracks ignore drags, blade and delete. Double-click a name to rename.
  "+ Audio track" below the last (default name "Audio N", `duck: false`). An empty
  audio track can be removed from its header's context menu.
- **Speech row**: each V1 clip's sound drawn as a waveform from the source's peaks
  (`/media/peaks?source=`), coloured `--clip-speech` when a cleaned output is used,
  `--clip-speech-raw` for the original, hatched grey when muted. Not draggable (it
  moves with V1); clicking selects the V1 clip.
- **Audio-track waveforms**: peaks for audio files via `/media/peaks?file=<abs path>`
  (file must be inside the project folder; computed on demand with `prep.peaks`, cached
  in `_gui/cache/peaks/` keyed by path id + mtime). Drawn with `lib/peaks.ts`.
- **Ruler**: timecode labels; tick spacing chosen from the zoom as today.
- **Playhead**: `--playhead`, 1 px line with a 9 px head in the ruler; dragging in the
  ruler scrubs.
- **Snapping** (toggle `N`, on by default): while dragging a clip, an edge or the
  playhead, snap to any clip edge on any track, the playhead, and 0, within 8 px; draw a
  1 px `--select` guide at the snap time. Holding `⌘` during the drag disables it.
  Implement as a pure function `snap(t, candidates, pps, threshold)` in `lib/edit.ts`,
  tested.
- **Tools**: Select (`A`) and Blade (`B`). In blade mode a vertical cut line follows the
  mouse over clips and a click splits that clip there (V1: `split`; audio clips:
  new `splitAudio`). `S` still splits at the playhead (V1 clip under it, plus selected
  audio clips under it).
- **Linked ripple** (toggle in the toolbar, saved as `project.ripple_audio`, on by
  default): when a V1 insert, delete or trim changes when later clips play, audio clips
  on *unlocked* audio tracks that start at or after the edit point move by the same
  amount (clips starting inside a deleted range move to its start; never before 0).
  Reordering V1 clips does not move audio. Pure function `rippleAudio(tracks, at,
  delta)` in `lib/edit.ts`, tested.
- **Selection**: click selects; `⇧`-click adds; `⌫` deletes all selected; `⌘C`/`⌘V`
  copy and paste selected clips (V1 clips paste as an insert at the playhead; audio
  clips paste onto their own track at the playhead, refused where they would overlap);
  `⌘A` selects all on unlocked tracks.
- **Zoom**: slider, `+`/`−`, ⌘-scroll, and `⇧Z` fits the whole edit to the lane width.
- `↑` / `↓`: jump to the previous / next cut (V1 clip starts and the end).

### 7.5 Keyboard map (Edit)

| Key | Action |
|---|---|
| `Space`, `K` | Play/pause · stop |
| `J` / `L` | Shuttle backward / forward (repeat to double) |
| `←` / `→` (`⇧`) | One frame (one second) |
| `↑` / `↓` | Previous / next cut |
| `Home` / `End` | Start / end |
| `I` / `O` | Mark in / out in the source viewer |
| `,` | Add the marked range at the end · `.` insert at the playhead |
| `A` / `B` | Select tool / blade tool |
| `S` | Split at the playhead |
| `N` | Snapping on/off |
| `⌫` | Delete selected |
| `⌘Z` / `⇧⌘Z` | Undo / redo |
| `⌘C` / `⌘V` / `⌘A` | Copy / paste / select all |
| `+` / `−` / `⇧Z` | Zoom in / out / fit |
| `⇧1`–`⇧4` | Workspaces |

Show the map in a popover from a "?" icon in the toolbar; remove the one-line key hint.

### 7.6 Inspector

Sections with collapsible headers; what shows depends on the selection.

- **Nothing selected — Timeline**: Canvas (Same as first clip / listed source sizes /
  720p / 1080p / 4K; frame rate) — today's `setCanvas`/`setFps`; Lower music under
  speech (threshold, amount = ratio, release) editing `project.duck`; a line "Loudness
  and file settings are in Deliver".
- **Video clip**:
  - *Clip*: source name; In, Out, Length as timecode fields.
  - *Picture*: "Use" select — Original / each version of the source that covers the
    clip (label by type, e.g. "Compressed (1080p)"). Sets `clip.version` and records the
    version under `project.sources[id].versions`.
  - *Zoom and position* (§8): Zoom slider 1.0–4.0× (number field up to 8), X and Y
    (−50…50 %, limited to what keeps the picture inside the frame), Reset. Hint: "Drag
    the picture in the viewer to move it".
  - *Sound*: cleaned / original / muted select and Gain (today's controls).
  - *Start*: Cut / Crossfade + length (today's `transition_in`).
- **Audio clip**: file name; Start; In, Out; Volume (dB); Fade in, Fade out.
- **Track header clicked**: Name; Volume (dB); "Lower under speech" checkbox (`duck`).
- Several clips selected: only the fields they share (Volume, Zoom reset), applied to
  all.

All edits go through the existing `commit()` so undo covers them; number fields commit
on change, sliders draft while dragging and commit on release (one undo step).

### 7.7 Audio meter

`Program` routes everything through one master `GainNode` → `ChannelSplitter` → two
`AnalyserNode`s (fftSize 1024). `Meter.svelte` reads peak per channel each animation
frame, shows two vertical bars with a dBFS scale (0, −6, −12, −24, −48), 1.5 s peak
hold, red above −1 dBFS. Tooltip: "Preview level, before the export's loudness
adjustment".

### 7.8 Playback engine changes (`lib/program.ts`)

- `setEdit(clips, audioTracks, opts)`: all audio-track clips are scheduled (today's
  music scheduling per clip), each through a per-track `GainNode` (track `gain_db`;
  0 when muted, or when another track is soloed). The speech row has its own gain node
  for `audio_muted`/solo.
- `Resolver` gains `frameStyle?(clip): { transform: string }` (§8.2) and, for later
  versions with their own proxy, `proxy(sourceId, versionId?)`.
- `setRate` / `shuttle` (§7.3) and the master node for the meter (§7.7).
- Still no ducking in the preview (Web Audio cannot sidechain); keep the existing note,
  now in the Inspector's ducking section.

---

## 8. Zoom and position (the `transform` effect)

### 8.1 Meaning

`{"type": "transform", "zoom": Z, "x": X, "y": Y}` — the visible part of the source
frame is a rectangle of size (W/Z, H/Z) centred at ((0.5 + X)·W, (0.5 + Y)·H).
`1 ≤ Z ≤ 8`; `|X|, |Y| ≤ (1 − 1/Z)/2` (clamped on input and again at export). `Z = 1`
with `X = Y = 0` is the identity and is stored as *no effect* (remove the entry). At
most one `transform` per clip.

### 8.2 Preview

In `TimelineViewer`, each of the two `<video>` elements sits in a `.frame` box that has
the **source's display aspect ratio**, is fitted (contain) and centred in the screen,
and has `overflow: hidden`. The video fills the box and gets

```css
transform: scale(Z) translate(calc(-100% * X), calc(-100% * Y));  /* origin: centre */
```

so bars from an aspect mismatch between source and canvas stay where the export puts
them. `Program` applies `resolve.frameStyle(clip)` to a video whenever it assigns a clip
to it (including the preloaded next clip). Dragging in the viewer while paused, with one
zoomed clip selected, changes X/Y (drafted during the drag, committed on release);
`⌥`-scroll changes Z around the pointer.

### 8.3 Export (`export.video_graph`)

For a clip with a transform, after deinterlacing and before scaling:

```
crop=w=trunc(iw/Z/2)*2:h=trunc(ih/Z/2)*2:x=(iw-ow)/2+X*iw:y=(ih-oh)/2+Y*ih
```

and that clip's `scale` uses `flags=lanczos`. `ffmpeg` effects append their filter
string at the same point, in list order. `export.plan()` gives the reason "zoomed clips
need re-encoding" (and keeps "visual edits need re-encoding" for `ffmpeg` effects), so
such a timeline is never stream-copied.

### 8.4 Tests

- Python: the crop expression for a given Z/X/Y; clamping; a zoomed timeline plans as
  `encode`; (slow-free) render of the fixture with `Z = 2, X = 0.25` and a check that
  the output frame differs from the unzoomed render and has the canvas size.
- TS: `transformOf`, clamping helper, `frameStyle` string.

---

## 9. Deliver workspace and export settings

```
┌────────────────────┬──────────────────────────────┬──────────────────┐
│ Render settings    │ Timeline viewer (read-only)  │ Render queue     │
│ timeline ▾         │                              │ ▸ Wedding… 42 %  │
│ ○ Best quality     │ what will happen:            │ ✓ Trip… Reveal   │
│ ○ Smaller file     │ "Re-encoded because: clips   │                  │
│ ○ Fast             │  come from more than one     │                  │
│ ○ Lossless cut     │  video"                      │                  │
│ size ▾ loudness ▾  │                              │                  │
│ file name          │ ~1.2 GB · about 9 min        │                  │
│ [Add to queue]     │                              │                  │
└────────────────────┴──────────────────────────────┴──────────────────┘
```

- **Presets** (radio list; choosing one writes concrete values into `project.export`):

  | Preset (`export.preset`) | Settings |
  |---|---|
  | Best quality — YouTube, Google Photos (`best`, default) | `codec h264`, `libx264`, CRF 18, speed `medium` |
  | Smaller file (`small`) | `codec hevc`, `libx265`, CRF 24, speed `medium`, `-tag:v hvc1` |
  | Fast (`fast`) | `codec h264`, `h264_videotoolbox`, today's bitrate table |
  | Lossless cut (`copy`) | Stream copy. Enabled only when `GET /api/projects/{name}/plan` says copy is possible; otherwise disabled with the plan's reasons listed under it |

  When the plan allows copy and the user has not chosen otherwise, preselect
  Lossless cut (today's behaviour: copy when possible). Choosing another preset while
  copy is possible sends `--reencode`.
- **Size**: Same as timeline · 1080p · 720p (`export.height`; the canvas is scaled to
  that height keeping aspect, width rounded to even; never upscaled).
- **Loudness**: target (−16 LUFS default; −14 for YouTube-loud) and ceiling — today's
  `target_lufs` / `target_tp`.
- **File name** and folder (default: today's `export.output_path`). Warn before
  overwriting an existing export.
- **Estimate**: duration × bitrate guess per preset and size (a small table in
  `lib/deliver.ts`; mark as approximate), and time from fixed rates as the New-run form
  does today.
- **Centre**: `TimelineViewer` reused read-only (play, scrub, timecode) above the plan
  explanation.
- **Right**: jobs of type `export` and `compress` for this project folder — progress,
  Cancel, Retry, Reveal, log — the same rows as the Jobs drawer, filtered.
- `export.py` changes: `settings()` understands `codec`, `preset_speed`, `height`;
  `encoder_args` gains `libx265` (`-crf`, `-preset`, `-tag:v hvc1`) and
  `hevc_videotoolbox`; `canvas()` applies `height`; `plan()` treats `preset == "copy"`
  as a request that fails loudly if copy is not possible. Keep `+faststart`. Also pass
  `-map_metadata 0` from the first clip's media so the creation date survives (Google
  Photos sorts by it); verify on the fixture that the tag is present.
- The Editor's Export dialog is removed; its button becomes "Deliver…" → `#/deliver/<timeline>`.

---

## 10. Compression and source versions

### 10.1 Why and what

Large camera files (4K phone video, DV transfers) take disk space and are slow to copy.
A **version** is another file with the same content and timing as (part of) a source.
`compress` versions are full-length, picture-equivalent, smaller files. The editor can
use a version instead of the original for any clip. Later version types (§12) share the
mechanism.

Editing smoothness is *not* a reason to compress: the editor already plays 540p proxies.

Recommended order per video, to state in the UI where relevant: enhance → compress →
edit → deliver. Cleaning always reads the original (best sound); cleaned outputs are
addressed by source time, so they stay valid for every version.

### 10.2 CLI: `clean-audio-compress` (`video_audio_cleaner/compress.py`)

```
clean-audio-compress SOURCE --output_dir DIR [--preset balanced|small] [--height N]
                     [--estimate] [--progress json]
```

- Output: `DIR/_versions/<source stem>.compressed[-<height>p].mp4` (+ `.partial` while
  writing, registered with `procs` for cleanup, replaced atomically like every other
  tool here) and a sidecar `<output>.params.json`:

  ```json
  {"kind": "version", "output": "x.compressed.mp4", "created": "…",
   "version": {"type": "compress", "source": "/abs/original.mp4", "preset": "balanced",
               "height": null, "same_picture": true, "window": null},
   "results": {"size": 3210000000, "source_size": 24100000000, "seconds": 812},
   "command": ["ffmpeg", "…"]}
  ```
- Encoding:

  | Preset | Video |
  |---|---|
  | `balanced` (default) | `hevc_videotoolbox -q:v 60` |
  | `small` | `libx265 -crf 26 -preset medium` |

  Common: `-tag:v hvc1`, `-pix_fmt yuv420p`, keyframe every 2 s (`-g round(2×fps)`),
  `-fps_mode passthrough` (timestamps unchanged so clip times stay valid),
  `-map 0:v:0 -map 0:a:0?`, `-map_metadata 0`, `-movflags +faststart`. Audio: `-c:a
  copy` when the source audio is AAC, else AAC 256k. Interlaced sources (probe
  `field_order`) are deinterlaced with `bwdif=mode=send_frame` first. `--height N`
  scales down to N lines (never up).
  The two quality numbers are starting values. Check them on the fixture for sanity
  only; ask the user before trying them on one real file, then adjust if the result is
  visibly soft or not much smaller.
- `--estimate`: encodes three 5-second samples (at 10 %, 50 %, 90 %) to a temp dir,
  prints (and emits as a `result` event) the projected size and the measured speed
  (× real time). No output file.
- Before encoding: refuse if the free space in `DIR` is below the estimate × 1.2 (when
  an estimate was not run, below 50 % of the source size).
- After encoding: if the result is not at least 10 % smaller than the source, delete it
  and finish with a `result` event `{"kept": false, "reason": "not smaller"}` and a
  plain message; exit code 0.
- Check the output's duration is within one frame of the source's; otherwise fail.
- Events: the standard `start/step/progress/result/done/error` via `events.session`.
- Register the script in `pyproject.toml` and document it in the README.

### 10.3 Server and UI

- `commands.compress_argv(source_path, output_dir, preset, height)`; job type
  `compress` in the heavy lane (rename the lane `ml` → `heavy` in `jobs.py`; it now
  holds `clean`, `clean_candidate`, `tune`, `export`, `compress`).
- `POST /api/compress/estimate {source_id, preset, height}` runs `--estimate`
  synchronously in a thread (it takes seconds) and returns `{size, source_size,
  speed}`.
- **Compress… dialog** (from Media details and Enhance → Versions): preset radio
  (Balanced — fast / Smallest — slow), "Reduce to 1080p" checkbox when the source is
  larger, the estimate ("About 3.1 GB instead of 24 GB · roughly 14 min"), the
  recommended-order hint when the source has no cleaned outputs yet is *not* needed
  (cleaning uses the original either way); instead show: "The original is kept. Picture
  enhancement works best on the original, so keep it until you are done enhancing."
  Buttons: Start, Cancel.
- `library.scan`: also read `_versions/*.params.json`; each source gains

  ```json
  "versions": [{"id": "<path id>", "path": "…", "type": "compress", "same_picture": true,
                "window": null, "size": 0, "created": "…", "exists": true,
                "preset": "balanced", "height": null}],
  "original_exists": true,
  "media_path": "/abs/…"      // the original if present, else the best existing version
  ```
  `exists` on a source becomes "the original or some version exists". Version sidecars
  never appear as outputs or stray files.
- Deleting a version moves file and sidecar to `_trash/_versions/`; refused (409) while
  a timeline's clip uses it, naming the timeline.

### 10.4 Editor use

- A version with `same_picture: true` reuses the source's proxy, thumbnails and peaks.
- Inspector → Picture → Use (§7.6). "Add to timeline" and drags from the pool default
  to the original when it exists, else the newest covering version.
- `export.plan()`: copy needs one *media file* (same source and same version for every
  clip); the output keeps that file's codec (HEVC stays HEVC, tagged `hvc1`).

### 10.5 When the original is gone

The app never removes it, but the user may, to free space.

- The source stays in the library (`original_exists: false`, badge "original missing").
- Posters, probe and prep come from `media_path`; an existing proxy keeps working.
- Clean, Tune and Compress are disabled for it with the reason "needs the original
  file".
- In timelines, clips with `version: null` on such a source are flagged in the lane
  (warning stripe) and the Inspector offers "Use the compressed version"; a one-click
  "Fix all" switches every such clip. Export refuses with the message of §4.3 until
  fixed.

---

## 11. Server and API summary

| Change | Where |
|---|---|
| Sources carry `versions`, `original_exists`, `media_path` | `library.scan` |
| `GET /media/poster` takes `n` (4 or 12) | `app.py`, `media.py` |
| `GET /media/peaks?file=` for project audio | `app.py`, `media.py`, `prep.peaks` |
| Projects are schema 2; `POST /api/projects/{name}/audio` (keep `/music` as an alias) copying into `audio/` | `projects.py`, `app.py` |
| `GET /api/projects/{name}/plan` reflects effects, versions, `export.preset` | `export.plan` |
| Job type `compress`; lane `heavy`; `POST /api/jobs/preview` supports it | `jobs.py`, `commands.py`, `app.py` |
| `POST /api/compress/estimate` | `app.py` |
| `POST /api/versions/delete` | `app.py` |
| Exports listed for the Media "Exports" bin (`library.scan` → `"exports": [...]` from `kind: "export"` sidecars) | `library.py` |

All new `/api` routes sit behind the existing guard (`Host` check and `x-vac: 1`);
`/media/peaks?file=` must apply the same inside-the-project check as `/media/file`.

---

## 12. Later: upscaling and face reconstruction (design the slot, build nothing)

- They are **version types** (`"type": "upscale" | "faces"`, `same_picture: false`),
  each produced by its own CLI (`clean-video-upscale`, …) with `--progress json`, run as
  heavy-lane jobs — the audio-cleaning pattern again.
- They will usually be **windowed** (an hour of video is too slow to enhance on this
  machine): `version.window = {"start_sec", "duration_sec"}`. A clip may use a windowed
  version only when the window covers the clip — the rule `coveringOutput` already
  implements for cleaned sound; generalise it rather than writing a second one.
- `same_picture: false` versions need their own proxy and thumbnails (prep keyed by
  version id; `Resolver.proxy(sourceId, versionId)` is already in §7.8).
- Enhance gets a second comparison mode for them: a picture A/B (split-screen wipe
  between original and version) in the same centre viewer. Leave `ABDeck`'s structure
  open to that (do not hard-wire "B is audio").
- A convenient later flow: "Enhance this clip" from the Edit inspector, which queues
  the job for exactly the clip's range and switches the clip to the result when done.
- Compressing an enhanced version: `compress` should accept a version as its input
  later (`version.parent`). Not now.

---

## 13. Phases

Each phase ends with: `./venv/bin/pytest` green, `npm run check && npm test && npm run
build` clean, a browser pass on the scratch project (§0.2), `docs/gui-design.md` §0 and
this document's status line updated, a CHANGELOG entry. Phases A–G are in dependency
order; H needs only A and C and may be pulled forward if the user asks for compression
first.

### Phase A — Shell, look, Media

Tokens and dark-only theme; `Split`, `Panel`, `Icon`; top bar, page bar, Jobs drawer;
routes and redirects (§3). Media workspace (§5) including 12-frame hover scrub. Enhance,
Edit and Deliver initially host the existing pages unchanged inside the new shell
(Compare/Candidates/NewRun reachable through redirects; the old Editor under Edit; the
timeline list under Deliver with "open in Edit").

Accept: no page-level scrollbar at 1180 × 700 or 1470 × 900; every 0.5.0 action is
still reachable; old URLs land in the right place; `test_source_posters` extended for
`n=12`.

### Phase B — Enhance

§6. Old route components deleted.

Accept: on the scratch project — select a source, load a cleaned window, A/B in sync
(same drift as before), switch to a tuning window, pick a candidate, render it, start a
new clean from a dragged range; all without leaving the workspace.

### Phase C — Schema 2

§4 on both sides: `export.migrate`, model helpers, multi-track `audio_graph` (per clip
chains as today → per-track `amix` → track gain; ducked tracks mixed and
side-chained under speech, others mixed straight; `V1.audio_muted` replaces speech with
silence and thereby disables ducking), `effects` plumbing (transform itself is phase F;
`ffmpeg` effects work now), TS types, the editor adapted with no new features.

Accept: a schema-1 `project.json` opens, plays and exports identically (same plan mode,
loudness within 0.1 LU of the 0.5.0 result on the fixture); it is rewritten as schema 2
with `project.v1.json` kept; tests for migration (round trip, idempotence), two audio
tracks with one ducked, a muted track, an unknown effect type refused.

### Phase D — Edit layout

§7.1, 7.2, 7.6 and the viewer modes of 7.3: split `Editor.svelte`, media pool, track
headers (mute, solo, lock, add/remove/rename), inspector, toolbar skeleton.

Accept: add clips from the pool by drag and by `,`; a second audio track with a sound
effect placed mid-timeline plays and exports; mute affects export, solo does not;
undo/redo covers inspector edits; layout sizes survive a reload.

### Phase E — Timeline and transport

Rest of §7.3–7.5, 7.7, 7.8: timecode, shuttle, prev/next cut, snapping, blade, linked
ripple, multi-select, copy/paste, waveforms on the speech row and audio tracks, meter,
key map popover.

Accept: unit tests for `snap`, `rippleAudio`, `splitAudio`, `formatTimecode` /
`parseTimecode`, and `Program` rate maths (mocked `AudioContext`, like
`abplayer.test.ts`); in the browser: `L L` plays at 2× with picture and sound together
(drift under 40 ms), `J` runs backwards, deleting a V1 clip moves later music with it
when linked and not when unlinked.

### Phase F — Zoom and position

§8.

Accept: the preview and an exported frame of the same instant show the same framing
(compare a frame grab of the export with the viewer by eye, and by the crop maths in a
test); a 4:3 source in a 16:9 canvas keeps its side bars when zoomed.

### Phase G — Deliver

§9; remove the editor's export dialog.

Accept: each preset produces a file that `ffprobe` reports as expected (codec, size,
`hvc1` tag for HEVC, faststart, creation date kept); Lossless cut is offered exactly
when the plan says copy; queue rows show progress and Reveal.

### Phase H — Compress and versions

§10 and the version parts of §5, §6, §7.6, §11.

Accept: on the fixture — estimate, compress, version listed under its source with the
saving; a clip switched to the version exports by stream copy as HEVC; renaming the
fixture's original away leaves the source usable, flags `version: null` clips, and
"Fix all" makes the timeline exportable again; "not smaller" results are discarded with
a clear message; cancel leaves no `.partial`.

---

## 14. Testing

- **Python** (fast tier, synthetic fixture): migration; multi-track audio graph;
  transform crop; export settings → encoder args; compress argv, estimate arithmetic,
  not-smaller and duration checks (with a tiny real encode of the fixture — it is a few
  seconds long); library scan with versions and a missing original; new endpoints
  including the path guard on `/media/peaks?file=`.
- **Frontend** (Vitest): `edit.ts` (tracks, snap, ripple, split, transform), `time.ts`
  timecode, `program.ts` rate and gain routing with a mocked `AudioContext`,
  `deliver.ts` preset → settings.
- **Browser pass** per phase on the scratch project, at 1180 × 700 and at the pane's
  full size; check the console for errors.
- Do not weaken existing tests to make a phase pass; where behaviour deliberately
  changes (graph labels, project shape), update the assertion and say so in the commit.

---

## 15. Left to the implementer

Only these; everything else above is decided.

- Exact pixel sizes, icon choices and micro-layout inside panels.
- Whether `Timeline.svelte` stays DOM-based or draws waveforms on `<canvas>` per clip
  (measure with a 60-minute timeline; keep scrolling at 60 fps).
- The compress quality numbers, after the check described in §10.2.
- The size/time estimate tables in Deliver.
- If a pinned `@lucide/svelte` release does not support the installed Svelte 5, use
  inline SVG paths in `Icon.svelte` instead; no other new dependencies.
