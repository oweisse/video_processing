# Local subtitles for Hebrew and English videos

## Goal and scope

Build a resumable, local-only pipeline for a MacBook Pro with Apple Silicon and 48 GB unified memory. Support Hebrew, English, and recordings that switch between them. Produce an editable transcript, subtitles in the spoken languages, optional fully Hebrew and fully English translations, and an optional video with subtitles burned in.

Network access is permitted during setup to download tools and models. Processing must work with networking disabled. Audio, video, transcripts, and translations must never require a remote inference service.

This is an implementation plan, not a tested installation guide. Pin tool versions and model revisions during implementation; verify available CLI options against those versions.

## Recommended stack

| Stage | Initial choice | Rationale |
|---|---|---|
| Inspect media and extract audio | FFprobe and FFmpeg | Local media handling and final rendering |
| Speech detection and transcription | whisper.cpp with Apple Silicon acceleration | Supports local Whisper inference and VAD without moving to a CPU-only backend |
| Baseline recognition model | OpenAI Whisper large-v3 | Baseline for mixed Hebrew/English recordings |
| Hebrew comparison model | ivrit.ai Whisper large-v3-turbo GGML | Candidate for predominantly Hebrew material; evaluate on actual recordings |
| Translation | Ollama with a local Gemma 3 27B quantized model | Initial candidate, subject to Hebrew translation evaluation |
| Orchestration | Python | Structured intermediate files, validation, caching, and restart support |
| Subtitle rendering | FFmpeg with libass and an installed Hebrew-capable font | Styled subtitles and reproducible burned-in output |

Do not assume a model's download size equals runtime memory consumption. Record actual peak memory and processing speed on the target Mac. Begin with one GPU-heavy job at a time and unload transcription models before loading the translation model if needed.

## Deliverables per video

| File | Purpose |
|---|---|
| `transcript.raw.json` | Unmodified recognition output, including available diagnostics |
| `transcript.reviewed.json` | Authoritative transcript after corrections; stable IDs and source timing |
| `transcript.txt` | Searchable text with time references |
| `translations.en.json` | English translations linked to source passage IDs |
| `translations.he.json` | Hebrew translations linked to source passage IDs |
| `video.mixed.srt` | Subtitles preserving the spoken languages |
| `video.en.srt` | Fully English subtitles, when requested |
| `video.he.srt` | Fully Hebrew subtitles, when requested |
| `video.<language>.ass` | Styled subtitles for rendering, when requested |
| `video.<language>.subtitled.mp4` | Separate burned-in viewing copy, when requested |
| `review.json` | Recognition, translation, timing, and layout issues requiring attention |
| `run.json` | Input fingerprint, versions, model revisions, settings, and completed stages |

Never overwrite the original video. Keep raw recognition output separately from corrections. Produce translations only for requested target languages.

## Phase 1: Validate the approach on representative clips

Before implementing a full batch workflow, select roughly five minutes of material containing:

- Clean Hebrew and clean English.
- Hebrew sentences containing English words and mid-sentence language changes.
- Names, numbers, dates, and family-specific vocabulary.
- Quiet speech, background noise, silence, and overlapping speakers.

Manually transcribe this small evaluation set. Compare stock large-v3 and the ivrit.ai fine-tune using the same clips and backend where possible.

Evaluate missing or invented words, language preservation, names and numbers, timestamp quality, runtime, peak memory, and correction effort. Word error rate is useful but does not capture every subtitle-quality issue. Do not select a model solely from a published Hebrew-only benchmark.

Choose a default model for each broad recording type. Retain the alternative model as an explicit retry option rather than running both on every file.

## Phase 2: Inspect and prepare audio

1. Inspect the input with FFprobe. Identify audio tracks, duration, channel layout, and audio/video start offsets.
2. Select the intended audio track explicitly. Listen to a sample before downmixing: one channel may contain cleaner speech, and different channels may contain different speakers.
3. Extract a 16 kHz mono PCM WAV for recognition when appropriate. Preserve the original media and track how extracted-audio time maps to video time.
4. Initially transcribe the original extracted audio. For problematic material, compare it against a lightly cleaned version on a short sample. Avoid aggressive denoising as a universal default.

Example for the first audio track, assuming downmixing is appropriate:

