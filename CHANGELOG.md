# Changelog

## Unreleased

**Added**

- GUI: **Deliver is where exports start.** Pick a project, then *Keep the original
  quality* or *Fit a size* (GB per hour or in total, HEVC or H.264, a smaller picture
  height, the hardware encoder). *Estimate* tries short samples and shows the predicted
  size, the encode time, the quality in words and the weakest moment, and a smaller height
  when it would look better; changing a setting marks the estimate stale. Missing files are
  listed and block the export. The editor's *Export…* opens the same settings in a dialog.
  Server: job type `export_estimate`, `GET /api/projects/{name}/estimate` (`{estimate,
  stale}`), and the plan endpoint lists `missing` files.
- Export to a size budget (`video_audio_cleaner/sizing.py`, plan §10):
  `clean-audio-export PROJECT --budget 2GB/h` (or `--size 2.5GB`) tries short samples of the
  timeline (lossless references through the export's own picture graph, encoded at top
  quality, then a CRF search), scores them with VMAF (SSIM when ffmpeg lacks libvmaf) and
  writes `estimate.json` beside the project; the export then encodes once: a lossless copy
  when that fits, one pass at top quality, or two passes at the budget's bitrate. HEVC
  (`hvc1`) by default under a budget; `--codec`, `--height`, `--fast`, `--one_pass`,
  `--estimate`; project keys `export.budget/codec/height/fast`. An estimate takes well under a
  minute (a few 2–3 s samples, two rounds at most, scored at ≤ 1080 lines). Suggests a smaller frame when
  a 4K picture is starved. Without a budget the export is unchanged (plus `--codec hevc` and
  `--height`).

**Changed**

- GUI: the browser tab has an icon (a play button with sound waves, blue to green; `gui/public/favicon.svg`).
- GUI: the app is called **Video Master** (browser tab, app window, server messages). The
  Python package and the command names are unchanged.
- GUI: **projects.** What the GUI called a timeline is now a *project*: a set of videos
  and the one video stitched from them (still `_projects/<name>/project.json`). The output
  folder is called the *library folder*. In Media, the left column lists the projects
  (each shows its own videos); selected videos go into a *New project from these…* or
  *Add to project ▾* (into its list of videos, nothing on its timeline yet); a cleaned
  piece's *Add to a project ▾* puts that piece on the project's timeline. Edit opens on
  the list of projects. The library's project summaries carry `videos` (source ids, in
  the project's order).
  Videos can also be dragged from the grid onto a project in the left column (the whole
  selection when the dragged video is part of it), or onto *New project…*; the projects
  there are listed by name so the list stays put while dropping.
  While Media shows a project, *Add videos to this project…*, *Add a folder…* and
  dropping files add the videos to that project as well as to the library.
- Zoom into a clip: the clip's *Zoom…* in Edit draws the part of the picture it shows as a
  rectangle on the original (drag to move, a corner or the scroll wheel to zoom, a slider to
  check it across the clip), shaped like the resulting video. The preview shows it; the
  export crops that part (`clip.frame`, fractions of the square-pixel picture) and scales it
  up with Lanczos, so zoomed projects are re-encoded. It warns when few lines of the original
  are stretched to the output.
- GUI: Edit's "Program" viewer is now *Resulting video*, shaped like the export.
- Music separation keeps only the music: Demucs puts the room, crowd, wind and hiss into
  its music stem too, so mixing that stem back whole returned all the background noise
  (a ceremony kept at 0 dB lost its noise reduction entirely). The stem is now kept at full
  level only where it is tonal (music) and turned down 20 dB elsewhere, with a hold and
  fades (`musicgate.py`). On that ceremony: background in pauses −12.9 dB against the
  original, the band at the end kept. `--no_music_gate` mixes the whole stem back. Tunings
  with music now live in `_tuning/<video>-with-music-gated` (older `-with-music` ones are
  not reused).
