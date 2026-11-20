"""Transcribing a stretch of a video into the mixed subtitle track (docs/subtitles-plan.md §5).

Audio of the stretch (plus 2 s each side) → speech regions (Silero VAD, or an energy
fallback) → chunks of at most 28 s cut in pauses → whisper.cpp over all chunks in one
run, the language detected per chunk → a retry forced to Hebrew and to English where the
language looks wrong → readable cues → the source's subtitle document. Only the last
step writes, so a cancelled run leaves the document as it was.
"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import procs, subs
from .console import print_info, print_success, print_warning
from .events import emit

SR = 16000
PAD_SEC = 2.0
CHUNK_MAX = 28.0
MERGE_GAP = 0.6
LANG_P_MIN = 0.6

MODELS = {
    "large-v3": {"file": "ggml-large-v3.bin", "label": "Standard (Whisper large-v3)",
                 "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
                 "size_gb": 3.1},
    "ivrit": {"file": "ivrit-large-v3-turbo.bin", "label": "Hebrew-tuned (ivrit.ai large-v3-turbo)",
              "url": "https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ggml/resolve/main/ggml-model.bin",
              "size_gb": 1.6},
}


def models_dir():
    env = os.environ.get("VAC_MODELS_DIR")
    base = Path(env) if env else Path.home() / "Library" / "Application Support" / "video-audio-cleaner" / "models"
    return base


def model_path(name):
    return models_dir() / "whisper" / MODELS[name]["file"]


def whisper_bin():
    return shutil.which("whisper-cli")


def perf_threads():
    try:
        n = int(subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], capture_output=True,
                               text=True, check=True).stdout.strip())
        return max(4, min(12, n + 2))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 8


# ------------------------------------------------------------------ audio and regions
def extract(source, start, dur, out, channel="mix", audio_start=0.0):
    """16 kHz mono WAV of [start, start+dur] of source (seconds of the source; for a
    cleaned output, audio_start is where that output begins in its source)."""
    pan = {"left": ["-af", "pan=mono|c0=c0"], "right": ["-af", "pan=mono|c0=c1"]}.get(channel, ["-ac", "1"])
    offset = max(0.0, start - audio_start)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{offset:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
           "-map", "0:a:0", "-vn", *pan, "-ar", str(SR), "-c:a", "pcm_s16le", str(out)]
    p = procs.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _, err = p.communicate()
    procs.untrack(p)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the audio: {err.decode(errors='replace')[-300:]}")


def speech_regions(audio):
    """[(start, end)] seconds of speech in a 16 kHz mono float array."""
    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = load_silero_vad()
        ts = get_speech_timestamps(torch.from_numpy(audio.astype(np.float32)), model, sampling_rate=SR,
                                   min_silence_duration_ms=400, speech_pad_ms=200, return_seconds=True)
        return [(float(t["start"]), float(t["end"])) for t in ts], "silero"
    except ImportError:
        return energy_regions(audio), "energy"


def energy_regions(audio, frame=0.03, above_db=12.0, pad=0.2, min_len=0.25):
    n = int(frame * SR)
    if len(audio) < n:
        return []
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    db = 10 * np.log10((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    floor = np.percentile(db, 10)
    on = db > floor + above_db
    out, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            a, b = max(0.0, i * frame - pad), min(len(audio) / SR, j * frame + pad)
            if b - a >= min_len:
                if out and a <= out[-1][1]:
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
            i = j
        i += 1
    return out


def split_regions(regions, audio=None, max_len=CHUNK_MAX, merge_gap=MERGE_GAP):
    """Speech regions merged across short gaps, and cut where longer than max_len:
    [(start, end, cut_edges)] (cut_edges: {'start', 'end'} where a cut fell in speech)."""
    merged = []
    for a, b in sorted(regions):
        if merged and a - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    pieces = []
    for a, b in merged:
        start, cut_in = a, False
        while b - start > max_len:
            cut = _quietest(audio, start + 20.0, start + max_len) if audio is not None else start + max_len
            pieces.append((start, cut, {"start": cut_in, "end": True}))
            start, cut_in = cut, True
        pieces.append((start, b, {"start": cut_in, "end": False}))
    return pieces


WIN = 3.0          # language check window (s)
STEP = 1.5         # …every STEP seconds
MIN_RUN = 2        # windows in a row that must agree before the language counts as changed


def windows_of(a, b):
    """Start times of the language-check windows over a piece [a, b]."""
    if b - a <= WIN + 0.5:
        return [a]
    n = int((b - a - WIN) // STEP) + 1
    starts = [a + k * STEP for k in range(n)]
    if starts[-1] + WIN < b - 0.5:
        starts.append(b - WIN)
    return starts


def language_runs(times, langs):
    """Window detections → [(start, end, language)]: a change of language counts once
    MIN_RUN windows in a row agree on it with confidence; unsure windows join their
    neighbours. times: the windows' starts; langs: [(code, p)]."""
    sure = [lg if lg and (p or 0) >= LANG_P_MIN else None for lg, p in langs]
    cur = next((x for x in sure if x), None)
    runs = [[times[0], cur]]
    i = 0
    while i < len(sure):
        lg = sure[i]
        if lg and lg != runs[-1][1]:
            agree = all(x in (lg, None) for x in sure[i:i + MIN_RUN]) and sum(1 for x in sure[i:i + MIN_RUN] if x == lg) >= min(MIN_RUN, len(sure) - i)
            if agree:
                # the change happens between the last window of the old language and this one
                prev_end = times[i - 1] + WIN if i else times[i]
                boundary = (times[i] + min(prev_end, times[i] + WIN)) / 2 if i else times[i]
                runs.append([boundary, lg])
        i += 1
    out = []
    for k, (a, lg) in enumerate(runs):
        end = runs[k + 1][0] if k + 1 < len(runs) else times[-1] + WIN
        out.append((a, end, lg))
    return out


