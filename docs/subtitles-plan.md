# Subtitles: mixed, English only, Hebrew only

Status: **parts 0–6 built** (2026-11-20); part 7 (pilot with the user) not done. Written 2026-11-20 against commit `7aa08ca` (branch
`editor-stitch-clean-budget`). Self-contained: an implementer who has not seen the
discussion can execute it. Background notes by the user: `making-subtitles_002.md` (repo
root; the newer of two). Where those notes and this plan disagree, this plan wins; §14
lists the differences and why.

---

## 0. How to use this document

### 0.1 What the user asked for

1. While editing, add subtitles to **one clip** of the Resulting video timeline, so short
   stretches can be tried first. On export, subtitles for the **whole video**.
2. Three subtitle tracks:
   - **Mixed**: whatever was actually spoken (Hebrew, English, or both in one sentence).
   - **English only**.
   - **Hebrew only**.
3. Order of work: generate the mixed track → the user **edits** it → the user clicks
   *Create English only* / *Create Hebrew only*, which translates the **edited** text.
4. Everything runs **locally** on the Mac (Apple M5 Pro, 18 cores, 48 GB). Network only
   for the one-time download of tools and models.
5. Export delivers subtitles as **separate `.srt` files** and as **selectable tracks inside
   the MP4**. **Not** burned into the picture (decided 2026-11-20; the installed ffmpeg has
   no libass, and nothing in this plan needs it).

### 0.2 Read first

| File | Why |
|---|---|
| `docs/stitch-and-size-budget-plan.md` §0.2, §0.5 | Working rules and how this codebase documents progress |
| `making-subtitles_002.md` | The user's research: models, pitfalls, review priorities |
| `video_audio_cleaner/export.py` | Project model (`clips`, `owned_spans`, `duration`), `run()`, `finish()`; export gains subtitles in §10 |
| `video_audio_cleaner/sizing.py` (`run`) | The second export path; must get subtitles too |
| `video_audio_cleaner/{events,procs,progress,memory}.py` | Job conventions: JSON events, child-process tracking, cleanup, the memory budget |
| `video_audio_cleaner/gui/{app,jobs,commands,library,settings,storage}.py` | Job types and lanes, endpoints, the library scan, per-user settings |
| `gui/src/lib/{edit,program,api,state.svelte,bidi}.ts` | Edit model, the timeline player, API client, store, Hebrew-safe text helpers |
| `gui/src/routes/Editor.svelte`, `gui/src/components/Timeline.svelte`, `gui/src/edit/{VideoStrips.svelte,cleanclips.ts,awaiting.svelte.ts}` | Where the controls go; `cleanStretches` is reused for batch generation |
| `gui/src/deliver/ExportSettings.svelte`, `gui/src/edit/ExportDialog.svelte` | Export settings UI |
| `tests/test_export.py`, `tests/test_gui_server.py`, `tests/fixtures.py`, `tests/data/*.json` | Test conventions, including tables shared by pytest and vitest |

### 0.3 Working rules (in addition to those of the stitch plan §0.2)

- `./venv/bin/…` for Python; frontend `cd gui && npx svelte-check --threshold error && npx vitest run && npm run build`.
- **Never run jobs on the user's real videos or library unasked.** Test on synthetic
  material in a scratch folder; browser checks on a scratch server (not port 8787).
- **Ask the user before downloading anything.** The setup in §3 downloads about 20 GB.
- The user's GUI server on port 8787 runs from this checkout. Python changes reach new
  jobs immediately (each job is a fresh process), so keep every saved state working.
  Restart that server only after checking `GET /api/jobs` shows nothing queued or running.
- Commit and push only when the user asks. Do not work on `main`.
- Labels in plain language. Hebrew text is never reversed or reordered by our code.
- Every long operation is a CLI with `--progress json` first, a job type second.
- Keep `CHANGELOG.md` (Unreleased) and this document's status line and §0.5 up to date.

### 0.4 Facts about this machine (checked 2026-11-20)

- `ollama` is installed (`/opt/homebrew/bin/ollama`); no server was running; no model
  list was available. `whisper-cli` (whisper.cpp) is **not** installed.
- `ffmpeg` 8.1 has the `mov_text` and `srt` subtitle encoders, and **no** `subtitles` /
  `ass` filters. That is enough for this plan.
- The venv has `torch` (Python 3.11); no `mlx`, `faster_whisper` or `silero_vad`.
- Hebrew-capable fonts are installed (Arial Hebrew, SF Hebrew); only the browser preview
  uses fonts.
- The GUI server enforces a memory budget of total RAM − 8 GB (40 GB here) over itself
  and its child processes (`memory.py`, the watchdog in `gui/jobs.py`). A model that
  runs outside our process tree is invisible to it; §8.2 handles that for Ollama.

### 0.5 Implementation notes

Built 2026-11-20 (parts 0–6). Installed with the user's approval: `whisper-cpp` 1.9.5
(Homebrew), `silero-vad` 6.2.3 (venv), `ggml-large-v3.bin` (3.1 GB),
`ivrit-large-v3-turbo.bin` (from `ivrit-ai/whisper-large-v3-turbo-ggml`, file
`ggml-model.bin`), `gemma3:27b` (17 GB) in Ollama's store.

- §3 checks: whisper-cli 1.9.5 has every flag assumed; with several inputs and no `-of`
  it writes `<input>.json` per file (used: one run, many chunks). Silero VAD works offline
  with torch only. A private `ollama serve` on a free port sees models pulled through
  another; it must get **its own process group** (`procs.popen` default), else stopping it
  stops the job.
- Translation memory: the Ollama runner **is** counted by `memory.our_usage()` (20.6 GB peak
  translating six lines), so no extra term was needed (§9). `TRANSLATE_GB = 22`.
- Translation ids: long random cue ids were garbled by the model; requests now number the
  lines `"1"`, `"2"`, … and map back. The model sometimes also translated the following
  context as an extra line: extra ids are ignored (every sent id must still come back
  once) and the context fields are named `…_do_not_translate`.
- `tr.src` holds the normalised source text instead of a hash (§2.1), so the browser can
  tell "out of date" without hashing.
- Timeline mapping: Python uses `export.owned_spans` itself; the browser uses `place()`.
  They agree except when a crossfade is longer than half a clip (export does not clamp
  there); the shared table avoids that case.