```bash
ffmpeg -i video.mp4 -map 0:a:0 -vn -ar 16000 -ac 1 -c:a pcm_s16le audio.wav
```

This extraction command alone does not establish the correct subtitle offset for every input; derive and verify the audio-to-video time mapping separately.

## Phase 3: Detect speech and transcribe

Use VAD to avoid sending long stretches of silence to the recognizer. Keep padding around speech boundaries, and merge nearby speech regions when doing so preserves useful sentence context.

Prefer the backend's supported VAD integration initially. If external chunking is necessary, retain each chunk's original start offset, use bounded overlap, and reconcile duplicated boundary text. Never concatenate speech regions without retaining a mapping back to original media time.

Do not cut every utterance into tiny fragments. Short fragments make language detection, names, and contextual recognition harder. Avoid treating background music or silence as speech; flag repeated text or implausible output for inspection.

### Mixed-language handling

Automatic language detection is a starting point, not a guarantee of faithful code-switching. A language selected near the beginning of a recording may bias later recognition.

- Test automatic detection and explicit Hebrew on predominantly Hebrew samples.
- For recordings with long stretches in different languages, process speech regions with enough context and allow a language override per region.
- For difficult regions, compare explicit Hebrew and English decoding, or retry the alternate model.
- Do not choose an output merely because it is more fluent. Use the audio to resolve important disagreements.
- For mid-sentence switches, retain the original-language words when recognizable. Do not silently translate them in the mixed transcript.

`faster-whisper` with `multilingual=True` is an optional comparison backend. Its per-processing-segment language detection does not guarantee correct recognition of every switch within a sentence. Verify its acceleration options on the installed Mac version before adopting it; do not assume CUDA-oriented performance results apply.

Save all available segment timing and diagnostics in `transcript.raw.json`. Recognition scores are useful review signals, not calibrated probabilities that the text is correct. Word timestamps, if available, are estimates and require spot checks.

## Phase 4: Review the source transcript

Create `transcript.reviewed.json` from the raw transcript. Assign stable source IDs and preserve source timing and provenance through corrections.

Prioritize review of:

- Names, places, numbers, and dates.
- Language changes and model disagreements.
- Low-confidence, repetitive, or unexpectedly empty regions.
- Speech near chunk boundaries and long gaps inside detected speech.
- Overlapping speakers and background voices.

Mark unintelligible speech explicitly instead of asking an LLM to invent missing content. A family-name glossary can guide recognition and translation, but it must not be treated as evidence that a particular name was spoken.

Offer two modes: a reviewed workflow for important recordings and an unattended draft workflow that preserves unresolved flags. Never label an unattended transcript as human-reviewed.

Speaker diarization is optional and outside the first implementation. Add it only if speaker labels materially improve the output; overlapping speech remains difficult even with diarization.

## Phase 5: Translate passages with context

Derive both target-language versions from the reviewed mixed transcript. This ensures corrections propagate consistently. Do not make independent audio-to-English translation the normal production path; reserve it for evaluating a difficult passage if useful.

Group text into complete sentences or short conversational passages before translation. Include limited preceding and following context, clearly distinguished from the text to translate. Translate into one target language per request.

Send stable passage IDs and request schema-constrained JSON containing those IDs and translated text. Keep timestamps outside the model's control. Ollama structured outputs constrain syntax; application validation must still check completeness and meaning.

Translation instructions should require:

- Preserve meaning, names, numbers, tone, and uncertainty markers.
- Translate all source languages into the requested target language.
- Preserve text already in the target language unless a small adjustment is necessary for coherence.
- Use surrounding context for pronouns and Hebrew gender agreement; do not invent identities or relationships.
- Return only the requested passage translations, without explanations.
- Avoid unnecessary verbosity while retaining substantive meaning.

Validate that every expected ID occurs exactly once and that no unknown IDs are returned. Flag missing text, unexpected commentary, suspicious number changes, or obvious language failures. Retry malformed responses a bounded number of times, then preserve the failure for review.

Cache translations by source text, context, target language, prompt version, and model revision. Source edits must invalidate affected translations, including those whose supplied context changed.

## Phase 6: Build readable subtitle cues

Treat subtitle layout as a separate stage from transcription and translation.