def split_by_language(audio, piece, detections, times):
    """A piece (a, b, edges) cut where its language changes, at the quietest 100 ms within
    a second of each change: [((a, b, edges), (language, p))]."""
    a, b, edges = piece
    runs = language_runs(times, detections)
    if len(runs) <= 1:
        lg = runs[0][2] if runs else None
        ps = [p for (l, p) in detections if l == lg and p is not None]
        return [((a, b, edges), (lg, max(ps) if ps else None))]
    cuts = []
    for k in range(1, len(runs)):
        t = runs[k][0]
        lo, hi = max(a + 0.5, t - 1.0), min(b - 0.5, t + 1.0)
        cuts.append(_quietest(audio, lo, hi) if audio is not None and hi > lo else t)
    bounds = [a] + cuts + [b]
    out = []
    for k, (lg_run) in enumerate(runs):
        pa, pb = bounds[k], bounds[k + 1]
        if pb - pa < 0.3:
            continue
        e = {"start": edges["start"] if k == 0 else True, "end": edges["end"] if k == len(runs) - 1 else True}
        ps = [p for (l, p), t in zip(detections, times) if l == lg_run[2] and pa - WIN <= t <= pb and p is not None]
        out.append(((pa, pb, e), (lg_run[2], max(ps) if ps else None)))
    return out


def group_by_language(pieces, langs, max_len=CHUNK_MAX):
    """Neighbouring pieces of one language (p ≥ LANG_P_MIN) packed into chunks of at most
    max_len: [(start, end, edges, language)], language "auto" where detection was unsure.
    A chunk never mixes languages, so the recognizer is never pushed to write one
    language as if it were the other."""
    out = []
    for (a, b, edges), (lang, p) in zip(pieces, langs):
        lang = lang if lang and p is not None and p >= LANG_P_MIN else "auto"
        last = out[-1] if out else None
        if (last and last[3] == lang and lang != "auto" and b - last[0] <= max_len
                and not last[2]["end"] and not edges["start"]):
            out[-1] = (last[0], b, {"start": last[2]["start"], "end": edges["end"]}, lang)
        else:
            out.append((a, b, edges, lang))
    return out


def pack_chunks(regions, audio=None, max_len=CHUNK_MAX, merge_gap=MERGE_GAP):
    """Speech regions → chunks [(start, end, cut_edges)] of at most max_len, cut only in
    the silences between regions; a region longer than max_len is cut at its quietest
    100 ms between 20 s and max_len (cut_edges marks such cuts: 'start'/'end')."""
    merged = []
    for a, b in sorted(regions):
        if merged and a - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    pieces = []
    for a, b in merged:
        start, cut_in = a, False
        while b - start > max_len:
            cut = _quietest(audio, start + 20.0, start + max_len) if audio is not None else start + max_len
            pieces.append((start, cut, {"start": cut_in, "end": True}))
            start, cut_in = cut, True
        pieces.append((start, b, {"start": cut_in, "end": False}))
    chunks = []
    for a, b, edges in pieces:
        if chunks and b - chunks[-1][0] <= max_len and not chunks[-1][2]["end"] and not edges["start"]:
            chunks[-1] = (chunks[-1][0], b, {"start": chunks[-1][2]["start"], "end": edges["end"]})
        else:
            chunks.append((a, b, edges))
    return chunks


