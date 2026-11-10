# GUI design: Video Audio Cleaner desktop app

Status: **phases 1, 2 and 3 implemented** (0.5.0); phase 4 (visual edits) designed only.
See §0 for what the implementation changed. The GUI is being redesigned after 0.5.0
(`docs/gui-editor-redesign.md`); for the shell, navigation and views, that document wins. Designed 2026-11-07. This document makes
every architectural decision so that implementation can start without further
discussion. Where a choice was close, the alternative and the reason for the
decision are recorded so they are not re-litigated.

Audience: the engineer (human or model) who implements it. It assumes
familiarity with the current code: `video_audio_cleaner/cli.py` (the pipeline),
`tune.py` (autotune), `batch.py`, `provenance.py` (sidecars), `progress.py`,
`procs.py`, `ffmpeg_utils.py`, `graph.py`.

---

## 0. Implementation status and deviations

Built (0.5.0): §4 (CLI contract), §5 (server), §6 (all five views), phase 2 (native
window), phase 3 (§8 projects and source preparation, §9 export, §10 editor). Where the
implementation differs from the text below, this list wins:

- **The window is the default**: `clean-audio-gui` opens a pywebview window;
  `--browser` serves to the default browser, `--no-open` only runs the server. Native
  dialogs are `GET /api/fs/pick` (501 in a browser, where the UI falls back to the
  in-page browser over `GET /api/fs/list`). `GET /api/app` says which mode is running.
  Dropped files give real paths through pywebview's `pywebviewFullPath`.
- **Level matching is measured in the browser**, BS.1770 integrated loudness of the two
  decoded buffers (`gui/src/lib/loudness.ts`), not taken from the sidecar:
  `lufs_before_normalize` is the *denoised* audio's loudness, not the original's.
  Candidates' stored `lufs` are kept in the plan but the player measures too, so every
  comparison uses one method.
- **Waveforms for Compare/Candidates are computed in the browser** from the decoded
  buffers (`peaks.ts`). Server-side peak files (§5.4) are left for the phase-3 timeline,
  where hour-long sources make them necessary.
- **`/media/audio` prepares synchronously** (a few seconds at most for a window) and
  returns the file; no 202/Retry-After dance.
- **Picture sync** follows the *heard* audio (clock minus `AudioContext.outputLatency`);
  measured drift 8–15 ms on a 720p output, with ±10 % playback-rate nudging instead of
  seeks for drift under 0.5 s.
- **Tuning progress**: the renders inside each candidate are muted in the event stream
  (`events.muted()`), so a tuning job reports "candidate n/36" rather than inner passes.
- Extra endpoints: `POST /api/jobs/preview` (the command a request would run, for the
  form), `POST /api/reveal`, `GET /api/app`.
- **API guard**: requests must carry `Host` 127.0.0.1/localhost and `/api/*` requests the
  header `x-vac: 1` (other sites cannot send it without a CORS preflight the server never
  grants); `/media/file` serves only files inside the project or registered sources.
- **Phase 3 files**: preparation is `python -m video_audio_cleaner.prep proxy thumbs
  peaks` (job type `prep`, light lane), into `_gui/cache/{proxy,thumbs,peaks}/`; the
  proxy uses VideoToolbox with a 15-frame GOP and no B-frames; thumbnails are 72 px high
  every 2 s. Export is `clean-audio-export project.json` (`--plan`, `--reencode`), job
  type `export` in the heavy lane. Projects are `_projects/<name>/project.json`, added
  songs are copied into `_projects/<name>/music/`, deleted projects go to
  `_trash/_projects/`. Exports get a `.params.json` with `"kind": "export"` and are
  listed with their project, not as outputs.
- **Project model** as §8 with these differences: a clip's cleaned audio is
  `audio: {file, output, start_sec, gain_db}` (absolute file for export, library name for
  the player, the source time where that output starts), `null` for the source's own
  sound, `{mute: true}` for silence. Ducking is one project-level setting,
  `tracks.music_duck`, not per music clip. No separate `audio_assets` table.
- **Export loudness is a single gain** measured with `ebur128` on an audio-only pass,
  then a true-peak limiter (`alimiter`, up to 3 dB of overs; past that the gain is
  lowered). Not two-pass `loudnorm`: its integrated measurement was 0.8 LU off on a
  10 s edit with silence, and a plain gain never changes dynamics. Measured: exports
  land within 0.1 LU of the target.
- **Export clip inputs** are separate `-ss/-t` inputs per clip (input seeking), not
  `trim` on one input: trimming would decode the whole source up to each cut.