- GUI: background music stays unless you tick **Remove background music** in the clean form
  (the old "Background music" box is gone). By default the voices are separated from the
  music (Demucs), cleaned on their own, and the music goes back at its own level
  (`--separate_music --music_level_db=0`; tuning gets `--music_levels=0`), so autotune no
  longer chooses to drop it. Before, music separation was off unless asked for, and the
  speech denoiser then turned music down along with the noise. API: `remove_music` on clean
  and tune jobs (older callers sending only `separate_music` work as before).
- GUI: each clip on the Resulting video timeline shows which sound it plays, as a badge:
  the cleaned version's name and settings ("cand2 · candidate 2 · atten 30 · hiss off"), or
  an orange "original sound" warning, or "muted". Clicking the badge switches the clip to
  any cleaned version that covers it, back to the original, or muted, and opens its tuned
  candidates (render another and the clip switches to it) or a new clean. Clips waiting to
  switch to a clean they asked for now keep waiting across a page reload or server restart. The badge
  and the clip's name stay in view while the timeline scrolls, as long as part of the clip shows.
- GUI: the Resulting video's header shows the clip under the playhead: "Clip 4 · 0:26.0 /
  0:58.1 · GX010033… @ 9:10.1" (time into the clip, its length, and where that is in its
  video), beside the time in the whole edit.
- GUI: **Match clip N's zoom** in the clip panel gives the selected clip the previous
  clip's framing (the same zoom on the same point of the picture, carried over if the two
  videos differ in shape), e.g. where a camera's recording continues in its next file.
- GUI: the Resulting video timeline remembers its zoom and how far it is scrolled, per
  project, across page reloads (a new project opens fitted). It follows the playhead only
  when the playhead moves, never when you zoom.
- GUI: the Resulting video timeline zooms like the video strips: **− / +** halve and
  double it, the label says how far in ("whole", "4.0×"), **fit** shows the whole edit
  (it opens that way). Scrolling no longer zooms it (a pinch or ⌘-scroll does nothing
  there, rather than zooming the page); the = and − keys still do.
- Zoom glides: where a clip continues the previous clip's shot (same video, no gap) with
  another zoom framing, the picture moves smoothly from the old framing to the new over
  the clip's first second (eased: slow, faster, slow) instead of jumping, so split → zoom
  in → split → zoom out reads as a camera push in and pull back. On by default; the clip
  panel's **Zoom change** sets 0.5–3 s or *cut (no glide)* (`zoom_glide` in the project:
  absent = 1 s, false = cut, {sec}). The timeline marks each glide; the Resulting video
  preview plays it; the export moves a zoompan window over the original's full resolution
  (the whole picture sits in an output-shaped canvas, letterboxed if its shape differs).
- GUI: in Edit, the selected clip's panel (In/Out, Sound, Gain, **Zoom…**, transitions, Clean
  this clip…) sits right under the timeline instead of below every video strip.
- GUI: in Edit, the marked In–Out stretch (the blue box on a strip) can be cleared: **✕ Clear**
  next to In/Out, **Esc**, or **⌥X**; the strip is bright again.
- GUI: **Storage…** (in a project's header for its videos, on the Projects page for the
  whole library) shows per video the original, the editing copies (preview, thumbnails,
  cached frames), the tuning audio (blue stripes) and the rendered versions (green stripes),
  with project totals, the project folder, the exported video and what waits in `_trash`
  (**Empty _trash…** deletes it for good). `GET /api/storage`, `POST /api/trash/empty`.
- GUI: right-click a green bar (a rendered version) for Compare, Rename… and Delete…; delete
  is also on the candidates card and in a clip's sound menu. Deleting asks first, naming the
  projects whose clips use it; those clips go back to their original sound, and the file
  goes to `_trash`. `GET /api/outputs/{name}/used-by`.
- GUI: **Delete this tuning run…** on the candidates page removes a tuned window (its blue
  bar): its measurements, kept candidate audio and listening file go to the library's
  `_trash`, and a run left empty goes there whole. Versions rendered from it stay
  (`POST /api/plans/window/delete`).