def _quietest(audio, lo, hi, win=0.1):
    a, b = int(lo * SR), min(len(audio), int(hi * SR))
    n = int(win * SR)
    if b - a <= n:
        return (lo + hi) / 2
    seg = audio[a:b].astype(np.float64) ** 2
    k = (b - a) // n
    energy = seg[: k * n].reshape(k, n).mean(axis=1)
    i = int(np.argmin(energy))
    return lo + (i + 0.5) * win


# ------------------------------------------------------------------ recognition
def _words_from_tokens(tokens):
    """whisper.cpp tokens → words: a token starting with a space starts a word."""
    words = []
    for t in tokens:
        text = t.get("text", "")
        if not text or (text.startswith("[_") and text.endswith("]")) or text.startswith("<|"):
            continue
        off = t.get("offsets") or {}
        a, b = off.get("from", 0) / 1000.0, off.get("to", 0) / 1000.0
        p = float(t.get("p", 1.0))
        if not words or text[:1].isspace():
            words.append({"start": a, "end": b, "text": text.strip(), "ps": [p]})
        else:
            words[-1]["text"] += text
            words[-1]["end"] = max(words[-1]["end"], b)
            words[-1]["ps"].append(p)
    out = []
    for w in words:
        if w["text"]:
            out.append({"start": w["start"], "end": max(w["end"], w["start"] + 0.05), "text": w["text"],
                        "p": float(np.mean(w["ps"]))})
    return out


def parse_whisper_json(data):
    """A whisper.cpp -ojf result → (language, words, avg_logprob)."""
    lang = ((data.get("result") or {}).get("language")) or (data.get("params") or {}).get("language")
    tokens = [t for seg in data.get("transcription") or [] for t in seg.get("tokens") or []]
    words = _words_from_tokens(tokens)
    ps = [max(1e-6, float(t.get("p", 1.0))) for t in tokens
          if t.get("text") and not t["text"].startswith("[_") and not t["text"].startswith("<|")]
    avg = float(np.mean(np.log(ps))) if ps else -10.0
    return lang, words, avg


class WhisperCli:
    """whisper.cpp's whisper-cli over many chunk files in one run (the model loads once)."""

    def __init__(self, model="large-v3", threads=None):
        self.model = model
        self.binary = whisper_bin()
        self.threads = threads or perf_threads()
        if not self.binary:
            raise RuntimeError("whisper.cpp is not installed. Run: brew install whisper-cpp")
        if not model_path(model).is_file():
            raise RuntimeError(f"The {MODELS[model]['label']} model is missing. Run: "
                               f"clean-audio-subtitles setup --download {'whisper' if model == 'large-v3' else 'ivrit'}")

    def version(self):
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True).stdout
            m = re.search(r"version:\s*(\S+)", out + "")
            return f"whisper.cpp {m.group(1)}" if m else "whisper.cpp"
        except OSError:
            return "whisper.cpp"

    def detect(self, files):
        """The spoken language of each file: [(code, probability)] (whisper -dl, fast)."""
        cmd = [self.binary, "-m", str(model_path(self.model)), "-l", "auto", "-dl",
               "-t", str(self.threads), *[str(f) for f in files]]
        p = procs.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate()
        procs.untrack(p)
        text = out.decode(errors="replace")
        found, current = {}, None
        for line in text.splitlines():
            m = re.search(r"processing '(.+?)'", line)
            if m:
                current = m.group(1)
            m = re.search(r"auto-detected language:\s*(\w+)\s*\(p\s*=\s*([0-9.]+)\)", line)
            if m and current:
                found[current] = (m.group(1), float(m.group(2)))
        return [found.get(str(f), (None, None)) for f in files]

    def run(self, files, language="auto", prompt=None, on_progress=None):
        for f in files:
            Path(str(f) + ".json").unlink(missing_ok=True)
        cmd = [self.binary, "-m", str(model_path(self.model)), "-l", language, "-ojf", "-np",
               "-t", str(self.threads), "-bs", "5", "-sns", "-mc", "64"]
        if prompt:
            cmd += ["--prompt", prompt]
        cmd += [str(f) for f in files]
        p = procs.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        done = 0
        while p.poll() is None:
            time.sleep(0.5)
            n = sum(1 for f in files if Path(str(f) + ".json").exists())
            if n != done and on_progress:
                done = n
                on_progress(done)
        err = p.stderr.read().decode(errors="replace") if p.stderr else ""
        procs.untrack(p)
        if p.returncode != 0:
            raise RuntimeError(f"whisper.cpp failed: {err[-400:]}")
        results = []
        for f in files:
            jp = Path(str(f) + ".json")
            data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
            lang, words, avg = parse_whisper_json(data)
            results.append({"language": lang, "words": words, "avg_logprob": avg, "raw": data})
        if on_progress:
            on_progress(len(files))
        return results