- Export: subtitles are added by `export.add_subtitles` just before the final rename in
  both `export.run` and `sizing.run`; `.srt` files are written after it. Language tags
  `mul` / `eng` / `heb`; titles "Original (spoken)", "English", "Hebrew".
- Language per stretch (found on the user's ceremony clip, 2026-11-20): one detection
  per VAD piece wrote a 5 s Hebrew sentence inside an English stretch as invented
  English. Now each piece is checked in 3 s windows every 1.5 s (`whisper-cli -dl`, many
  files in one run, ~0.2 s each), a change needs two agreeing windows (p ≥ 0.6), the piece
  is cut at the quietest 100 ms within a second of the change, and chunks never mix
  languages (`language_runs`, `split_by_language`, `group_by_language`). Shorter pieces
  lose context for names: the Names list fixed "Tess and Ofir" on that clip.
- Not built yet: `setup --offline_check`, the `compare` developer command (§12), PieceBar
  coverage lane on the video strips, cue nudge keyboard keys (buttons only: ⌥-arrows move
  by word inside a text box). Undo of a merge re-adds the second line without its
  translations.

---

## 1. Decisions at a glance

| Topic | Decision |
|---|---|
| Where subtitles live | Per **source video**, in source time: `OUTPUT_DIR/_subtitles/<source_id>/subtitles.json`. A clip shows the cues that fall inside it. Re-cutting, moving or duplicating clips never loses subtitle work, and two projects using one video share it. |
| Tracks | One cue list. Each cue has the mixed text and optional `en` / `he` translations of that same cue (1:1, same timing). |
| Transcription | whisper.cpp (`whisper-cli`), model `ggml-large-v3`; the ivrit.ai Hebrew model as a selectable alternative. One process per job, the stretch cut into chunks at pauses, **language detected per chunk** (§5). |
| Speech detection | Silero VAD through the `silero-vad` Python package (torch is already here); energy-based fallback. |
| Audio transcribed | The **original** sound by default; the clip's cleaned version as an option (§5.1). The pilot (§12) settles the default. |
| Translation | A local LLM through **our own `ollama serve` child process**, default model `gemma3:27b`, one passage per request with context, schema-constrained JSON, validated (§8). |
| Editing | A *Subtitles* tab in the editor's lower area: a list of cues with text, timing, translations; split, merge, add, delete. Edits are saved per cue as they are made. |
| Preview | Cues drawn over the Resulting video (HTML overlay, `dir="auto"`), a subtitle lane on the timeline, a track switch: Mixed / English / Hebrew / Off. |
| Stale translations | A translation remembers a hash of the mixed text it was made from; when the mixed text changes it shows as **out of date**. *Update* retranslates only those. Hand-edited translations are never overwritten unasked. |
| Export | `.srt` files beside the video (`Name.mixed.srt`, `Name.en.srt`, `Name.he.srt`) and `mov_text` tracks in the MP4, added by one stream-copy remux at the end of either export path (§10). |
| Burn-in, diarization, `.ass` styling | Out of scope. |
| Jobs | Two new heavy job types, `subtitles` and `subtitles_translate`, in the ML lane (one heavy job at a time), with memory estimates. |

---

## 2. Formats

### 2.1 The subtitle document

`OUTPUT_DIR/_subtitles/<source_id>/subtitles.json` (`source_id` = the library's id for
the video, the key used in `project["sources"]`):

```json
{
  "schema": 1,
  "rev": 42,
  "source": {"id": "52501f8e4ee4", "path": "/…/GX010033.MP4", "duration_sec": 1730.4},
  "coverage": [[250.0, 409.0]],
  "runs": [
    {"id": "r3", "kind": "transcribe", "created": "2026-11-21T10:00:00+00:00",
     "start_sec": 250.0, "end_sec": 409.0, "model": "ggml-large-v3", "model_sha256": "…",
     "tool": "whisper.cpp 1.8.2", "language": "auto", "audio": "original",
     "vad": "silero", "raw": "raw/r3.json"},
    {"id": "r4", "kind": "translate", "created": "…", "to": "en", "model": "gemma3:27b",
     "model_digest": "…", "prompt_version": 1, "cues": 31}
  ],
  "cues": [
    {"id": "c7f3a91e", "start": 253.12, "end": 255.80,
     "text": "שלום everyone, תודה שבאתם",
     "lang": "mixed", "run": "r3", "edited": false, "flags": ["low_confidence"],
     "tr": {"en": {"text": "Hello everyone, thank you for coming", "src": "9c1f…",
                   "run": "r4", "edited": false, "flags": []}}}
  ]
}
```

- Times are seconds in the **source video**, 3 decimals. `cues` is sorted by `start`,
  never overlapping (`end ≤ next.start`).
- `text` may hold one `\n` (a line break the user chose); otherwise lines are wrapped
  when shown or written (§4.5).
- `lang`: `he` / `en` / `mixed` / `other`, computed from the text's letters (§4.2), not
  taken from the recognizer. Recomputed on every text edit.
- `coverage`: merged stretches that have been transcribed. A silent stretch is covered
  and has no cues.
- `tr.<lang>.src` = the mixed text at translation time, whitespace-normalised (changed
  from a hash during part 1 so the browser can compare it without hashing). **Stale** is
  computed (`src != normalise(text)`), never stored.
- `flags` (cue): `low_confidence`, `repeated`, `language_uncertain`, `chunk_edge`,
  `too_fast`, `too_long`. (translation): `failed`, `number_changed`, `wrong_language`,
  `too_fast`, `too_long`.
- `raw/<run>.json` keeps the recognizer's untouched output for that run. Never edited.
- Writes go through one function (`subs.update(doc_path, fn)`): take an exclusive
  `fcntl.flock` on `subtitles.lock`, re-read, apply `fn`, bump `rev`, write
  `subtitles.json.partial`, `os.replace`. Before replacing, copy the old file to
  `history/rev-000042.json` and keep the newest 20. Jobs and GUI edits both use it, so
  they can interleave.

A library-wide glossary of names: `OUTPUT_DIR/_subtitles/glossary.json`, a list of
`{"he": "אופיר", "en": "Ofir"}` (either side may be empty). Used as a recognition hint
and in translation (§5.2, §8.3).

### 2.2 Project additions (schema stays 1)

```json
"export": {
  "subtitles": {"srt": ["mixed", "en", "he"], "tracks": ["mixed", "en", "he"], "default_track": null}
}
```

Absent or empty lists = no subtitles in the export (today's behaviour). Nothing else in
the project changes: cues are found through each clip's `source`, `in_sec`, `out_sec`.

### 2.3 Models and tools

- Models directory: `~/Library/Application Support/video-audio-cleaner/models/` (beside
  the GUI settings; override `VAC_MODELS_DIR`). `whisper/ggml-large-v3.bin`,
  `whisper/ivrit-large-v3-turbo.bin`, and `models.json` recording file, source URL,
  sha256 and download date.
- Ollama models stay in Ollama's own store (`~/.ollama/models`).

---

## 3. Part 0: setup and checks (do first; ask the user before downloading)

New CLI `clean-audio-subtitles` (`video_audio_cleaner/subtitles_cli.py`, registered in
`pyproject.toml`), subcommand `setup`:

- `clean-audio-subtitles setup` prints what is present and what is missing, with the
  exact command for each missing piece, and exits non-zero when transcription cannot run.
  Checks: `whisper-cli` on PATH and its version; the whisper model file(s); the
  `silero_vad` import; `ollama` on PATH; the translation model in `ollama list` (through
  our own server, §8.2).
- `clean-audio-subtitles setup --download whisper|ivrit|translate` downloads one model
  with progress events (whisper models from Hugging Face `ggerganov/whisper.cpp` and
  `ivrit-ai/whisper-large-v3-turbo-ggml`; the translation model by `ollama pull`). It
  verifies size, records sha256 in `models.json`. Never runs implicitly.
- Installing software is the user's step: `brew install whisper-cpp`, and
  `./venv/bin/pip install silero-vad` (add `silero-vad` to a new optional dependency
  group `subtitles` in `pyproject.toml`).

**Checks the implementer must make before writing §5 and §8** (record results in §0.5):

1. `whisper-cli --help`: confirm the flags this plan assumes: several input files in one
   run (model loaded once), `-l auto`, `-ojf` (full JSON with per-token times and
   probabilities), `--prompt`, `-np`, `-t`, `-bs`, `-sns`, `--dtw`. Note the actual
   spellings. If several inputs in one run are not supported, use `whisper-server` (one
   model load, one HTTP request per chunk) behind the same interface (§5.2).
2. In the full JSON: where the detected language and its probability are, and whether
   token times are good enough to split cues (compare `--dtw large.v3` against plain
   token offsets on a synthetic clip with known word times; pick one).
3. `silero_vad`: `load_silero_vad()` and `get_speech_timestamps(...)` work offline in
   this venv without pulling `onnxruntime`.
4. Ollama: the exact model tag and size (`ollama show gemma3:27b`), that `/api/chat`
   accepts a JSON schema in `format`, and that a server started by us on another port
   (`OLLAMA_HOST=127.0.0.1:<port> ollama serve`) sees the already-pulled models.
5. Measure peak memory of one transcription and one translation job with
   `/usr/bin/time -l` plus `memory.our_usage()`; set the estimates in §6.1.
6. `ffmpeg` writes two `mov_text` tracks into an MP4 with language tags, and QuickTime
   Player and VLC show Hebrew cues right-to-left with punctuation in place (§10.4).

---

## 4. Part 1: the model and its pure functions (no ML)

New module `video_audio_cleaner/subs.py`. Everything here is pure or plain file I/O, and
fully unit-tested (`tests/test_subs.py`).

### 4.1 Paths and I/O

`docs_dir(output_dir)`, `doc_path(output_dir, source_id)`, `load(path)` (returns an empty
document when missing), `update(path, fn)` (§2.1), `new_id()` (`"c" + 8 hex`),
`text_hash(text)`.

### 4.2 Language of a cue

`script_lang(text)`: count Hebrew letters (U+0590–U+05FF) and Latin letters, ignoring
digits, punctuation and spaces. `he` when Latin < 10 % of letters, `en` when Hebrew
< 10 %, `mixed` otherwise, `other` when there are no letters of either.

### 4.3 Cue operations (used by the edit endpoint and by jobs)

All take and return the cue list, keep it sorted and non-overlapping, and raise
`ValueError` with a plain message when an edit is impossible.

| Function | Rule |
|---|---|
| `set_text(cues, id, text, lang="mixed")` | Mixed: sets `text`, `edited`, recomputes `lang`; translations stay and become stale by hash. `en`/`he`: sets `tr[lang].text`, `edited: true`, `src` = current hash (an edit means "this matches now"). Empty mixed text is refused (delete instead). |
| `set_time(cues, id, start, end)` | Clamped to the neighbours (keep 0.04 s apart) and to `[0, duration]`; at least 0.3 s long. |
| `split(cues, id, at_char, at_sec=None)` | Two cues; text cut at `at_char` (trimmed); time cut at `at_sec`, else in proportion to characters. Both halves lose their translations. |
| `merge(cues, id)` | With the next cue: texts joined with a space; translations joined when both exist, `src` rehashed; else dropped. |
| `delete(cues, id)`, `add(cues, start, end, text)` | `add` refuses an overlap. |
| `replace_range(cues, start, end, new_cues)` | Removes cues whose midpoint is in `[start, end)`, inserts `new_cues`, trims neighbours that would overlap. Used by transcription. |
| `add_coverage(coverage, start, end)` / `uncovered(coverage, start, end)` | Interval arithmetic. |

### 4.4 Building readable cues from recognized words

`build_cues(words)`: `words` = `[{"start", "end", "text", "p"}]` in order (source time).
Constants at the top of the module: `LINE = 42` characters, `MAX_CHARS = 84`,
`MAX_SEC = 6.0`, `MIN_SEC = 0.7`, `PAUSE = 0.5`.

1. Start a new cue at a pause ≥ `PAUSE`, after sentence punctuation (`. ? ! …`), or when
   adding the next word would pass `MAX_CHARS` or `MAX_SEC`. When forced to break by
   length, prefer the last comma, semicolon or colon in the cue so far; else the last
   pause ≥ 0.2 s; else the word boundary.
2. Cue `start` = first word's start − 0.05; `end` = last word's end + 0.15; then extend
   `end` to make `MIN_SEC` where the next cue allows; never overlap (0.04 s apart).
3. Flags: `low_confidence` when the mean `p` < 0.5; `too_fast` when characters / seconds
   > 20; `too_long` when over `MAX_CHARS`; `repeated` when three cues in a row have the
   same text (keep the first, flag it, drop the others).

### 4.5 Lines, SRT

- `wrap(text, line=LINE)`: a text with `\n` is kept; ≤ `line` is one line; else two
  lines, broken at the space nearest the middle, preferring one just after punctuation.
  Never more than two lines (the second may run long; that is what `too_long` flags).
- `to_srt(items)`: `items` = `[{"start", "end", "text"}]` on the output's clock. Numbered
  from 1, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, UTF-8 without BOM, `\n` line ends, one blank
  line between cues, text passed through `wrap`. Hebrew stays in logical order.
  `direction_marks=True` puts U+202B (RLE) before and U+202C (PDF) after each line that
  starts with a Hebrew letter; off by default; §10.4 decides whether exports use it.
- `from_srt(text)`: the inverse, tolerant of BOM, `\r\n` and `.` in times. Used by tests
  and by a later "import .srt" (not in this plan).

### 4.6 From source time to the timeline: one rule, two languages

`timeline_cues(project, docs, lang)` in `subs.py`, mirrored as `timelineCues(clips,
docs, lang)` in `gui/src/lib/subtitles.ts`. Both are tested against one table,
`tests/data/subtitle_mapping.json` (the pattern of `tests/data/joins.json`: cases with
`project`, `docs`, `lang`, `expect`; read by `tests/test_subs.py` and
`gui/src/lib/subtitles.test.ts`).

For each clip, in timeline order, with its owned span from `export.owned_spans(project)`
(`placed` / crossfade midpoint in `edit.ts`):

1. A clip with `audio.mute` gets no cues.
2. The clip shows source times `[s0, s1]` = its owned span mapped back to the source
   (`source_time = clip.in_sec + (timeline_time − clip_start)`).
3. Take the source's cues that overlap `[s0, s1]` by at least `min(0.4 s, half the cue)`.
   Clip their times to `[s0, s1]` and map to the timeline.
4. Drop a result shorter than 0.3 s. Keep 0.04 s between consecutive results (trim the
   earlier one), also across clip joins.
5. Text: `lang == "mixed"` → `text`. Else the cue's `tr[lang].text` when present and
   non-empty; a cue whose own `lang` equals the target needs no translation and uses
   `text`; otherwise **fall back to `text`** and mark the item `missing: true`.
6. Each item: `{start, end, text, cue, source, clip, missing, stale}` (`stale` from §2.1).

Also `completeness(project, docs, lang)` → `{cues, translated, missing, stale,
uncovered_sec}` where `uncovered_sec` is timeline time of unmuted clips outside every
`coverage`. The GUI and the export report use it.

**Done when:** `tests/test_subs.py` and the vitest mirror pass; no ML code exists yet.

---

## 5. Part 2: transcription (CLI)

### 5.1 Command

```
clean-audio-subtitles transcribe SOURCE --output_dir DIR --id SOURCE_ID
    --start SEC --end SEC [--replace missing|all] [--model large-v3|ivrit]
    [--language auto|he|en] [--audio original|PATH_TO_CLEANED_OUTPUT --audio_start SEC]
    [--channel mix|left|right] [--progress json]
```

- `--replace missing` (default): only the parts of `[start, end]` not in `coverage` are
  transcribed. `all`: the whole stretch is transcribed again and
  `subs.replace_range` replaces what was there (the GUI warns first, §7.2).
- The stretch actually transcribed is `[start − 2, end + 2]` clamped to the video, so
  cut-off words at the clip's edges are heard whole; that padded stretch is what
  `coverage` records.
- `--audio`: a cleaned output's own audio, with `--audio_start` = its
  `window.actual_start_sec`, when the user chose "the clip's cleaned sound".
- Events: `step` ("Finding speech", "Transcribing", "Building subtitles"), `progress`
  (seconds of audio done / total), `result` (`{"doc": path, "run": id, "cues": n,
  "flagged": n}`), like the other tools (`events.session`, `procs`).

### 5.2 Steps (`video_audio_cleaner/transcribe.py`)

1. **Audio.** `ffmpeg -ss S -t D -i SOURCE -map 0:a:0 -vn -ac 1 -ar 16000 -c:a pcm_s16le
   work/stretch.wav` (`--channel left|right` uses `pan=mono|c0=c0` / `c0=c1`). The work
   dir is a `tempfile.mkdtemp` registered with `procs.register_path`.
2. **Speech regions.** Silero VAD on the WAV (`min_silence 0.4 s`, `speech_pad 0.2 s`).
   Fallback when `silero_vad` cannot be imported: frames of 30 ms whose RMS is more than
   12 dB above the stretch's 10th-percentile level, with the same padding. No speech at
   all → the stretch is covered, no cues, a `message` event says so.
3. **Chunks.** Merge regions closer than 0.6 s; then pack consecutive regions into
   chunks of at most **28 s**, cutting only in the silences between regions. A single
   region longer than 28 s is cut at its quietest 100 ms window in the 20–28 s range,
   and both sides get the `chunk_edge` flag on the cues that touch the cut. Each chunk
   remembers its offset in the stretch. Write `work/chunk_0007.wav` files.
4. **Recognize** through a small interface so the backend can change:

   ```python
   class Recognizer:                      # transcribe.py
       def run(self, chunks, language, prompt, on_progress) -> list[ChunkResult]: ...
   # ChunkResult: {"index", "language", "language_p", "words": [{"start","end","text","p"}],
   #               "avg_logprob", "no_speech_p", "raw": <the backend's JSON>}
   ```

   `WhisperCli` (default): **one** `whisper-cli` run over all chunk files (the model
   loads once; the language is detected per file), full JSON per chunk, threads =
   performance cores, beam 5, temperature 0, non-speech tokens suppressed. Progress =
   chunks finished (poll for the JSON files, or parse the progress lines). Tokens are
   joined into words (a token starting with a space starts a word; Hebrew has no
   special case), special tokens dropped. `FakeRecognizer` for tests returns words from
   a fixture.
5. **Language retry**, at most once per chunk: when `language_p < 0.6`, or the detected
   language disagrees with the letters (`en` detected but `script_lang` of the text is
   `he`, or the reverse), run those chunks again forced to `he` and forced to `en` (two
   more `whisper-cli` runs over just those files) and keep the result with the highest
   `avg_logprob`; flag its cues `language_uncertain`. `--language he|en` skips detection
   and this step.
6. **Hint.** `--prompt` = glossary names for the language(s) expected, joined with
   commas, at most 200 characters. Nothing else (a prompt in one language biases the
   next chunk toward it).
7. **Cues.** Word times + chunk offset + stretch start → source time; `subs.build_cues`;
   `lang` by letters; a chunk with `no_speech_p > 0.6` and `avg_logprob < −1.0`
   contributes nothing.
8. **Save.** `subs.update`: add the run, `replace_range` over each transcribed part
   (keeping cues outside it), `add_coverage`, write `raw/<run>.json` (chunks with their
   offsets and the backend's JSON).

Cancellation (SIGINT from the job queue) must leave the document untouched: step 8 is
the only write.

### 5.3 Tests (`tests/test_transcribe.py`)

- Chunk packing and the long-region cut, on synthetic region lists.
- The whole flow with `FakeRecognizer` on a fixture WAV: cues land at the right source
  times; `missing` transcribes only the uncovered part; `all` replaces; a cancelled run
  changes nothing.
- The energy fallback finds the tone bursts in a synthetic WAV.
- One test marked `slow` (deselected by default, like the model tests) that runs the
  real `whisper-cli` on a 10-second synthetic speech file made with macOS `say`
  (`say -o x.aiff "hello this is a test"`), skipped when the tool or model is missing.

---

## 6. Part 3: server and jobs

### 6.1 Job types (`gui/commands.py`, `gui/app.py`, `gui/jobs.py`)

| Type | Request | Lane | `meta.memory_gb` |
|---|---|---|---|
| `subtitles` | `{source_id, start_sec, duration_sec, replace, model, language, audio: "original" \| <output file name>, channel}` | ML (add to `ML_TYPES`) | measured in §3 check 5 (expect 4–6) |
| `subtitles_translate` | `{source_id, to: "en"\|"he", start_sec?, duration_sec?, mode: "missing"\|"stale"\|"all"}` | ML | model size + 4 (expect about 22) |
| `subtitles_setup` | `{what: "whisper"\|"ivrit"\|"translate"}` (download one model) | light | 0.5 |

`meta` carries `source_id`, `start_sec`, `duration_sec` like clean jobs, so the strips
show the striped "in progress" bar (extend the label in `VideoStrips.svelte`'s
`pendingFor` to say "subtitles" / "translating" by job type). Titles: "Subtitles for
GX010033 4:12–6:47", "Translate GX010033 to English".

### 6.2 Endpoints (`gui/app.py`)

| Endpoint | Does |
|---|---|
| `GET /api/subtitles/setup` | What `clean-audio-subtitles setup` reports, as JSON: per piece `{ok, detail, command}`. Cached 30 s. |
| `GET /api/subtitles/{source_id}` | The document (`{"schema": 1, "rev": 0, "cues": [], …}` when none). Optional `?start=&end=` returns only cues overlapping the range. |
| `POST /api/subtitles/{source_id}/edit` | `{ops: [...]}` applied in order under the lock; returns `{rev, cues: [the changed cues], removed: [ids]}`. Ops: `{"op": "text", "id", "lang", "text"}`, `{"op": "time", "id", "start", "end"}`, `{"op": "split", "id", "at_char", "at_sec"}`, `{"op": "merge", "id"}`, `{"op": "delete", "id"}`, `{"op": "add", "start", "end", "text"}`, `{"op": "clear_tr", "id", "lang"}`. An unknown id → 404 naming it, and **no** op of that request is applied. |
| `GET /api/subtitles/glossary`, `PUT …` | The names list. |
| `GET /api/projects/{name}/subtitles?lang=` | `{items: timeline_cues(...), completeness: {...}}` for one track; `lang=all` returns `{completeness: {mixed, en, he}}` only. Used by Deliver; the editor computes its own from the documents (§7.1). |
| `GET /api/projects/{name}/subtitles.srt?lang=` | The timeline's `.srt` as a download (handy for checking in a player before exporting). |

The library scan (`gui/library.py`) adds to each source a small `subtitles` summary read
from the document: `{rev, coverage, cues, edited, flagged, langs: {"en": {"done",
"stale"}, "he": {…}}}` (null when none). `storage.py` counts `_subtitles/<id>` in a new
`subtitles` figure per video (shown in Storage as its own column; these are small).

### 6.3 Tests (`tests/test_gui_subtitles.py`)

Edits round-trip and are atomic per request; a job request becomes the right argv
(`/api/jobs/preview`); the summary appears in `/api/library`; the project endpoint maps
cues through clips; setup reports missing tools without failing.

---

## 7. Part 4: the editor

### 7.1 State (`gui/src/lib/subtitles.ts`, `gui/src/edit/subtitles.svelte.ts`)

- `subtitleDocs`: a `$state` map `source_id → document`, loaded for the project's videos
  when the editor opens and reloaded for one source when a `subtitles*` job for it
  finishes (the store already receives job events) or after an edit response.
- `timelineCues(clips, docs, lang)` (§4.6) is `$derived` from the project's clips and
  the documents; everything below reads from it.
- The chosen track: pref `edit.subtitleTrack` = `"off" | "mixed" | "en" | "he"`.
- Edits: optimistic. Apply the op locally, `POST …/edit`, replace the affected cues with
  the response; on failure put the old cue back and toast the reason. Text edits are
  sent when the field loses focus or after 800 ms without typing.
- Undo inside the Subtitles tab: its own stack of inverse ops (⌘Z while a subtitle field
  or the tab has focus). The project's undo stack is not involved, since subtitles are
  not part of the project file.

### 7.2 Generating: one clip, or all of them

- **Clip panel** (under the timeline, where *Zoom…* and *Clean this clip…* are): a
  *Subtitles* group for the selected clip showing its state ("none", "31 cues · 4 need a
  look", "English ✓ · Hebrew out of date (3)") and:
  - **Generate subtitles…** / **Generate again…** → a small dialog: Model (Standard /
    Hebrew-tuned), Language (Detect per sentence / Hebrew / English), Listen to (Original
    sound / This clip's cleaned sound, offered when the clip plays a cleaned version),
    then *Generate*. "Again" on a stretch with edited cues or translations says how many
    will be replaced and needs a second confirmation.
  - **Create English only** / **Create Hebrew only** (enabled once the clip has cues) →
    queues `subtitles_translate` for the clip's stretch, `mode: "missing"`. When
    translations exist: **Update English (3 out of date)** → `mode: "stale"`;
    a menu item *Translate everything again* → `mode: "all"` after a confirmation that
    names how many hand-edited translations would be replaced.
- **Header**, beside *Clean clips…*: **Subtitles…** with a menu: *Generate for clips
  without subtitles (N)*, *Create English only for all*, *Create Hebrew only for all*,
  *Names…* (the glossary dialog). Batch generation reuses `cleanStretches` from
  `edit/cleanclips.ts` (pad 2 s, merge stretches of one video closer than 20 s) and
  queues one job per stretch, like *Clean clips*.
- When setup is incomplete (`GET /api/subtitles/setup`), these buttons open a **Set up
  subtitles** dialog instead: each missing piece with its command (copy button), a
  *Download* button for model files (queues `subtitles_setup`, shows size), *Check
  again*.

### 7.3 Seeing them

- **Over the Resulting video:** a `div.subs` inside `.screen.program`, bottom-centred,
  showing the item of `timelineCues` under the current time (binary search). One `<div
  dir="auto">` per line (`unicode-bidi: plaintext`), white on a translucent dark box,
  font-size `5cqh`, at most two lines (`text-wrap: balance`). A `missing` item (a
  fallback to the mixed text on the English/Hebrew track) is shown in italics with a
  small "not translated" tag; a `stale` one with "out of date".
- **Track switch** in the Resulting video header: `Subtitles: Off · Mixed · English ·
  Hebrew` (segmented control; tracks with no cues at all are dimmed).
- **Timeline:** a third lane, "Subtitles", under the music lane (`Timeline.svelte` gets a
  `cues` prop = `timelineCues` items): one block per cue with its first words
  (`dir="auto"`), dimmed red outline when flagged, hatched where an unmuted clip has no
  coverage. Clicking a block seeks to it and opens the Subtitles tab at that cue.
- **Clip badge:** next to the sound badge, a small `CC` chip per clip: grey "CC –"
  (none), green "CC" (mixed), with "+EN" / "+HE" when those are complete for the clip,
  amber when something is out of date or flagged. Tooltip spells it out.
- **Video strips:** a thin lane under the pictures marking `coverage` (PieceBar gets a
  `subtitles` prop), so it is visible which parts of a video already have subtitles.

### 7.4 Editing: the Subtitles tab

The editor's lower area (today the Videos strips) gets two tabs, **Videos** and
**Subtitles** (pref `edit.lowerTab`). The viewers stay pinned above, so the user watches
the Resulting video while editing.

- **Rows:** the timeline's cues in order, with a thin separator and the clip's name
  where a new clip starts. Filters: *This clip* / *Whole video*; *Needs a look* (flagged,
  missing or out of date).
- **Columns:** time in the resulting video (start–end, click to seek); **Mixed** (a
  `textarea`, `dir="auto"`, grows to two lines); **English**; **Hebrew** (same; empty
  with a faint "not translated" until created; an out-of-date translation has an amber
  edge and a one-click *Update* that retranslates just that cue's passage); flags as
  small chips with tooltips ("The recognizer was unsure here", "Reads fast: 24
  characters per second").
- **Per-row actions** (buttons at the row's end, and keys while the row has focus):
  split at the text cursor (`⌘⏎`), merge with the next (`⌘J`), add a cue after (`⌘⇧⏎`),
  play just this cue (`⌘P`; plays the Resulting video from the cue's start and pauses at
  its end). Delete and the 0.1 s nudges are buttons only. As built: `⌘⌫` and `⌥←/→`
  already edit text inside the box (delete to the line start, move by word) and
  `⌘⇧N` opens a private window in the browser, so they are not taken.
  `⏎` moves to the next row (a line break inside a cue is `⇧⏎`).
- **Follow:** while playing, the row under the playhead is highlighted and scrolled into
  view unless the user is typing in another row.
- The editor's global keys already ignore text fields (`typing()` in `Editor.svelte`
  keeps `TEXTAREA`), so typing never triggers split/delete of clips.
- Times shown and edited are **timeline** times; ops are sent in **source** time
  (`source_time = clip.in_sec + (t − clip_start)`). A cue cut by a clip edge can only be
  retimed on the visible side.

### 7.5 Tests

Vitest: `timelineCues` (the shared table), the op-to-inverse-op undo logic, time
conversion at clip edges. Browser check on a scratch project (fake documents written by
a script; no models needed): overlay follows playback in both directions of text, lane
blocks line up with clips after a split and a reorder, edits survive a reload.

---

## 8. Part 5: translation

### 8.1 Command

```
clean-audio-subtitles translate --output_dir DIR --id SOURCE_ID --to en|he
    [--start SEC --end SEC] [--mode missing|stale|all] [--model TAG] [--progress json]
```

Which cues get translated (within the range, or the whole document):

| Mode | Cues |
|---|---|
| `missing` | No `tr[to]`, or an empty one. |
| `stale` | Also those whose `src` no longer matches. |
| `all` | Every cue. |

Never translated in any mode: a cue whose `lang` equals `--to` (its text already is the
target language; `timeline_cues` uses it as is). Never overwritten unless `--mode all`
with `--replace_edited`: a translation with `edited: true`.

### 8.2 Our own Ollama (`video_audio_cleaner/translate.py`)

- Find a free port; start `ollama serve` with `OLLAMA_HOST=127.0.0.1:<port>`,
  `OLLAMA_KEEP_ALIVE=5m`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, through
  `procs.popen` (so it is in the job's process group: cancel kills it, and the memory
  watchdog counts it and the model runner it starts). Wait up to 20 s for
  `GET /api/version`.
- The model must already be pulled (`GET /api/tags`); otherwise fail with the setup hint.
  The job never downloads.
- On every exit path: ask it to unload (`POST /api/generate` with `keep_alive: 0`), then
  terminate the server.
- Reason for not using a server the user may already run: its memory would be outside
  the budget, and it may be busy or configured differently. If port discovery or the
  shared model store fails on this machine (§3 check 4), record the finding and fall
  back to the user's server at `127.0.0.1:11434`, adding the model's size from
  `/api/ps` to the job's memory estimate.

### 8.3 Passages, the request, validation

- **Passages:** walk the cues in order (all cues, not only those to translate, since
  context matters). A passage ends after a cue that ends a sentence (`. ? ! …`), at a
  gap ≥ 2 s to the next cue, at 8 cues, or at 600 characters. A passage is sent when at
  least one of its cues needs translating.
- **Request** (`POST /api/chat`, `stream: false`, `options: {temperature: 0, seed: 0,
  num_ctx: 8192}`), `format` = a JSON schema `{lines: [{id: string, text: string}]}`
  with exactly those fields required:
  - System prompt (version it: `PROMPT_VERSION = 1`, stored in the run): translate
    subtitle lines into `<target>`; keep meaning, names, numbers, tone; text already in
    the target language stays as it is; translate **every** other language into the
    target; one output line per input line with the same `id`; words may move between
    neighbouring lines of the same sentence so each line reads naturally, but no line
    may be left empty; keep it about as long as the original (subtitles are read fast);
    mark nothing, explain nothing; `[unclear]` stays `[unclear]`.
  - User message: JSON with `target`, `glossary` (pairs relevant to this passage:
    entries whose `he` or `en` appears in the passage or its context),
    `context_before` (the previous two passages as `{source, translation}` where a
    translation exists), `lines` (`[{id, text}]` of this passage: **all** its cues, so
    the sentence is whole), `context_after` (the next passage's source text).
- **Validation**, per passage: the ids returned are exactly the ids sent, each once;
  no empty text; digits in each line's source appear in its translation (else flag
  `number_changed`); for `he` the line contains Hebrew letters, for `en` Latin, unless
  the source line has no letters (else `wrong_language`); length within 0.4–2.5× of the
  source (else `too_long` or retry). A malformed or failed reply is retried twice (the
  second time with `seed: 1`); then the cues that needed translating get `tr[to] =
  {"text": "", "flags": ["failed"], …}` and the job continues. The job's result counts
  `translated`, `failed`, `flagged`.
- **What is stored:** only for cues that were due (§8.1), `tr[to] = {text, src:
  text_hash(cue.text), run, edited: false, flags}`. Lines of the passage that were sent
  only as context are discarded, so an existing good or hand-edited translation never
  changes behind the user's back.
- Save after every passage (`subs.update`), so a cancelled job keeps what it finished
  and progress shows in the editor as it goes. Progress events: passages done / total.

### 8.4 Tests (`tests/test_translate.py`)

A fake chat function (injected; the real one is a thin `urllib` call): passage building;
only due cues are stored; edited translations survive; validation failures retry and
then flag; same-language cues are skipped; a cancelled run keeps finished passages. One
`slow` test against a real Ollama, skipped when absent.

---

## 9. Memory and scheduling

- Both job types go through the existing admission check (`meta.memory_gb`) and the
  watchdog. With the estimates from §3 check 5, a translation (about 22 GB) and a clean
  never overlap anyway (one ML job at a time), and a preparing job (1.5 GB) fits beside
  either.
- Verify with `memory.our_usage()` during a real translation that the Ollama runner's
  memory **is** counted (it holds the model on the GPU; `phys_footprint` included GPU
  memory for Demucs). If it is not, add an explicit term: while a
  `subtitles_translate` job runs, `jobs.py` adds the model size reported by
  `/api/ps` to the usage it compares with the budget.
- whisper.cpp and Ollama both use the GPU; never run them at once (the ML lane
  guarantees it).

---

## 10. Part 6: export

### 10.1 Settings and CLI

`export.subtitles` (§2.2). `clean-audio-export` gains `--subtitles_srt mixed,en,he` and
`--subtitle_tracks mixed,en,he` (either may be `none`), overriding the project keys, and
`--subtitles_dir DIR` (default: `<library>/_subtitles`, the library being two levels
above `_projects/<name>/project.json`).

### 10.2 What export does (`export.py`, one new function used by both paths)

`add_subtitles(partial, project, project_path, s, work) -> dict`, called in `run()` and
in `sizing.run()` after the video is written to `partial` and **before**
`os.replace(partial, out)`:

1. Load the documents of the project's sources; for each requested language build
   `subs.timeline_cues` and `subs.to_srt` → `work/<lang>.srt`. A language with no cues
   at all is skipped with a warning.
2. **Tracks:** when any are requested, remux once:
   `ffmpeg -y -i partial -f srt -i work/mixed.srt -f srt -i work/en.srt … -map 0:v -map
   0:a -map 1:0 -map 2:0 … -c:v copy -c:a copy -c:s mov_text -metadata:s:s:0
   language=mul -metadata:s:s:0 title="Original (mixed)" -metadata:s:s:1 language=eng
   -metadata:s:s:1 title="English" -metadata:s:s:2 language=heb -metadata:s:s:2
   title="Hebrew" -disposition:s 0 [-disposition:s:N default] -movflags +faststart
   partial2` then replace `partial` with it. Keep the `hvc1` tag when the video is HEVC
   (`-tag:v hvc1`). This is a stream copy: seconds, not a re-encode; it needs room for
   one more copy of the file while it runs (register `partial2` with `procs`).
3. **Files:** after the final `os.replace`, write `Name.mixed.srt`, `Name.en.srt`,
   `Name.he.srt` beside `Name.mp4` for the requested languages (atomic, replacing).
4. Return, and put into the export's result and `.params.json`:
   `{"tracks": [...], "srt": [paths], "completeness": {lang: {...}}}`; print one line
   per language ("English: 412 cues, 6 not translated (shown in the original
   language), 3 out of date").

Missing translations never block an export; they fall back to the mixed text (§4.6) and
are reported. Subtitles are ignored by estimates (`--estimate`) and add nothing
measurable to a size budget.

### 10.3 Export settings UI (`gui/src/deliver/ExportSettings.svelte`)

A **Subtitles** section: per track (Mixed / English / Hebrew) two checkboxes, *separate
.srt file* and *selectable track in the video*; beside each track its completeness from
`GET /api/projects/{name}/subtitles?lang=all` ("412 cues · 6 not translated · 3 out of
date", or "none yet"); a *Shown by default* choice among the embedded tracks (None by
default); when clips lack subtitles or translations, a line "9 min of the video has no
subtitles yet" with a link to the editor's Subtitles menu. The editor's Export dialog
uses the same component, so it gets this for free.

### 10.4 Checks and tests

- `tests/test_export.py`: an export with a document for the fixture source writes the
  `.srt` files with timeline times (after a cut, a reorder and a crossfade), embeds the
  tracks (`ffprobe` shows `mov_text` streams with `language` tags and titles), keeps the
  video stream untouched in the copy path, and works through `sizing.run` too. Without
  `export.subtitles` the output is byte-for-byte what it is today.
- By hand, with the user (they decide what looks right): open an exported file in
  QuickTime Player, VLC and on an iPhone; check Hebrew lines with numbers, parentheses
  and a question mark. If punctuation lands on the wrong side in a player the user
  cares about, turn on `direction_marks` for the `.srt` files and/or tracks and record
  which players needed it.

---

## 11. Offline

After setup, a short end-to-end run (transcribe a clip, translate both ways, export)
must succeed with Wi-Fi off. No code path may fetch anything: whisper.cpp reads a local
model file; `silero_vad` ships its model in the package; our Ollama server only reads
its local store. Add `clean-audio-subtitles setup --offline_check` that verifies every
file and tag is present without touching the network, and make the GUI's setup dialog
show its result.

---

## 12. Part 7: pilot with the user, then defaults

Do this once parts 1–5 work, **with the user, on clips they choose** (about five minutes
in all: clean Hebrew, clean English, mid-sentence switches, names and numbers, noisy or
quiet speech). The editor is the tool:

1. Generate with the standard model; the user corrects the mixed text. That corrected
   text is the reference.
2. `clean-audio-subtitles compare --id … --start … --end … --model ivrit [--audio …]`
   (a developer command): transcribes the same stretch with another model or audio
   choice **without touching the document**, and prints word error rate against the
   reviewed cues, per language, plus a side-by-side of the lines that differ.
3. Decide with the user: default model per kind of recording (Standard vs Hebrew-tuned),
   original vs cleaned sound, whether the language retry helps. Store the defaults in
   the GUI settings (`subtitles.model`, `subtitles.audio`).
4. Translation: translate the reviewed cues both ways with `gemma3:27b` and one or two
   alternatives the user has room for; the user reads them in the Subtitles tab. Pick
   the default (`subtitles.translate_model`).
5. Record runtimes and peak memory per minute of audio in §0.5 and tighten the
   estimates in §6.1.

---

## 13. Order of work and acceptance

| Part | Contents | Needs models |
|---|---|---|
| 0 | Setup command and the checks of §3 | downloads (ask first) |
| 1 | `subs.py`, the shared mapping table, TS mirror | no |
| 2 | `transcribe.py`, `clean-audio-subtitles transcribe` | whisper |
| 3 | Job types, endpoints, library summary | no (fakes in tests) |
| 4 | Editor: generate, overlay, lane, badge, Subtitles tab | whisper for real use |
| 5 | `translate.py`, the translate job, the buttons | Ollama + model |
| 6 | Export: `.srt` files and tracks, settings UI | no |
| 7 | Pilot and defaults | all |

Parts 1, 3, 4 (with fake documents) and 6 can be built and tested before any model is
installed; do them first if downloads are waiting on the user.

Accept when:

- A clip gets mixed subtitles from the editor; they show over the Resulting video, on
  the timeline lane and in the Subtitles tab; cutting, moving and duplicating clips keeps
  them in step.
- Edits to text and timing are saved, survive a reload, and undo works in the tab.
- *Create English only* / *Create Hebrew only* translate the **edited** mixed text;
  editing the mixed text afterwards marks the translation out of date, and *Update*
  retranslates only those; hand-edited translations are not overwritten.
- An export writes the requested `.srt` files and embeds the requested tracks, with
  times right after cuts and across a long video; QuickTime and VLC show them.
- Every job is cancellable, leaves the document intact, respects the memory budget, and
  nothing runs on the network after setup.
- The full suites pass (`./venv/bin/python -m pytest -q`; `cd gui && npx vitest run`),
  `svelte-check` shows no errors, README has a "Subtitles" section, CHANGELOG is updated.

---

## 14. Where this differs from `making-subtitles_002.md`, and why

| The notes | This plan | Why |
|---|---|---|
| One set of files per video (`transcript.raw.json`, `transcript.reviewed.json`, …) | One document per **source** video with raw output kept per run; `.srt` files are produced at export from the timeline | The tool edits clips of several videos into one output; subtitles must follow the clips. |
| Translate passages, then lay translated text out into new cues within the passage's time span | Translate **cue by cue** (1:1) with the whole passage and neighbours as context, words allowed to move between lines of a sentence | The user edits mixed and translated text side by side and retranslates single cues; a 1:1 mapping keeps that simple and traceable. Cues are built at phrase and sentence breaks, so most are whole clauses. Revisit if the pilot shows awkward lines. |
| Context changes invalidate neighbours' translations | Only a cue's own text change makes its translation out of date | Predictable for the user; retranslating a cue still sends its whole passage. |
| Ollama as a service | Our own `ollama serve` child per job | The memory budget only sees our process tree; the model is unloaded when the job ends. |
| VAD through the backend | Silero VAD in Python, then chunks, language detected per chunk | whisper.cpp fixes the language for a whole input; per-chunk inputs let Hebrew and English stretches each be detected. |
| Pilot first | Pilot after the editor exists (part 7) | The editor is how the user makes the reference transcript. |
| Burned-in copy, ASS styling | Not built | The user chose `.srt` files and selectable tracks. |
| Speaker diarization optional | Not built | As in the notes. |