- GUI: a cleaned version's label (the tag in its file name) can be changed after it is
  rendered: **Rename** next to it on the candidates card, and *rename* in a clip's sound
  menu. The video, its .params.json and every project clip that plays it follow
  (`POST /api/outputs/{name}/rename`). Versions without a tag are called "no label" (they
  were "first version", which was wrong once a stretch had several). Tuning runs say how
  they treated music on their bars ("3 candidates · music kept").
- GUI: the candidates page has **Clean this stretch again…**: the full clean form for that
  window, filled in (video, start, length, source kind), to clean it once more with settings
  chosen afresh (keep or remove the music, autotune or fixed settings, advanced options).
  Opened from a clip, the clip switches to the new version when it is ready. The page also
  says when a tuning ran with the music removed. Esc closes only the topmost of stacked
  dialogs.
- GUI: the candidates page lists, under the table, every setting of the candidate being looked
  at (it follows the selection): what this candidate chose, what the tuning run fixed (window,
  music separation, loudness) and clean-audio's defaults, with settings that do nothing (hiss
  amounts with hiss off, and so on) hidden unless asked for, a note when music separation is
  off (the speech denoiser then turns music down), and the command that renders it, plain and
  with every setting spelled out. New `GET /api/plans/candidate_settings`.
- GUI server: a memory budget for the editor and everything it starts (jobs, their children,
  the ffmpeg runs behind pictures and sound previews): the machine's memory less 8 GB by
  default (40 GB on a 48 GB Mac), or `VAC_MEMORY_BUDGET_GB`. A job starts only when its
  estimate fits (cleaning: about 2.2 GB + 0.45 GB per minute of the stretch, measured on an
  M3); one that can never fit fails at once, asking for shorter stretches. A watchdog checks
  every second and, over budget, stops preview ffmpeg runs, then the light job, then the
  heavy one. The bottom bar shows the editor's memory live (hover for the whole Mac's).
- GUI server: pictures for strips and posters run at most three ffmpeg at a time, skip
  requests the browser has dropped (panning a zoomed strip used to start dozens of 0.5 GB
  decodes of a 4K original a second, and is the likely cause of a crash), and come from the
  light editing copy when there is one (80 MB and 0.05 s instead of 0.5 GB and 0.6 s).
  Filmstrip frames sit on a fixed time grid, so panning reuses the frames already shown.
- GUI: the video strips in Edit give the room to the pictures: they are taller, and the
  lanes for cleaned versions and tuning take space only when something of theirs is in view
  (an empty lane is no longer drawn).
- GUI: in Edit, the Source and Resulting video viewers stay pinned at the top while the
  timeline and the video strips scroll under them (the Videos header sticks just below).
  Drag the handle under the viewers to make them smaller (to see more strips) or bigger;
  the height is remembered, and a double-click on the handle restores the default.
- GUI: the video strips in Edit zoom too, up to 200×: each strip has its own − / + under its
  name, and the Zoom slider in the Videos panel's header (which stays in view while the list
  scrolls) sets every strip at once. Scrolling never zooms a strip (a pinch or ⌘-scroll over the
  strips does nothing, rather than zooming the whole page). To pan, scroll sideways on
  a strip, or drag or scroll (a plain mouse wheel works) the bar above its pictures. Each strip pans on its own; the one
  being looked at follows its playhead only when playback runs out of view; a click on a
  strip moves the playhead, never the view. Frames, cleaned versions and time marks are redrawn
  for the stretch shown. Hovering a video's name shows all of it.
- GUI: in Edit, ←/→ always move by the chosen step. After a click on the scrub bar or the
  Zoom slider, the arrows used to move that slider by its own step (about a frame), and
  after picking a Source video they switched videos.
- GUI: in Edit, a box next to Zoom sets how far ←/→ move: 1 s while playing and a frame while
  paused (the default), a frame, or 0.1–10 s. ⇧ moves a frame instead (a second when the step
  is a frame). The choice is remembered.
