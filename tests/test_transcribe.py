"""Transcription flow with a stand-in recognizer (docs/subtitles-plan.md §5)."""
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from video_audio_cleaner import subs, transcribe as T


def test_pack_chunks_cuts_only_in_silences():
    regions = [(0.0, 5.0), (5.3, 9.0), (12.0, 20.0), (21.0, 33.0)]
    chunks = T.pack_chunks(regions)
    assert [(round(a, 1), round(b, 1)) for a, b, _ in chunks] == [(0.0, 20.0), (21.0, 33.0)]
    long = T.pack_chunks([(0.0, 70.0)])          # no audio: cut at max length
    assert len(long) == 3 and long[0][2]["end"] and long[1][2]["start"] and not long[2][2]["end"]
    assert all(b - a <= T.CHUNK_MAX + 1e-6 for a, b, _ in long)


def test_energy_regions_find_bursts():
    sr = T.SR
    x = np.zeros(sr * 6, np.float32)
    t = np.arange(sr) / sr
    x[sr:2 * sr] = 0.3 * np.sin(2 * np.pi * 300 * t)
    x[4 * sr:5 * sr] = 0.3 * np.sin(2 * np.pi * 300 * t)
    r = T.energy_regions(x)
    assert len(r) == 2 and abs(r[0][0] - 0.8) < 0.1 and abs(r[1][1] - 5.2) < 0.1


def test_words_from_whisper_json():
    data = {"result": {"language": "he"}, "transcription": [{"tokens": [
        {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 1.0},
        {"text": " שלום", "offsets": {"from": 0, "to": 400}, "p": 0.9},
        {"text": " every", "offsets": {"from": 450, "to": 700}, "p": 0.8},
        {"text": "one", "offsets": {"from": 700, "to": 900}, "p": 0.6},
        {"text": "[_TT_45]", "offsets": {"from": 900, "to": 900}, "p": 1.0}]}]}
    lang, words, avg = T.parse_whisper_json(data)
    assert lang == "he" and [w["text"] for w in words] == ["שלום", "everyone"]
    assert words[1]["start"] == 0.45 and words[1]["end"] == 0.9 and words[1]["p"] == pytest.approx(0.7)
    assert avg < 0


class Fake:
    """Recognizes every chunk as two words, in English, unless told otherwise."""
    def __init__(self, langs=None):
        self.calls = []
        self.langs = langs or {}

    def run(self, files, language, prompt, on_progress):
        self.calls.append((len(files), language))
        out = []
        for f in files:
            if language == "auto":
                lang, text, avg = self.langs.get(f.name, ("en", ["hello", "there."], -0.2))
            else:
                lang, text, avg = language, (["שלום", "לכם."] if language == "he" else ["hello", "there."]), \
                    (-0.1 if language == "he" else -0.9)
            words = [{"start": 0.3 + 0.5 * k, "end": 0.7 + 0.5 * k, "text": w, "p": 0.9} for k, w in enumerate(text)]
            out.append({"language": lang, "words": words, "avg_logprob": avg, "raw": {}})
        if on_progress:
            on_progress(len(files))
        return out