For the spoken-language track, use source segments and checked word timing when available. For translations, preserve each passage's source time span, then split the translated text into readable cues within that span. Translated word order can differ substantially; do not reuse source word timestamps as if they aligned to translated words.

Begin with configurable heuristics rather than rigid requirements:

- At most two lines per cue.
- Approximately 35–42 characters per line, adjusted after font and video preview.
- Flag reading speeds above roughly 17–20 visible characters per second for review.
- Prefer sentence or phrase boundaries and avoid separating tightly connected words.
- Avoid flashes, unintended overlaps, and cues extending beyond the media duration.

These are initial review thresholds, not language-independent standards. Permit splitting or merging cues while retaining their relationship to source speech. Do not extend a subtitle into unrelated speech merely to meet a reading-speed target. When a translation cannot fit comfortably, flag it for concise rewording or manual timing adjustment.

Use deterministic validation for positive durations, chronological order, media bounds, missing text, excessive line lengths, and unexpected overlaps. Keep source-to-cue mappings so errors can be traced back to the transcript.

## Phase 7: Hebrew rendering and video export

Write UTF-8 text in normal Unicode logical order. Never reverse Hebrew strings manually.

Preview mixed Hebrew/English words, parentheses, quotation marks, numbers, punctuation, and line breaks in the actual playback or burn-in renderer. Use an explicitly selected Hebrew-capable font and check that it is available on the machine.

Deliver editable SRT files first. Generate ASS when styled burn-in is requested, with readable font size, outline or background, and margins appropriate to the video resolution.

Use FFmpeg with libass to render a separate viewing copy. Burning subtitles requires video re-encoding. Select quality settings explicitly and preserve the original resolution, aspect ratio, and timing unless a conversion is requested. Handle rotation and HDR/color metadata explicitly; test unusual inputs rather than assuming a generic MP4 command preserves their appearance.

Copy audio only when compatible with the output container; otherwise encode it explicitly. Inspect the result near the beginning, middle, end, and representative language transitions.

## Phase 8: Offline operation and batch processing

During setup, download all models, VAD assets, and fonts. Pin versions and record model revisions or file hashes. Use local model paths and a loopback-only Ollama endpoint, with no remote inference fallback.

Run a complete short job with networking disabled, including translation and subtitle rendering. A successful transcription-only test does not prove the entire workflow works offline.

Start batch processing with one transcription or translation job using the GPU at a time. Measure throughput before increasing concurrency; multiple model instances compete for unified memory and bandwidth. CPU-side preparation may overlap if measurements show a benefit.

Persist each completed stage atomically. Support restart without retranscribing unchanged inputs, isolate failures per video, and keep an understandable run log. Use argument arrays for subprocesses so filenames containing spaces or shell characters are handled safely.

## Implementation order and acceptance criteria

1. **Pilot:** compare transcription candidates on the manually checked sample set and select defaults.
2. **Transcription workflow:** implement extraction, timing mapping, raw JSON, reviewable JSON, searchable text, and mixed SRT.
3. **Translation:** implement contextual passage translation, schema validation, bounded retries, and caching.
4. **Presentation:** implement cue layout, readability checks, Hebrew preview, and optional burn-in.
5. **Batch operation:** add resume support, per-video failure isolation, and measured concurrency.

Accept the first complete version when:

- The full pipeline succeeds with networking disabled after setup.
- Every cue and translation can be traced to source transcript IDs.
- Representative language switches preserve the spoken languages in the mixed track, with unresolved cases flagged.
- Translation output has no missing or duplicate passage IDs.
- Subtitle timing remains correct after silence, chunk boundaries, and near the end of a long recording.
- Hebrew and mixed-direction text render correctly in the intended output.
- A failed or interrupted job can resume without discarding completed work.
- Original media and raw recognition output remain untouched.

## References

- whisper.cpp: https://github.com/ggml-org/whisper.cpp
- ivrit.ai Hebrew Whisper turbo model: https://huggingface.co/ivrit-ai/whisper-large-v3-turbo
- ivrit.ai GGML model repository: https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ggml
- faster-whisper implementation and multilingual option: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- FFmpeg subtitle filter: https://ffmpeg.org/ffmpeg-filters.html#subtitles-1

The workflow, review thresholds, and model-selection procedure above are proposed engineering choices. Actual Hebrew/English accuracy and performance must be established using the pilot clips.