- GUI: Space, J/K/L and the arrow keys drive the viewer last used (outlined): after a click on
  a video's strip, Space plays the Source viewer from there; a click on the timeline or the
  Resulting video viewer gives them back to the timeline.
- GUI: clean after cutting. Build the timeline on original sound, then clean only the clips
  in it: *Clean clips (N)…* in Edit queues every clip still on its original sound with one set
  of settings (nearby clips of a video as one stretch, each padded for soft sound), and a
  selected clip has *Clean this clip…* / *Clean again…* with the full clean form and
  *candidates…* for its tuned windows. A clip switches to its new cleaned version by itself
  when it is ready; the header shows "N of M clips cleaned · K cleaning".
- GUI: prepare several videos at once (*Prepare all* in Edit's Videos panel, *Prepare
  for editing (N)* for a selection in Media). Clips can be picked from a video before it
  is prepared (its strip, the marks, Sound, Clean this stretch…, Add to end); only its
  preview waits for the editing copy.
- GUI: add **several videos at once**. Media's *Add videos…* takes many files from one
  dialog (⌘/⇧-click), *Add a folder…* adds every video in a folder, and dropping several
  video files on the window adds them all (`POST /api/sources/many`; `/api/fs/pick`
  takes `multiple`).
- GUI Edit: the output timeline sits right under the players, with the Videos panel
  below it; video rows can be reordered (drag a name, or *to top*), and the order is
  kept with the timeline (`video_order` in project.json).
- GUI Edit: **clean while stitching.** *Clean this stretch…* opens the full clean form
  (tape/digital, background music, autotune with every candidate kept, tag, advanced)
  for the marked stretch over the editor; while it runs a striped bar marks it on the
  video's strip. Tuned stretches show as named bars (*36 candidates*); clicking one opens
  its Candidates view (listen A/B with the video, render another) over the editor, and
  rendered candidates appear as versions to hear and choose.
- GUI: long videos in the editor and in Compare. The editor streams any clip sound
  longer than three minutes instead of decoding it (a whole-tape stitch opens at once
  and plays without memory growing); Compare works on long cleaned files five minutes at
  a time, with a strip of the whole file to pick the section (PageUp/PageDown). The
  level-matching measurement no longer copies the sound, so it needs far less memory.
- GUI Edit: **pick clips from several videos.** A Videos panel shows every video of the
  timeline as a strip of frames with its cleaned stretches; click a strip to look at
  that moment, drag along it to choose a clip, cleaned or not. When the clip lies inside
  cleaned output(s), choose which version's sound it uses (or the original); a link
  leads to that window's tuning candidates. *Add videos…* brings in more of the
  project's videos.
  Click a version's green bar to hear that version in the Source viewer as it plays,
  without the picture moving (outside its stretch, the original is heard); the version
  heard is outlined and becomes the clip's sound. Every version bar is named (its tag,
  or "first version"), and each clip on the timeline says which sound it uses.
- GUI: **stitch videos into one timeline.** Select several videos in Media (⌘-click,
  ⇧-click, ⌘A) and choose *Stitch into a new timeline…*: set the order, use each whole
  (cleaned sound where it exists, original elsewhere) or only its cleaned pieces, and
  the sound between clips. The timeline gets a common picture size and frame rate, with
  notes when videos differ, and unprepared videos are prepared for the preview. Any
  cleaned piece, or a whole video, can also be added to the end of a timeline (*Add to
  timeline ▾*).