@pytest.fixture
def video(tmp_path):
    """20 s: tones (speech-like for the energy detector) at 2–5 s and 12–15 s."""
    sr = 16000
    x = np.zeros(sr * 20, np.float32)
    t = np.arange(3 * sr) / sr
    for s0 in (2, 12):
        x[s0 * sr:(s0 + 3) * sr] = 0.3 * np.sin(2 * np.pi * 220 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
    wav = tmp_path / "v.wav"
    sf.write(wav, x, sr)
    return wav


def test_transcribe_fills_the_document_and_only_the_gaps(tmp_path, video, monkeypatch):
    monkeypatch.setattr(T, "speech_regions", lambda x: (T.energy_regions(x), "energy"))
    lib = tmp_path / "lib"
    rec = Fake()
    r = T.transcribe(lib, "v1", video, 0, 8, recognizer=rec, duration=20)
    doc = subs.load(subs.doc_path(lib, "v1"))
    assert r["cues"] == 1 and doc["coverage"] == [[0.0, 10.0]]
    c = doc["cues"][0]
    assert c["text"] == "hello there." and 1.9 < c["start"] < 2.6 and c["lang"] == "en"
    # the same stretch again: nothing to do; a longer one: only the new part
    assert T.transcribe(lib, "v1", video, 0, 8, recognizer=rec, duration=20)["run"] is None
    T.transcribe(lib, "v1", video, 0, 18, recognizer=rec, duration=20)
    doc = subs.load(subs.doc_path(lib, "v1"))
    assert doc["coverage"] == [[0.0, 20.0]] and len(doc["cues"]) == 2 and len(doc["runs"]) == 2
    # edits survive a "missing" run; "all" replaces
    subs.update(subs.doc_path(lib, "v1"), lambda d: subs.apply_op(d, {"op": "text", "id": d["cues"][0]["id"], "text": "fixed"}))
    T.transcribe(lib, "v1", video, 0, 18, recognizer=rec, duration=20)
    assert subs.load(subs.doc_path(lib, "v1"))["cues"][0]["text"] == "fixed"
    T.transcribe(lib, "v1", video, 0, 8, replace="all", recognizer=rec, duration=20)
    assert subs.load(subs.doc_path(lib, "v1"))["cues"][0]["text"] == "hello there."


def test_language_retry_takes_the_better_guess(tmp_path, video, monkeypatch):
    monkeypatch.setattr(T, "speech_regions", lambda x: (T.energy_regions(x), "energy"))
    # detected as English, but the words are Hebrew letters: tried again both ways
    rec = Fake(langs={"chunk_0000.wav": ("en", ["שלום", "לכם."], -1.5)})
    T.transcribe(tmp_path, "v2", video, 0, 8, recognizer=rec, duration=20)
    assert rec.calls == [(1, "auto"), (1, "he"), (1, "en")]
    c = subs.load(subs.doc_path(tmp_path, "v2"))["cues"][0]
    assert c["text"] == "שלום לכם." and "language_uncertain" in c["flags"]


def test_cancelled_run_leaves_the_document(tmp_path, video, monkeypatch):
    monkeypatch.setattr(T, "speech_regions", lambda x: (T.energy_regions(x), "energy"))

    class Boom(Fake):
        def run(self, *a, **k):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        T.transcribe(tmp_path, "v3", video, 0, 8, recognizer=Boom(), duration=20)
    assert not subs.doc_path(tmp_path, "v3").exists()


@pytest.mark.slow
def test_real_whisper_on_synthetic_speech(tmp_path):
    if not (T.whisper_bin() and T.model_path("large-v3").is_file() and shutil.which("say")):
        pytest.skip("whisper.cpp, its model or macOS `say` is missing")
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "Hello everyone, thank you for coming today."], check=True)
    r = T.transcribe(tmp_path, "real", aiff, 0, 4, duration=4)
    text = " ".join(c["text"] for c in subs.load(subs.doc_path(tmp_path, "real"))["cues"]).lower()
    assert r["cues"] >= 1 and "thank you" in text


def test_language_changes_inside_a_stretch_split_it():
    times = [0, 1.5, 3, 4.5, 6, 7.5, 9]
    dets = [("en", .9), ("en", .8), ("en", .7), ("he", .9), ("he", .95), ("en", .4), ("en", .9)]
    assert T.language_runs(times, dets) == [(0, 5.25, "en"), (5.25, 9.75, "he"), (9.75, 12.0, "en")]
    # one stray window is not a change
    one = [("en", .9), ("he", .9), ("en", .9), ("en", .9), ("en", .9)]
    assert T.language_runs(times[:5], one) == [(0, 9.0, "en")]
    # a 12 s piece: English, then Hebrew from about 5 s, back to English near 10 s
    parts = T.split_by_language(None, (0.0, 12.0, {"start": False, "end": False}), dets, times)
    assert [lg for _, (lg, _) in parts] == ["en", "he", "en"]
    assert parts[0][0][2] == {"start": False, "end": True} and parts[-1][0][2]["end"] is False
    assert all(b > a for (a, b, _), _ in parts)


def test_windows_cover_a_piece():
    assert T.windows_of(0, 2.0) == [0]
    w = T.windows_of(10, 20)
    assert w[0] == 10 and w[1] == 11.5 and w[-1] + T.WIN >= 19.5