- **Editor preview**: no ducking (Web Audio cannot sidechain); crossfades are heard and
  the picture switches at the fade's midpoint; the playback engine is `gui/src/lib/
  program.ts` (two alternating proxy players, the next clip preloaded a second ahead).
  When a trim leaves a clip outside its cleaned output, the editor switches it to
  another covering output or to the original sound, and says so.
- Editor keys as §10 except: `,` adds the marked range at the end, "Insert at playhead"
  splits and inserts; `J` jumps back 5 s (browsers cannot play backwards); `L` plays at
  normal speed (no 2×/4× shuttle yet). No source-viewer waveform yet (peaks are made
  and served, `/media/peaks`, but not drawn).
- Not done: `clean-audio-batch --progress json` (the GUI queues one clean job per window
  instead), timing estimates from job history (the form uses fixed rates).

## 1. Goals and scope

### 1.1 Goals

1. **Clean speech in home videos with a GUI** instead of the three CLIs, with
   the same results (the CLI remains the engine; the GUI never re-implements
   signal processing).
2. **Compare original vs result instantly**: one picture, one key to flip the
   audio between original and cleaned, sample-synced, loudness-matched.
3. **Audition autotune's candidates**: see the 36 measured candidates of a
   window, click one, hear it against the source, and render it instead of the
   automatic pick if the ears disagree with the numbers.
4. **Run and queue jobs with live progress**, cancel them, see their logs.
5. Later (phase 3+): cut long videos into pieces, stitch pieces from several
   sources into one film, add a background soundtrack, basic visual edits,
   export one upload-ready MP4.

### 1.2 Non-goals

- Distribution to other people: this runs from the repo's venv on the owner's
  Mac. No installer, no code signing, no bundled torch.
- Real-time video effects, colour grading, multi-layer compositing,
  GPU-accelerated export.
- Cloud anything. Everything is local; the server binds to `127.0.0.1` only.

### 1.3 Output contract (unchanged by the GUI)

Every deliverable is **one MP4 file** that Google Photos accepts and YouTube
ingests without complaint:

| Property | Value |
|---|---|
| Container | MP4, `-movflags +faststart` |
| Video | H.264 High profile, `yuv420p`, source resolution and frame rate (no upscaling of VHS), `libx264 -crf 18 -preset medium` when re-encoding, stream copy when possible |
| Audio | AAC-LC, 48 kHz, source channel count (stereo for all known sources), 192 kb/s (`--audio_bitrate`) |
| Loudness | ≈ −17 LUFS integrated, true peak ≤ −1.5 dBTP (today's `--normalize` behaviour) |

Phase 1–2 outputs are exactly what `clean-audio` produces today. Phase 3's
exporter (§9) produces the same contract from an edit.

---

## 2. Decisions at a glance

| Topic | Decision | Rejected |
|---|---|---|
| UI technology | **Web UI** (HTML/CSS/TS) served by a local Python server | SwiftUI (second codebase for one user; ML stays Python anyway), PySide/Qt (A/B synced audio and a timeline are much harder) |
| Window | Phase 1: default browser tab. Phase 2: **pywebview** native window (WKWebView) for real file paths and drag-and-drop | Electron/Tauri (another runtime to maintain) |
| Backend framework | **FastAPI + uvicorn** (async, SSE, byte-range file serving, OpenAPI for free) | Flask (sync; SSE and ranges are awkward) |
| Frontend framework | **Svelte 5 + TypeScript + Vite** | Vanilla JS (the timeline's state needs a framework), React (heavier than needed) |
| Styling | Hand-written CSS with CSS variables, dark theme by default; no component library | Tailwind/MUI (build complexity, not needed) |
| Waveforms | **Own renderer on `<canvas>`** from backend-computed peak files | wavesurfer.js (decodes whole files in the browser, too heavy for hour-long 4K sources; the editor needs custom drawing anyway) |
| A/B audio playback | **Web Audio API**: two `AudioBuffer`s, gain swap; `<video>` muted for the picture, slaved to the audio clock | Two `<video>` elements (drift, no gapless swap) |
| Job execution | **Subprocess per job** running the existing CLIs with a new `--progress json` mode; **one job at a time** | In-process (torch memory never returned; a crash kills the GUI), parallel jobs (16 GB RAM, Demucs alone uses several GB) |
| Progress transport | **Server-Sent Events** (`text/event-stream`) | WebSockets (bidirectional not needed; SSE reconnects for free) |
| Persistent state | **Files in the output folder** (`.params.json`, `_tuning/*/plan.json`, new `_gui/`) — no database | SQLite (a second source of truth beside the sidecars) |
| Media serving | Local HTTP with `Range` support, never `file://` | `file://` (WKWebView restrictions, no seeking) |
| Scrubbing | **Proxies**: 540p H.264, keyframe every 0.5 s, built once per source (phase 3) | Scrubbing originals (long GOPs make seeks stutter in any editor) |
| Editor render model | **EDL (JSON) → one ffmpeg command**; stream copy when every cut is on a keyframe of the same source, re-encode otherwise | Rendering in the browser (WebCodecs export is slow and limited) |
| Export encoder | `libx264 -crf 18 -preset medium` default; `h264_videotoolbox` as a "fast export" option | HEVC (fine for Photos, but YouTube/x264 compatibility is simpler) |

---

## 3. Architecture

```
┌──────────────────────────────┐   HTTP 127.0.0.1:8787   ┌──────────────────────────────┐
│ Browser tab / pywebview      │ ◄─────────────────────► │ clean-audio-gui (FastAPI)    │
│  Svelte app (static files)   │  JSON API, SSE, media   │  - library index (sidecars)  │
│  Web Audio A/B player        │  with Range             │  - job queue (1 worker)      │
│  canvas waveform/timeline    │                         │  - media cache (_gui/cache)  │
└──────────────────────────────┘                         └──────────────┬───────────────┘
                                                                        │ subprocess, stdout = JSON lines
                                                         ┌──────────────▼───────────────┐
                                                         │ clean-audio --progress json  │
                                                         │ clean-audio-tune --progress… │
                                                         │ ffmpeg (proxy/thumbs/export) │
                                                         └──────────────────────────────┘
```

- **The CLI is the contract.** The server only shells out. Anything the GUI
  needs from the pipeline is added to the CLI first (and tested there), then
  surfaced. This keeps the CLI and GUI in lock-step and keeps the GUI testable
  with fake subprocesses.
- **The output folder is the project.** Opening the GUI on
  `~/Documents/video_processing_output` shows everything the CLI ever wrote
  there, and anything the GUI writes is visible to the CLI (`clean-audio-batch`
  can consume plans the GUI tuned, etc.).
- **Every long operation is a job** with an id, a type, a command line, a log
  file and a JSON event stream. Phase 3's proxy build and export are the same
  mechanism as phase 1's cleaning.

### 3.1 Processes and lifetime

- `clean-audio-gui [--output_dir DIR] [--port 8787] [--no-open]` starts
  uvicorn on `127.0.0.1:<port>`, then opens `http://127.0.0.1:<port>/` in the
  default browser (`webbrowser.open`). Phase 2 adds `--window` to open a
  pywebview window instead.
- Jobs are children of the server. On server shutdown (SIGINT/SIGTERM) the
  server terminates the running job via its process group (reuse
  `procs.terminate`) and marks it `cancelled` in its job file.
- If the server dies hard, the next start finds job files in state `running`
  and marks them `lost`. Partial outputs are the CLI's problem and it already
  cleans them up (`procs.register_path`).

### 3.2 Repository layout (new parts)

```
video_audio_cleaner/
  gui/                      # Python side of the GUI
    __init__.py
    app.py                  # FastAPI app factory, static mount, startup/shutdown
    main.py                 # clean-audio-gui entry point (argparse, uvicorn.run)
    library.py              # scans an output folder: sidecars, plans, sources
    jobs.py                 # JobQueue, Job dataclass, subprocess runner, SSE fan-out
    media.py                # range-aware file responses, audio extraction cache
    peaks.py                # waveform peak files (numpy)
    settings.py             # ~/Library/Application Support/video-audio-cleaner/settings.json
    static/                 # built frontend (gitignored), produced by `npm run build`
  probe.py                  # ffprobe → dict (codec, size, fps, field_order, duration, keyframe stats)
  events.py                 # the --progress json emitter shared by cli.py and tune.py
gui/                        # frontend source
  package.json  vite.config.ts  tsconfig.json  svelte.config.js
  src/
    main.ts  App.svelte
    lib/api.ts              # typed fetch wrappers, SSE client
    lib/abplayer.ts         # Web Audio A/B engine (framework-free, unit-testable)
    lib/peaks.ts            # peak file decode + canvas drawing
    lib/time.ts             # 07m00s formatting, parse (mirrors timeparse.py)
    routes/Library.svelte   # §6.1
    routes/Compare.svelte   # §6.2
    routes/Candidates.svelte# §6.3
    routes/NewRun.svelte    # §6.4
    routes/Jobs.svelte      # §6.5
    components/…
docs/gui-design.md          # this file
```

`pyproject.toml` gains an optional dependency group `gui = ["fastapi>=0.115",
"uvicorn[standard]>=0.30", "pywebview>=5"]` (pywebview only used when
`--window` is passed) and the script `clean-audio-gui =
"video_audio_cleaner.gui.main:main"`. `setup.sh` gains a step that runs
`npm ci && npm run build` in `gui/` when `node` ≥ 20 is present, and prints a
clear message otherwise (the CLIs keep working without it).

---

## 4. CLI additions (do these first)

### 4.1 `--progress json` (clean-audio, clean-audio-tune, clean-audio-batch)

A machine-readable mode. When set:

- **stdout carries only JSON lines** (one object per line, UTF-8, `\n`
  terminated, flushed immediately). Everything the console module prints today
  (`print_step/info/warning/error/success`) and every `ProgressReporter`
  update becomes an event. `tune.py`'s bare `print()` calls are routed through
  `console` so they are captured too. Third-party noise (torch, DeepFilterNet
  loguru, HF hub) already goes to stderr and stays there.
- Progress updates are throttled to at most 4 per second per reporter.
- Implementation: `video_audio_cleaner/events.py` holds a module-level
  `emitter` that is `None` (text mode) or a `JsonEmitter(stream)`. `console.*`
  and `ProgressReporter` check it. `--progress {text,json}` defaults to `text`;
  `main()` installs the emitter before anything prints. Also set
  `sys.stdout.reconfigure(line_buffering=True)`.

Event schema (`"v": 1` on every event):

```jsonc
{"v":1,"event":"start","tool":"clean-audio","argv":[…],"pid":12345,"ts":"2026-11-07T10:00:00Z"}
{"v":1,"event":"step","index":3,"total":8,"title":"Filtering noise using DeepFilterNet"}
{"v":1,"event":"progress","step":3,"prefix":"Denoising","processed_sec":12.3,"total_sec":258.0,"speed":"4.1 sec/sec","eta_sec":60}
{"v":1,"event":"message","level":"info|success|warning|error","text":"…"}
{"v":1,"event":"candidate","window":"24m45s-29m45s","index":7,"total":36,"candidate":{…Candidate asdict…}}   // tune only
{"v":1,"event":"window","window":"24m45s-29m45s","picked":{…},"runner_up":{…},"plan":"/…/plan.json"}    // tune only
{"v":1,"event":"result","output":"/abs/…mp4","sidecar":"/abs/…params.json","replaced":false,"results":{…same object as sidecar.results…}}
{"v":1,"event":"done","returncode":0,"elapsed_sec":412.7}
{"v":1,"event":"error","text":"…formatted failure…","returncode":1}  // then process exits with that code
```

Rules: exactly one `start`; `done` or `error` is always the last line (also on
KeyboardInterrupt, with `returncode` 130); `progress.total_sec` may be `null`;
`eta_sec` is `null` until known. Unknown fields must be ignored by consumers.

Tests: `tests/test_progress_json.py` runs the fast fixture pipeline with
`--progress json`, parses every stdout line as JSON, asserts the first/last
events and that no non-JSON line appears; a tune test asserts 36 `candidate`
events for the synthetic fixture.

### 4.2 `clean-audio-tune --keep_candidates`

Today each window's 36 candidate WAVs are deleted with the temp dir; only
`listen/<label>.wav` (source → best → runner-up) survives. The GUI must audition
all of them.

- With `--keep_candidates`, after a window is measured, write
  `_tuning/<stem>/candidates/<label>/source.m4a` and `cand_<i>.m4a`
  (`i` = index into `windows[].candidates`), AAC 192 kb/s via ffmpeg, same
  channel count and sample rate as the pipeline's WAVs. AAC at 192 kb/s is
  chosen deliberately: it is exactly what the final output's audio will be, so
  the audition is faithful to the deliverable, and it is ~8× smaller than
  FLAC (a 5-minute stereo window: 36 × ~7 MB ≈ 250 MB instead of ~2 GB).
- Measure each file's integrated loudness (`ebur128`, reuse
  `postprocess.parse_ebur128_summary`) and store it, so the player can
  level-match without decoding first.
- `plan.json` windows gain:
  ```jsonc
  "candidate_files": {"dir": "candidates/24m45s-29m45s", "source": {"file":"source.m4a","lufs":-25.2},
                      "candidates": [{"file":"cand_0.m4a","lufs":-17.1}, …]}
  ```
  Absent when not kept. `_reusable()` does not consider it (a plan without files
  is still a valid plan; the GUI offers "render candidates" as a job that
  re-runs tune with `--retune --keep_candidates` for that window).
- The GUI always passes `--keep_candidates`. Disk use is reported in the Library
  view with a "delete candidate audio" action per video.
- Candidate rendering must use the *same* bed level and normalisation as the
  measured candidate so what is heard is what was scored (today
  `evaluate_window` renders candidates with `normalize` and the chosen music
  level; keep that).

### 4.3 `clean-audio --from_candidate PLAN WINDOW INDEX`

Render a window with a specific candidate's settings instead of autotune's
pick: loads the plan, takes `windows[label].candidates[index]`, applies
`Candidate.flags()` (plus `--music_level_db` from the window's `music` when
`separate_music`), and records in the sidecar
`results.autotune = {"cache": plan, "picked": candidate, "chosen_by": "user",
"autotune_pick": <the plan's pick>}`. Mutually exclusive with `--autotune`.
This is what the Candidates view's "Render this one" button runs.

### 4.4 `clean-audio probe FILE` → `video_audio_cleaner/probe.py`

Not a new CLI flag, a module the server imports (and a tiny
`python -m video_audio_cleaner.probe FILE` for debugging). Returns:

```jsonc
{"path": "...", "size": 11986468682, "mtime": 1760000000.0, "duration_sec": 3961.834,
 "video": {"codec":"h264","width":4096,"height":2160,"fps":29.97,"field_order":"progressive","pix_fmt":"yuv420p","bit_rate":24063141},
 "audio": {"codec":"aac","sample_rate":48000,"channels":2},
 "keyframes": {"sampled_sec": 60, "mean_interval_sec": 5.4, "max_interval_sec": 8.3}}
```

Keyframe stats come from `ffprobe -skip_frame nokey -read_intervals %+60` on
the first minute (the existing `probe_keyframe_at_or_before` stays for exact
lookups). Measured on the real sources: `01-.mp4` is 1280×720p30 H.264 with
keyframes every 1–4 s; the 4K wedding file is 4096×2160 at 29.97 fps with
keyframes every 5–8 s. Both are progressive, so no deinterlacing is needed for
the current material; `field_order != progressive` enables a `bwdif` step in
phase 3's export.

### 4.5 Source identity

`source_id = sha1(f"{abs_path}|{size}|{int(mtime)}")[:12]`. Hashing 12 GB of
content is too slow for a GUI; path+size+mtime is stable enough and a moved
file simply becomes a new source (its outputs still resolve via the sidecar's
absolute `input_video`; the Library view marks sources whose file is missing).

---

## 5. Server (FastAPI)

### 5.1 Library model (`gui/library.py`)

Built by scanning the output folder (no recursion except `_tuning/`), cached in
memory, refreshed on demand (`POST /api/library/rescan`) and after every job.

```ts
type Library = {
  output_dir: string;
  sources: Source[];          // one per distinct sidecar.parameters.input_video
};
type Source = {
  id: string;                 // §4.5
  path: string; name: string; exists: boolean;
  probe?: Probe;              // lazily filled (ffprobe is ~100 ms; done once, cached in _gui/cache/probe/<id>.json)
  outputs: Output[];
  plans: Plan[];              // _tuning/<stem>/plan.json and _tuning/<stem>-with-music/plan.json
};
type Output = {
  file: string;               // basename of the mp4 (or wav for --filter_audio_only)
  sidecar: string;
  created: string;
  window: { requested_start_sec: number; requested_duration_sec: number|null;
            actual_start_sec: number; actual_duration_sec: number|null };
  label: string;              // "24m45s-29m45s" derived from the name or window
  tag: string|null;           // from the name (after the window, before the extension)
  params: Record<string, unknown>;   // sidecar.parameters
  results: Record<string, unknown>;  // sidecar.results (autotune pick, music_bed, final_lufs, lufs_before_normalize …)
  size: number;
  kind: "video" | "audio" | "compare";
};
type Plan = {
  path: string; separate_music: boolean; source_kind: "tape"|"digital"; method: number;
  windows: PlanWindow[];      // label, start_sec, end_sec, picked, runner_up, candidates[], music, candidate_files?
};
```

Outputs are grouped under their source by `parameters.input_video`. Files
without a sidecar (pre-0.3 outputs, foreign files) are listed under a pseudo
source "Unknown" with only name/size.

### 5.2 Jobs (`gui/jobs.py`)

```ts
type Job = {
  id: string;                 // ULID-ish: time-sortable, e.g. "20261107T100000-3f2a"
  type: "clean" | "tune" | "candidates" | "proxy" | "thumbs" | "peaks" | "export";
  title: string;              // "Clean 02-חתונה 24m45s–29m45s (autotune, tape)"
  argv: string[];             // exactly what runs; shown in the UI and copyable
  cwd: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled" | "lost";
  created: string; started?: string; finished?: string;
  returncode?: number;
  step?: { index: number; total: number; title: string };
  progress?: { processed_sec: number; total_sec: number|null; speed?: string; eta_sec: number|null };
  result?: unknown;           // the `result` event payload
  error?: string;
  log: string;                // _gui/jobs/<id>.log (stderr + raw stdout)
};
```

- Persistence: `<output_dir>/_gui/jobs/<id>.json` rewritten on every state
  change (atomic: write `.partial`, `os.replace`), plus `<id>.log`.
- **Queue**: FIFO, **one worker**. Rationale: Demucs + DeepFilterNet use
  several GB and all performance cores; two at once is slower than sequential
  on a 16 GB M3 Air. `proxy`/`thumbs`/`peaks` jobs are ffmpeg/numpy only and
  are allowed to run **concurrently with one ML job** (a second worker that
  only takes non-ML types). Max two processes total.
- Runner: `asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=log_file,
  start_new_session=True, env={…, "PYTHONUNBUFFERED": "1"})`. Reads stdout
  line by line, parses JSON, updates the Job, appends the raw line to the log,
  and publishes to subscribers. For ffmpeg-type jobs (proxy/thumbs/export) the
  runner passes `-progress pipe:1 -nostats` and translates `out_time_us=` lines
  into `progress` events itself (reuse the parsing in `ffmpeg_utils.run_ffmpeg`
  by extracting a small pure function `parse_progress_line`).
- Cancel: `os.killpg(pgid, SIGINT)`, wait 5 s, then `SIGTERM`, then `SIGKILL`
  (same ladder as `procs.terminate`, with SIGINT first so the CLI's own cleanup
  runs). State → `cancelled`.
- The command line of a `clean` job always includes `--progress json` and, when
  autotuning, `--source tape|digital` (the GUI never lets the CLI prompt).
  `clean-audio` and `clean-audio-tune` are run as `sys.executable -m
  video_audio_cleaner.cli` / `.tune` so the server's own venv is used.

### 5.3 Media (`gui/media.py`)

- `GET /media/file?path=<abs>` serves any file **inside the output folder or
  inside a registered source's directory** with `Range` support (FastAPI's
  `FileResponse` handles ranges in Starlette ≥ 0.38; verify, else implement
  206 by hand). Anything else → 403. This is the only path-based endpoint.
- **Audio windows for the A/B player** (`GET /media/audio?...`) are prepared
  files, not on-the-fly streams, because `decodeAudioData` needs a complete
  file:
  - `kind=output&file=<name>`: `ffmpeg -i out.mp4 -vn -c:a copy out.m4a`
    (instant, stream copy of the output's AAC).
  - `kind=original&source=<id>&start=<actual_start_sec>&duration=<sec>`:
    `ffmpeg -ss S -t D -i src -vn -c:a aac -b:a 256k -ar 48000 x.m4a`. For a
    5-minute window this is ~2 s.
  - `kind=candidate&plan=…&window=…&index=…`: the kept `cand_<i>.m4a`.
  - Cached under `_gui/cache/audio/<sha1 of the request>.m4a`; the response
    `Location`s the browser to the `/media/file` URL once ready. Preparation
    runs as a tiny inline ffmpeg call (not a queued job: < 5 s, and the Compare
    view needs it now). A `preparing` state is returned as 202 with
    `Retry-After: 1` so the UI can show "preparing audio…".
- **Why AAC, not WAV, for the browser**: `decodeAudioData` accepts AAC-in-MP4 in
  both Safari (WKWebView) and Chrome; a 5-minute stereo WAV is 57 MB vs 7 MB,
  and the original's quality is not the question in an A/B (it is the
  reference, and 256 kb/s AAC is transparent for VHS audio).
- `GET /media/peaks?source=<id>` and `?output=<name>` return the peak file
  (§5.4), 404 until a `peaks` job has produced it (the UI enqueues it).

### 5.4 Peaks (`gui/peaks.py`)

Waveform overviews for the compare timeline and the editor. For a file:
`ffmpeg -i f -vn -ac 1 -ar 8000 -f s16le -` piped into numpy, reduced to
**100 columns per second** (10 ms bins) of `(min, max)` as `int8` (scaled to
±127). One hour = 720 000 bytes. Written to
`_gui/cache/peaks/<source_id or output name>.peaks` with a 16-byte header
(`magic "VACP", version u8, columns_per_sec u16, n_columns u32, reserved`).
Frontend draws any zoom level from this by bucketing columns; at zooms finer
than 10 ms it draws steps (good enough: the editor never needs sample-level
zoom).

### 5.5 Settings (`gui/settings.py`)

`~/Library/Application Support/video-audio-cleaner/settings.json`:
`{"last_output_dir": "...", "recent_output_dirs": [...], "recent_sources": [...],
 "default_source_kind": "tape", "window_bounds": {...}}`. Nothing else is
global; everything per-project lives in the output folder.

### 5.6 API

All JSON unless noted. Errors: `{"error": "message"}` with 4xx/5xx.

```
GET  /api/library                         → Library (cached)
POST /api/library/rescan                  → Library (rebuilt)
POST /api/library/open   {output_dir}     → switch project (server-side state), returns Library
GET  /api/sources/{id}/probe              → Probe (runs ffprobe once, cached)
POST /api/sources        {path}           → register a source file not yet in the library (for New Run); returns Source
GET  /api/fs/pick?kind=file|dir           → phase 2 (pywebview): native dialog, returns {path}; phase 1: 501 and the UI shows a text field
GET  /api/fs/list?dir=…                   → phase 1 fallback folder browser: [{name, path, is_dir, size}] (home dir and below only)

GET  /api/jobs                            → Job[] (newest first, last 200)
GET  /api/jobs/{id}                       → Job
GET  /api/jobs/{id}/log                   → text/plain
POST /api/jobs           JobRequest       → Job (queued).  JobRequest is one of:
      {type:"clean", source_id, start_sec, duration_sec|null, autotune:true, source_kind:"tape"|"digital",
       separate_music:bool, tag?:string, output?:string, overrides?:{[cli_flag]: value}}
      {type:"clean_candidate", plan_path, window_label, index, tag?}      → runs clean-audio --from_candidate
      {type:"tune", source_id, windows:[{start_sec,end_sec}], separate_music, source_kind, keep_candidates:true}
      {type:"proxy"|"thumbs"|"peaks", source_id}                           (peaks also accepts {output:name})
      {type:"export", project_path}                                        (phase 3)
DELETE /api/jobs/{id}                     → cancel (running) or remove from queue (queued)
POST /api/jobs/{id}/retry                 → new job with the same argv
GET  /api/events                          → SSE: every job state change and progress event, as
                                             `event: job\ndata: <Job JSON>\n\n`; a `ping` every 15 s

GET  /media/file?path=…                   → bytes with Range (§5.3)
GET  /media/audio?…                       → 302 → /media/file, or 202 while preparing
GET  /media/peaks?…                       → application/octet-stream
GET  /media/thumb?source=…&t=…            → JPEG (phase 3)

POST /api/outputs/{name}/delete           → moves the mp4 + sidecar to <output_dir>/_trash/ (never unlink); UI confirms first
POST /api/plans/candidates/delete {plan_path, window} → deletes kept candidate audio for a window (disk-use control)
```

The `clean` request is translated to argv by a single pure function
`gui.jobs.clean_argv(req, library) -> list[str]` which is unit-tested against
the CLI parser (`cli.build_parser().parse_args(argv[1:])` must not raise), so
the GUI can never produce an invalid command. It always emits
`--output_dir <project> --progress json`, `--skip_first_n_sec <start>`,
`--process_n_seconds <duration>` when set, `--autotune --source <kind>`,
`--separate_music` when asked, `--tag` when given, and copies `overrides`
verbatim (the Advanced panel in §6.4).

---

## 6. Frontend

### 6.1 Library view (home)

Left: project folder picker (recent folders dropdown + "Open…"). Main: one
card per source, sorted by name:

- Header: source name, path (greyed, middle-ellipsised), probe summary
  ("1280×720 30 fps H.264 · 1:02:18 · 1.0 GB · tape"), a **missing-file**
  badge when `exists` is false.
- A horizontal **coverage bar** of the source's duration showing each output's
  window as a block (label on hover); overlapping outputs stack. Clicking a
  block opens Compare for that output. This answers "which parts of 02- have I
  cleaned?" at a glance.
- Table of outputs: window label, tag, created, pick summary from
  `results.autotune.picked` (e.g. "atten 10 · hiss off · no declick"), music
  bed (`results.music_bed` → "−15 dB"), final LUFS, size, buttons **Compare**,
  **Candidates** (enabled when a plan has that window), **Re-run…** (opens New
  Run prefilled from the sidecar), **Reveal in Finder** (`open -R`, server
  side), **Delete** (to `_trash/`).
- Buttons: **New run** (→ §6.4 prefilled with this source), **Tune windows…**.
- "Unknown" group at the bottom for sidecar-less files.

### 6.2 Compare view (the core feature)

Layout: video on top (the cleaned output's `<video>`, muted, `playsinline`),
below it a transport bar, below that the waveform strip (original on top,
cleaned below, both from peaks, playhead line), right side a settings panel.

**Audio engine (`lib/abplayer.ts`)**, framework-free class:

```ts
class ABPlayer {
  constructor(ctx: AudioContext, video: HTMLVideoElement)
  async load(a: ArrayBuffer, b: ArrayBuffer, opts: {offsetA: number; gainA_dB: number; gainB_dB: number})
  play(); pause(); seek(t: number); setActive("A"|"B"); toggle();
  setLoop(inSec: number|null, outSec: number|null)
  get time(): number        // ctx-derived, authoritative
  on(event: "time"|"state"|"active", cb)
}
```

- Decode both buffers with `ctx.decodeAudioData`. Create two
  `AudioBufferSourceNode`s on `play()`, both started at the same
  `ctx.currentTime + 0.05` with the same buffer offset, each through its own
  `GainNode`; the inactive one has gain 0. Switching = exponential ramp over
  **8 ms** between the two gains (click-free, perceptually instant). Both
  sources keep running so the switch is sample-aligned; no re-seeking.
- `offsetA`: the original's window starts at `sidecar.window.actual_start_sec`;
  the audio endpoint already extracts exactly that window, so both buffers
  start at the same instant and `offsetA` is 0 for outputs. (Kept in the API
  for the editor, where clips have independent offsets.)
- **Level matching** (on by default, toggle in the panel): the cleaned file is
  at `results.final_lufs` (≈ −17); the original at
  `results.lufs_before_normalize` (≈ −25). Apply `gainA_dB = final_lufs −
  lufs_before_normalize` to the original, clamped so the original's decoded
  peak × gain ≤ −1 dBFS (compute the peak from the buffer once). When the
  sidecar lacks these numbers (old files), compute RMS of both buffers in JS
  and match RMS. The panel shows the applied gain ("original +8.2 dB").
- **Video sync**: Web Audio is the master clock. On `play()` set
  `video.currentTime = t` and `video.play()`; every animation frame, if
  `|video.currentTime − player.time| > 0.06` set `video.currentTime`. On
  `seek()`: stop sources, set `video.currentTime`, recreate sources at the new
  offset when playing. Scrubbing the waveform while paused shows the frame
  (`video.currentTime = t`; the output's keyframes are the source's, so a seek
  may take ~100 ms on the 4K source; acceptable, and phase 3's proxies fix it).
- **Loop**: `I`/`O` set loop points at the playhead; sources are created with
  `loop = true, loopStart, loopEnd`, both identically, so looping stays aligned.

**Keyboard** (global on the view): `Space` play/pause · `A` original ·
`B` cleaned · `X` or `Tab` toggle · `I`/`O` loop in/out · `L` loop on/off ·
`←/→` ±1 s · `Shift+←/→` ±10 s · `,`/`.` ±1 frame while paused · `M` level
match on/off · `0` go to start. Hold-to-compare: holding `A` plays the
original while held and returns to cleaned on release (implemented with
keydown/keyup; `keydown` repeat events ignored).

**Panel**: active side indicator (big "ORIGINAL"/"CLEANED" label that also
flashes on switch), level-match toggle with applied gain, output's pick and
scores (fidelity, warble, speech→hiss, clicks for source and pick), the exact
`reproduce` command (copy button), links: "Candidates for this window", "Re-run
with changes…", "Reveal in Finder".

**Compare any two**: the B side defaults to the output; a dropdown lets B be
another output of the same source whose window overlaps (e.g. `_no-music` vs
`_v2`), aligned by `actual_start_sec`. A side can also be a candidate (§6.3).

### 6.3 Candidates view

For one plan window. Top: the same player as §6.2 with A = `source.m4a`
(the window's extracted source as tune heard it) and B = the selected
candidate. Below: a sortable table of the 36 candidates — columns atten, hiss,
declick, (music level when separated), fidelity, speech→hiss gain, warble,
clicks, score, qualifies (✓/✗ with the reason: fidelity below bar / warble
above bar), and markers for **autotune pick ★** and **runner-up ☆**. The
source's own row is pinned at top. Clicking a row loads it as B (decoded
buffers are cached per candidate; switching candidates re-creates the B source
at the current time so the comparison continues uninterrupted). A small
**scatter plot** (x = warble, y = hiss gain, point size = clicks, colour =
atten, qualifying points filled, others hollow; the pick outlined) gives the
shape of the trade-off; clicking a point selects that candidate.

Level matching here uses each file's stored `lufs` (§4.2): play everything at
the source's loudness, i.e. attenuate candidates by `cand.lufs − source.lufs`,
so louder is never mistaken for better.

Buttons: **Render this candidate** (→ `clean_candidate` job with an optional
tag, default tag `cand<i>`), **Make it the pick** (rewrites `picked` in
`plan.json` with `"chosen_by": "user"`, so `clean-audio-batch` and later
autotune reuse honour it; `tune.py` must treat a user-chosen pick as sticky on
rescoring — add that rule), **Delete candidate audio**.

If `candidate_files` is absent: an empty state with "Render candidates for
this window" (→ `tune` job with `--retune --keep_candidates` for just that
window).

### 6.4 New run view

A form that mirrors `clean-audio`, in three tiers:

1. **Always visible**: source (dropdown of known sources + "Add file…"), start
   and duration (text fields accepting `24m45s`, `1h05m`, `1485`, validated by a
   JS port of `timeparse.py`; a slider over the source's duration; "to end"
   checkbox), **Source kind** (radio: tape / digital, required; default from
   settings; prefilled from the source's last sidecar), **Background music**
   toggle (→ `--separate_music`, help text: autotune then chooses the bed
   level), **Tag** (validated like `_validate_tag`), **Autotune** (on by
   default). Below the form, the **command line** that will run, live-updated,
   with a copy button — the GUI must never do something the CLI cannot.
2. **Advanced** (collapsed): every other parser option, generated from the
   parser itself: add `GET /api/cli/schema` which introspects
   `cli.build_parser()` and returns `[{dest, flags, type, default, choices,
   help, group}]`; the form renders it generically. Values differing from the
   default go into `overrides`. When autotune is on, options in
   `AUTOTUNED_OPTIONS` are shown disabled with the note "chosen by autotune".
3. **Output**: computed name preview using the same rules as
   `derive_output_name` (ask the server: `GET /api/outputs/preview-name?…`
   rather than porting the regex), warning when it will replace an existing
   file, estimated time (`duration × 0.8 s/s` for autotune + `duration × 0.3`
   for the render; refine from measured job history: the server keeps
   `elapsed/duration` per job type and reports the median).

**Queue** button adds the job and navigates to Jobs; **Queue and open
another** keeps the form. A "batch" affordance: the window list can hold
several rows (add row), producing one job per row with shared settings — this
replaces `redo_all_v2.sh`.

### 6.5 Jobs view

List of jobs newest first with state chips, step title, progress bar, ETA,
elapsed, and a **Cancel** button on the running/queued ones. Expanding a job
shows its argv (copyable), the live log tail (last 200 lines, auto-follow,
SSE-updated), and on completion links: **Compare**, **Reveal**. Failed jobs
show the `error` text prominently and a **Retry** button. A persistent
mini-status in the app header shows the running job's title and percent from
any view.

### 6.6 App shell

Header: project folder name (click → switch), nav (Library · New run · Jobs),
running-job mini status, a **tape/digital** badge is *not* global (it is per
source). Routing: hash-based (`#/compare/<output>`), no router library.
Theme: dark default, respects `prefers-color-scheme`. Minimum window width
1024 px; no mobile layout.

---

## 7. Phase plan and acceptance criteria

### Phase 1 — Clean, compare, audition (the current tool with a face)

CLI (§4.1–4.5), server (§5), views §6.1, §6.2, §6.3 (A/B against the kept
candidates), §6.4, §6.5. Browser tab, text-field path entry with the `/api/fs/list`
folder browser.

Done when:
- Opening `clean-audio-gui --output_dir ~/Documents/video_processing_output`
  lists every existing output grouped by source with correct windows and picks
  (including the pre-tag `_no-music` files and files without sidecars).
- Compare on a 5-minute output: audio ready in < 5 s, switching A/B has no
  audible gap or click, the picture never drifts more than 60 ms (measured by
  logging `video.currentTime − player.time`), level matching changes the
  original by the sidecar's gain.
- Queuing an autotune run from the form yields a file identical in parameters
  to the equivalent CLI run (compare sidecars), with live progress and a
  working cancel that leaves no `.partial` files.
- A tune run with `--keep_candidates` produces audible candidates; selecting a
  non-pick candidate and rendering it produces a file whose sidecar says
  `chosen_by: user`.
- `./venv/bin/pytest` passes, including new tests for `events.py`,
  `clean_argv`, library scanning on a fixture folder, and the job runner with a
  fake script that emits events.

### Phase 2 — Native window

`clean-audio-gui --window`: pywebview (`webview.create_window(url=…,
width=1400, height=900)`), native open dialogs via
`window.create_file_dialog` exposed as `/api/fs/pick`, drag-and-drop of files
onto the window (pywebview's `DOMContentLoaded`/drop bridge gives real paths),
"Reveal in Finder", window bounds persisted. Make `--window` the default once
it works.

### Phase 3 — Editor (cut, stitch, soundtrack, export)

See §8–§9. Done when a project mixing a VHS clip and a 4K clip with a music
bed exports to one MP4 that uploads to Google Photos and YouTube and whose
speech is the cleaned audio from phase 1.

### Phase 4 — Visual edits (not designed here)

Crop/rotate, fades, titles, stabilisation. All are ffmpeg filters on clips;
they slot into the EDL (§8) as per-clip `video_filters: string[]`.

---

## 8. Editor data model (phase 3)

A **project** is a folder `<output_dir>/_projects/<name>/` containing
`project.json`, written atomically, versioned with `"schema": 1`, with undo
implemented in the frontend (in-memory history of project states, 200 levels)
and saved on every change (debounced 1 s).

```jsonc
{
  "schema": 1,
  "name": "Wedding 1994 + 2026",
  "canvas": { "width": 1920, "height": 1080, "fps": 29.97, "sample_rate": 48000, "channels": 2 },
  "sources": {
    "a1b2c3d4e5f6": { "path": "/…/02-חתונה.mp4", "probe": {…}, "proxy": "_gui/cache/proxy/a1b2c3d4e5f6.mp4",
                      "thumbs": "_gui/cache/thumbs/a1b2c3d4e5f6/", "peaks": "_gui/cache/peaks/a1b2c3d4e5f6.peaks" }
  },
  "audio_assets": {
    "02-חתונה_24m45s-29m45s_v2": { "source": "a1b2c3d4e5f6", "file": "/…/02-חתונה_24m45s-29m45s_v2.mp4",
                                   "start_sec": 1485.0, "duration_sec": 300.0, "lufs": -17.0 }
  },
  "tracks": {
    "video": [                                 // one video track; clips are sequential, no gaps (gaps = black clip)
      { "id": "c1", "source": "a1b2c3d4e5f6", "in_sec": 1490.0, "out_sec": 1612.5,
        "audio": { "asset": "02-חתונה_24m45s-29m45s_v2", "gain_db": 0 },   // or {"asset": null} = source audio, or "mute"
        "video_filters": [], "transition_in": { "type": "crossfade", "sec": 0.5 } }
    ],
    "music": [                                 // soundtrack clips, positioned on the timeline
      { "id": "m1", "file": "/…/song.mp3", "at_sec": 0.0, "in_sec": 0.0, "out_sec": 240.0,
        "gain_db": -18, "duck": { "enabled": true, "threshold_db": -35, "ratio": 6, "attack_ms": 20, "release_ms": 600 },
        "fade_in_sec": 2, "fade_out_sec": 4 }
    ]
  },
  "export": { "profile": "upload", "encoder": "libx264", "crf": 18, "preset": "medium",
              "audio_bitrate": "192k", "target_lufs": -16, "output": "/…/Wedding 1994 + 2026.mp4" }
}
```

Decisions baked in:

- **Canvas**: defaults to the first clip's resolution and fps when the project
  is created; the user can change it in Project settings. Clips with other
  sizes are scaled to fit and letterboxed/pillarboxed (`scale=…:force_original_aspect_ratio=decrease,pad=…`),
  fps converted with `fps=` (no motion interpolation). Mixed 720p VHS + 4K:
  the sane default is 1080p (set by the user; the UI suggests 1080p when
  sources differ and the largest is > 1080p, because 4K of upscaled VHS is
  wasted bits).
- **Clip audio** references a cleaned *asset* (a phase-1 output) when one
  covers the clip's range; otherwise the source's own audio. The timeline
  shows an uncovered clip with a warning icon and a one-click "Clean this
  range" that queues a `clean` job and attaches the result when done. A clip
  partially covered by an asset is split at the asset boundary by that action.
- **Time units**: seconds as floats in the model; the UI snaps cuts to frame
  boundaries of the canvas fps. Clips store source times (`in/out`), not
  timeline positions; the timeline position is the sum of previous clips
  (single video track, no gaps). This keeps ripple edits trivial.
- **Music ducking**: ffmpeg `sidechaincompress` keyed by the speech mix. The
  default (threshold −35 dB, ratio 6, attack 20 ms, release 600 ms) is a
  starting point to tune by ear in the editor's preview; the Web Audio preview
  uses `DynamicsCompressorNode` with the same parameters (close enough for
  preview; the export is authoritative).

### 8.1 Source preparation jobs

Run once per source when it is added to a project (and reusable across
projects; stored under `_gui/cache/`):

- **proxy**: `ffmpeg -i src -map 0:v:0 -map 0:a:0? -vf "scale=-2:540" -c:v
  h264_videotoolbox -b:v 2500k -g 15 -bf 0 -c:a aac -b:a 128k -movflags
  +faststart proxy.mp4`. Keyframe every 15 frames (0.5 s) with no B-frames, so
  any seek decodes ≤ 15 small frames: scrubbing and frame-stepping are
  instantaneous in the browser. ~1.1 GB per hour; VideoToolbox encodes the 4K
  source at roughly 4–6× real time on an M3. For sources already ≤ 540p, still
  re-encode (the short GOP is the point).
- **thumbs**: one JPEG every 2 s at 96 px height, `fps=1/2,scale=-2:96`, named
  `t<index>.jpg`, for the timeline's film strip; ~1 800 files/hour, ~5 KB each.
- **peaks**: §5.4.

The editor uses proxies for everything shown on screen and originals only in
export. Phase 1's Compare view may also use the proxy for the picture when one
exists (it then needs `actual_start_sec` to align the proxy time).

---

## 9. Export (phase 3)

`POST /api/jobs {type:"export", project_path}` runs a new module
`video_audio_cleaner/render_edl.py` (also exposed as `clean-audio-export
project.json --progress json`) that compiles the project into **one ffmpeg
command**:

1. **Decide copy vs encode.** A video track can be stream-copied iff: a single
   source, every clip's `in_sec` lies on a keyframe of that source (checked
   with `ffprobe -skip_frame nokey` around each cut), no filters, no
   transitions, canvas == source. Then the video is produced with the concat
   demuxer (`-f concat -c:v copy`) exactly like today's `_merge_video`, and
   only audio is encoded. Otherwise, re-encode: per clip
   `[0:v]trim=in:out,setpts=PTS-STARTPTS,<filters>,scale/pad,fps[vN]`, joined
   with `concat=n=N:v=1:a=0`, crossfades via `xfade` where requested.
   The UI shows "lossless" or "re-encode" next to the export button and why.
2. **Audio graph.** Per clip: `amovie`/input of the chosen asset or source,
   `atrim`, `asetpts`, `volume`, concatenated with `acrossfade` at transitions
   → `[speech]`. Music clips: `atrim`, `adelay=at_sec`, `volume`, `afade`
   in/out, summed → `[music]`; `[music][speech]sidechaincompress=…[ducked]`;
   `[speech][ducked]amix=inputs=2:normalize=0` → `[mix]`; then the existing
   two-pass `loudnorm` to `export.target_lufs` (reuse `graph.loudnorm_filter`)
   and `alimiter`.
3. **Encode.** `-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -profile:v
   high -c:a aac -b:a 192k -ar 48000 -movflags +faststart`. Option "fast":
   `-c:v h264_videotoolbox -b:v <bitrate from a table by resolution: 720p 8M,
   1080p 16M, 2160p 45M>`. HEVC is not offered (YouTube re-encodes anyway;
   H.264 is universally accepted).
4. Write `<output>.params.json` beside the export with the project snapshot
   embedded (`"project": {…}`) and the ffmpeg command — the same provenance
   discipline as today.

Progress: ffmpeg's `-progress pipe:1` translated to events; the `done` event
carries the output path and its measured `ebur128` summary.

---

## 10. Timeline UI (phase 3) — key behaviours to implement

- Single video track with film-strip thumbnails and the clip's waveform
  (speech asset's peaks when attached, else source peaks); one music track
  below; a ruler on top; zoom with `⌘`+wheel, pan with wheel/trackpad; the
  playhead is a draggable line; current time readout as `h:mm:ss.ff`.
- Transport: `Space` play/pause, `J`/`K`/`L` shuttle (L repeated = 2×, 4×;
  implemented by `video.playbackRate` on the proxy with audio muted above 2×),
  `←/→` one frame, `Shift` ±1 s, `Home/End`.
- Editing: `S` split at playhead (razor), `Delete` removes the clip (ripple),
  drag clip edges to trim (snap to other edges and the playhead), drag clips
  to reorder, `⌘Z`/`⇧⌘Z` undo/redo, `I`/`O` mark a range on the source viewer
  and `,` inserts it as a clip.
- **Source viewer / program viewer**: two players (left: a source's proxy to
  pick ranges; right: the program = the timeline played back by switching
  the `<video>` between proxies at clip boundaries, with audio from Web Audio
  scheduled per clip, `AudioBufferSourceNode.start(when, offset, duration)`
  for each clip in the next 10 s, rescheduled on edits). Transitions are not
  previewed (hard cut in preview; `xfade` only in export) — stated in the UI.
- Export dialog: output name, profile (quality/fast), canvas summary, lossless
  or re-encode indicator, estimated size and time.

---

## 11. Testing strategy

- **Python**: pytest as today. `events.py` unit tests; `--progress json` e2e on
  the synthetic fixture (fast tier); `clean_argv` round-trips through the real
  parser; `library.py` on a fixture output folder built from real sidecars
  copied into `tests/fixtures/library/` (with the mp4s replaced by empty files);
  `jobs.py` with a fake tool `tests/fake_tool.py` that emits a scripted event
  stream and honours SIGINT (asserts cancel works and the job file ends in
  `cancelled`); `render_edl.py` compiles fixture projects to argv and the
  argv is run on the fixture clips (slow tier). FastAPI endpoints via
  `httpx.AsyncClient`.
- **Frontend**: Vitest for `abplayer.ts` (with a mocked `AudioContext`:
  asserts both sources start at the same time, gain ramps, loop points),
  `time.ts` (parity table generated from `timeparse.py`), `peaks.ts`. No
  browser automation initially; the Compare view's sync is verified by the
  drift logger in dev mode.
- **Manual acceptance** per phase in §7, performed on the real sources.

---

## 12. Development workflow

- `./setup.sh` installs `.[gui]` and builds the frontend when node is available.
- Dev: `./venv/bin/clean-audio-gui --no-open --output_dir <dir>` (API on 8787)
  and `cd gui && npm run dev` (Vite on 5173 proxying `/api` and `/media` to
  8787). Production: `npm run build` → `video_audio_cleaner/gui/static/`,
  served by FastAPI at `/` with SPA fallback.
- `.gitignore` gains `video_audio_cleaner/gui/static/` and `gui/node_modules/`.
- Version bump to 0.5.0 for phase 1; CHANGELOG entries per phase.

---

## 13. Deliberately open

Only these are left to the implementer's judgment; everything else above is
decided:

- Exact CSS/visual design (keep it plain, dark, dense; this is a tool).
- Whether `FileResponse` range handling suffices or a custom 206 responder is
  needed (depends on the Starlette version installed).
- Thumbnail density and peak resolution may be tuned after measuring on the
  hour-long sources.