- Editing: **soft sound** between clips. The picture cuts; the sounds pass softly from
  one clip to the next. They overlap for the chosen time (equal power), centred on the
  cut, or shifted to the side that has sound beyond the cut (a cleaned piece ending
  where the original sound goes on); only where neither side has any (one video's end
  meeting the next one's start) does the sound fade out, then in. Back-to-back clips of one video with the same sound stay a plain
  cut. Chosen per clip in the inspector (soft sound 0.5/1/2 s), marked on the timeline,
  heard in the preview as exported, and it does not stop a lossless export
  (`transition_in: {"type": "soft_cut", "sec": …}` in project.json).
- `clean-audio-export`: clips from several videos are joined without re-encoding when
  the videos share one format (same codec, size, frame rate, pixel shape and encoder
  setup) and every jump in the picture is on a keyframe; a change of sound inside one
  video no longer counts as a cut. Each lossless join is checked after writing; if one
  does not decode cleanly the export re-encodes instead (`copy_verified` in the
  sidecar).
- Videos with non-square pixels (common in tape transfers, e.g. 16:15) are exported
  and previewed at their true shape: the export squares the pixels when it re-encodes,
  the canvas uses the displayed width, and posters, frames, thumbnails and the editing
  copy are widened (previews made earlier keep their old shape until made again).
- The probe records pixel aspect, codec profile/level, time base, a hash of the
  decoder setup, the recording date and the audio bitrate (probe version 2; older
  cached probes are refreshed). `export.suggest_canvas()` picks a common frame for
  clips from different videos.
- GUI: a full-window workspace layout (`docs/gui-editor-redesign.md`, phase A). Four
  workspaces switched from a bar at the bottom (⇧1–⇧4): **Media** (bins, a poster grid
  that scrubs through each video under the mouse, a details panel with the video's
  facts, cleaned windows, tuning and editing state), **Enhance** (compare, candidates,
  new clean), **Edit** and **Deliver** (timelines and the render queue). Jobs open in a
  drawer from the bar. Resizable panels remember their sizes. Dark theme only. Old links
  (`#/compare/…`, `#/candidates/…`, `#/new`, `#/jobs`) still work.
- GUI Enhance: every video and its pieces (cleaned windows and tuned windows) in a tree
  on the left, a breadcrumb back to the video and to all videos, `[`/`]` to step
  between a video's pieces and `{`/`}` between videos. Under the player, the whole video
  as a filmstrip with its pieces on it; hover one to see a frame from it, click to open
  it, drag across the bar to clean a new range. Media's details panel uses the same bar.
  Compare and Candidates show the clip's frames above the A/B waveforms, with the
  playhead through them; click the frames to seek. Switching to another version of the
  same stretch (or any piece that covers the moment) carries on from the same point in
  the video, on the same A/B side, still playing if it was.
- GUI Enhance, a video's own page: the whole original video in a player (the editing
  copy when prepared, else the file), with play, ±10 s and frame steps. On the bar below,
  a click jumps and a click-and-drag chooses a span (the player follows the mouse, so
  each edge can be placed by eye); `I`/`O` mark a span at the playhead. *Clean this
  span…* (`Enter`) opens the clean form with it.
  Clicking the bar on a piece's page opens the whole video at that moment.
- GUI: file names mixing Hebrew (or Arabic) with digits display in their real order
  (e.g. an output name no longer reads "02-04_חתונהm37s…").
- GUI Candidates: the video plays in sync while you listen, large above the waveforms
  (the editing copy when prepared, else the file), and `↑`/`↓` flip to the previous/next
  candidate in table order without stopping playback. The card on the right shows the
  candidate being heard: its settings, measurements and verdict.
- GUI jobs: a waiting job can be removed from the queue, finished ones removed one by one
  or with *Clear finished* (new `POST /api/jobs/{id}/remove` and `/api/jobs/clear`).
  "Queued" messages have a *Show queue* button.
- GUI Candidates: the candidate card shows its render while it waits or runs (progress,
  Show queue, Cancel/Remove), and a candidate that already has an output says so and
  links to it instead of rendering it again (*Render it again anyway…* is still there).
  Rendered candidates are marked in the table.
- GUI: preparing a video for editing can be cancelled where its progress is shown
  (Media and Enhance details, the editor).
- `/media/poster` takes `n=12` for the hover-scrub frames; new `/media/frame` for a
  frame at any time; the library lists exports.

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