# ------------------------------------------------------------------ the whole step
def _expected_letters(lang, words):
    sl = subs.script_lang(" ".join(w["text"] for w in words))
    return (lang == "en" and sl == "he") or (lang == "he" and sl == "en")


def transcribe(output_dir, source_id, source_path, start, end, *, replace="missing", model="large-v3",
               language="auto", audio=None, audio_start=0.0, channel="mix", duration=None,
               recognizer=None, glossary=None, log=print_info):
    """Transcribe [start, end] of a source into its subtitle document. Returns the result
    dict. recognizer: an object with run(files, language, prompt, on_progress) (tests)."""
    doc_file = subs.doc_path(output_dir, source_id)
    doc = subs.load(doc_file)
    full = duration or (doc.get("source") or {}).get("duration_sec") or end + PAD_SEC
    a = max(0.0, float(start) - PAD_SEC)
    b = min(float(full), float(end) + PAD_SEC)
    parts = [[a, b]] if replace == "all" else subs.uncovered(doc.get("coverage") or [], a, b)
    if not parts:
        print_success("This stretch already has subtitles.")
        result = {"doc": str(doc_file), "run": None, "cues": 0, "flagged": 0, "transcribed_sec": 0}
        emit("result", output=str(doc_file), results=result)
        return result
    rec = recognizer or WhisperCli(model)
    work = Path(tempfile.mkdtemp(prefix="vac_subs_"))
    procs.register_path(str(work))
    run_id = "r" + subs.new_id()[1:]
    try:
        emit("step", index=1, total=3, title="Finding speech")
        log("Finding speech")
        chunks, vad, pieces = [], None, []
        for pa, pb in parts:
            wav = work / f"part_{pa:.0f}.wav"
            extract(audio or source_path, pa, pb - pa, wav, channel, audio_start if audio else 0.0)
            x, _ = sf.read(str(wav), dtype="float32")
            regions, vad = speech_regions(x)
            for ca, cb, edges in split_regions(regions, x):
                f = work / f"piece_{len(pieces):04d}.wav"
                sf.write(str(f), x[int(ca * SR):int(cb * SR)], SR, subtype="PCM_16")
                pieces.append({"x": x, "pa": pa, "a": ca, "b": cb, "edges": edges, "file": f})
        # the language of each stretch of speech, then chunks that never mix languages
        detecting = language == "auto" and bool(pieces) and hasattr(rec, "detect")
        if detecting:
            # the language every 1.5 s, so a short stretch of the other language inside a
            # longer one is found (and not written in the wrong language's words)
            log("Detecting the language of each stretch of speech")
            wins = []
            for k, p in enumerate(pieces):
                for t in windows_of(p["a"], p["b"]):
                    f = work / f"win_{len(wins):05d}.wav"
                    sf.write(str(f), p["x"][int(t * SR):int(min(p["b"], t + WIN) * SR)], SR, subtype="PCM_16")
                    wins.append((k, t, f))
            found = rec.detect([f for _, _, f in wins])
            split_pieces, langs = [], []
            for k, p in enumerate(pieces):
                mine = [(t, d) for (kk, t, _), d in zip(wins, found) if kk == k]
                for (pa, pb, e), lg in split_by_language(p["x"], (p["a"], p["b"], p["edges"]),
                                                         [d for _, d in mine], [t for t, _ in mine]):
                    split_pieces.append({**p, "a": pa, "b": pb, "edges": e})
                    langs.append(lg)
            pieces = split_pieces
        else:
            langs = [(language if language != "auto" else None, 1.0 if language != "auto" else None)] * len(pieces)
        detected = {}
        for p, (lg, pr) in zip(pieces, langs):
            detected.setdefault(lg or "unsure", 0)
            detected[lg or "unsure"] += 1
        by_part = {}
        for p, lg in zip(pieces, langs):
            by_part.setdefault(p["pa"], []).append((p, lg))
        for pa, items in by_part.items():
            x = items[0][0]["x"]
            for ca, cb, edges, lg in group_by_language([(p["a"], p["b"], p["edges"]) for p, _ in items],
                                                      [lg for _, lg in items]):
                f = work / f"chunk_{len(chunks):04d}.wav"
                sf.write(str(f), x[int(ca * SR):int(cb * SR)], SR, subtype="PCM_16")
                chunks.append({"file": f, "offset": pa + ca, "end": pa + cb, "edges": edges, "language": lg,
                               "unsure": detecting and lg == "auto"})
        speech_sec = sum(c["end"] - c["offset"] for c in chunks)
        log(f"{len(chunks)} stretches of speech, {speech_sec:.0f} s in all")
        new_cues, raws, retried = [], [], 0
        if chunks:
            emit("step", index=2, total=3, title="Transcribing")
            total = len(chunks)

            def progress(n):
                emit("progress", prefix="Transcribing", processed_sec=round(speech_sec * n / total, 1),
                     total_sec=round(speech_sec, 1))
            prompt = ", ".join(g for gl in (glossary or []) for g in (gl.get("he"), gl.get("en")) if g)[:200] or None
            # one recognizer run per language (each run loads the model once)
            results = [None] * len(chunks)
            done_n = [0]
            for lg in dict.fromkeys(c["language"] for c in chunks):
                idx = [i for i, c in enumerate(chunks) if c["language"] == lg]

                def step(n, base=done_n[0]):
                    progress(base + n)
                for i, r in zip(idx, rec.run([chunks[i]["file"] for i in idx], lg, prompt, step)):
                    results[i] = {**r, "language": r.get("language") or lg}
                done_n[0] += len(idx)
            # language retry where the detection was unsure or looks wrong
            if language == "auto":
                doubtful = [i for i, r in enumerate(results) if r["words"] and (chunks[i]["unsure"] or
                    (r.get("language_p") is not None and r["language_p"] < LANG_P_MIN)
                    or _expected_letters(r.get("language"), r["words"]))]
                if doubtful:
                    retried = len(doubtful)
                    log(f"Checking the language of {retried} stretch(es) again")
                    files = [chunks[i]["file"] for i in doubtful]
                    he = rec.run(files, "he", prompt, None)
                    en = rec.run(files, "en", prompt, None)
                    for k, i in enumerate(doubtful):
                        best = max((results[i], he[k], en[k]), key=lambda r: r["avg_logprob"])
                        results[i] = {**best, "uncertain": True}
            emit("step", index=3, total=3, title="Building subtitles")
            for c, r in zip(chunks, results):
                words = [{**w, "start": w["start"] + c["offset"], "end": w["end"] + c["offset"]}
                         for w in r["words"]]
                if not words:
                    continue
                cues = subs.build_cues(words, run_id)
                for k, cue in enumerate(cues):
                    if r.get("uncertain"):
                        cue["flags"].append("language_uncertain")
                    if (k == 0 and c["edges"]["start"]) or (k == len(cues) - 1 and c["edges"]["end"]):
                        cue["flags"].append("chunk_edge")
                new_cues += cues
                raws.append({"offset": c["offset"], "language": r.get("language"),
                             "avg_logprob": r["avg_logprob"], "raw": r.get("raw")})
        version = rec.version() if hasattr(rec, "version") else type(rec).__name__

        def write(d):
            d["source"] = {"id": source_id, "path": str(source_path), "duration_sec": full}
            for pa, pb in parts:
                d["cues"] = subs.replace_range(d["cues"], pa, pb,
                                               [c for c in new_cues if pa <= (c["start"] + c["end"]) / 2 < pb])
                d["coverage"] = subs.add_coverage(d.get("coverage") or [], pa, pb)
            d.setdefault("runs", []).append({
                "id": run_id, "kind": "transcribe", "created": subs.now(), "start_sec": a, "end_sec": b,
                "model": model, "tool": version, "language": language,
                "audio": "original" if not audio else Path(audio).name, "vad": vad,
                "chunks": len(chunks), "retried": retried, "detected": detected, "raw": f"raw/{run_id}.json"})
        raw_dir = doc_file.parent / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{run_id}.json").write_text(json.dumps(raws, ensure_ascii=False), encoding="utf-8")
        subs.update(doc_file, write)
        flagged = sum(1 for c in new_cues if c["flags"])
        print_success(f"{len(new_cues)} subtitles ({flagged} to check)")
        result = {"doc": str(doc_file), "run": run_id, "cues": len(new_cues), "flagged": flagged,
                  "transcribed_sec": round(sum(pb - pa for pa, pb in parts), 1)}
        emit("result", output=str(doc_file), results=result)
        return result
    finally:
        procs.unregister_path(str(work))
        shutil.rmtree(work, ignore_errors=True)
