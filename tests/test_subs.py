import json
from pathlib import Path

import pytest

from video_audio_cleaner import subs

TABLE = json.loads((Path(__file__).parent / "data" / "subtitle_mapping.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda c: c["name"])
def test_timeline_mapping_table(case):
    got = subs.timeline_cues(case["clips"], TABLE["docs"], case["lang"])
    rows = [[it["start"], it["end"], it["text"], it["cue"]]
            + ([it["missing"], it["stale"]] if case["lang"] != "mixed" else []) for it in got]
    assert rows == case["expect"]


def test_script_lang():
    assert subs.script_lang("שלום לכולם") == "he"
    assert subs.script_lang("hello everyone") == "en"
    assert subs.script_lang("שלום everyone, תודה") == "mixed"
    assert subs.script_lang("12:30 !") == "other"
    assert subs.script_lang("אני ב-Google") == "mixed"


def _cues():
    return [subs.make_cue(1, 3, "first line"), subs.make_cue(4, 6, "second line here"),
            subs.make_cue(7, 9, "שורה שלישית")]


def test_text_edit_makes_translation_stale_and_edit_of_translation_does_not():
    cues = _cues()
    cid = cues[0]["id"]
    subs.set_text(cues, cid, "a translation", "he")
    assert not subs.is_stale(cues[0], "he") and cues[0]["tr"]["he"]["edited"]
    subs.set_text(cues, cid, "first line, fixed")
    assert subs.is_stale(cues[0], "he") and cues[0]["edited"]
    with pytest.raises(ValueError):
        subs.set_text(cues, cid, "  ")


def test_time_split_merge_add_delete():
    cues = _cues()
    a, b, c = (x["id"] for x in cues)
    subs.set_time(cues, b, 2.5, 6.5)                    # clamped to the neighbours
    assert cues[1]["start"] == pytest.approx(3.04) and cues[1]["end"] == pytest.approx(6.5)
    with pytest.raises(ValueError):
        subs.set_time(cues, b, 5, 5.1)                  # too short
    cues = subs.split(cues, b, at_char=len("second line"))
    assert [x["text"] for x in cues] == ["first line", "second line", "here", "שורה שלישית"]
    assert cues[1]["end"] < cues[2]["start"]
    cues = subs.merge(cues, b)
    assert cues[1]["text"] == "second line here"
    cues = subs.add(cues, 10, 11, "added")
    with pytest.raises(ValueError):
        subs.add(cues, 8.5, 10.5, "overlaps")
    cues = subs.delete(cues, a)
    assert [x["text"] for x in cues][0] == "second line here"
    with pytest.raises(KeyError):
        subs.delete(cues, "nope")


def test_merge_keeps_translations_only_when_both_have_one():
    cues = _cues()
    for x in cues[:2]:
        subs.set_text(cues, x["id"], "t-" + x["text"], "en")
    subs.set_text(cues, cues[0]["id"], "only one", "he")
    cues = subs.merge(cues, cues[0]["id"])
    assert cues[0]["tr"]["en"]["text"] == "t-first line t-second line here"
    assert not subs.is_stale(cues[0], "en") and "he" not in cues[0]["tr"]


def test_replace_range_and_coverage():
    cues = _cues()
    new = [subs.make_cue(3.5, 6.5, "replacement")]
    out = subs.replace_range(cues, 3.5, 6.5, new)
    assert [x["text"] for x in out] == ["first line", "replacement", "שורה שלישית"]
    cov = subs.add_coverage([[0, 10]], 20, 30)
    cov = subs.add_coverage(cov, 9, 21)
    assert cov == [[0, 30]]
    assert subs.uncovered([[0, 10], [20, 30]], 5, 25) == [[10, 20]]
    assert subs.uncovered([], 5, 6) == [[5, 6]]


def test_build_cues_breaks_at_pauses_and_sentences():
    w = lambda a, b, t, p=0.9: {"start": a, "end": b, "text": t, "p": p}
    words = [w(0.0, 0.3, "Hello"), w(0.35, 0.6, "everyone."), w(0.7, 1.0, "Welcome"),
             w(1.05, 1.3, "to"), w(1.35, 1.6, "the"), w(1.65, 2.0, "wedding"),
             w(3.0, 3.3, "שלום", 0.2), w(3.35, 3.7, "לכולם", 0.3)]
    cues = subs.build_cues(words)
    assert [c["text"] for c in cues] == ["Hello everyone.", "Welcome to the wedding", "שלום לכולם"]
    assert cues[2]["flags"] == ["low_confidence"] and cues[2]["lang"] == "he"
    for x, y in zip(cues, cues[1:]):
        assert x["end"] <= y["start"] - subs.GAP + 1e-9
    long = [w(i * 0.3, i * 0.3 + 0.25, f"word{i}") for i in range(40)]
    assert all(len(c["text"]) <= subs.MAX_CHARS for c in subs.build_cues(long))


def test_srt_round_trip_keeps_hebrew_in_logical_order():
    items = [{"start": 1.5, "end": 3.25, "text": "שלום everyone, ברוכים הבאים לחתונה של אופיר ושל טס היום"},
             {"start": 3661.0, "end": 3662.5, "text": "line one\nline two"}]
    srt = subs.to_srt(items)
    assert "00:00:01,500 --> 00:00:03,250" in srt and "01:01:01,000 --> 01:01:02,500" in srt
    back = subs.from_srt(srt)
    assert back[0]["text"].replace("\n", " ") == items[0]["text"]
    assert back[1]["text"] == "line one\nline two"
    marked = subs.to_srt(items, direction_marks=True)
    assert subs.RLE in marked and subs.from_srt(marked)[0]["text"] == back[0]["text"]


def test_update_is_atomic_and_keeps_history(tmp_path):
    p = subs.doc_path(tmp_path, "abc123")
    subs.update(p, lambda d: d["cues"].append(subs.make_cue(1, 2, "one")))
    subs.update(p, lambda d: subs.apply_op(d, {"op": "add", "start": 3, "end": 4, "text": "two"}))
    doc = subs.load(p)
    assert doc["rev"] == 2 and [c["text"] for c in doc["cues"]] == ["one", "two"]
    assert len(list((p.parent / "history").glob("rev-*.json"))) == 1
    with pytest.raises(KeyError):
        subs.update(p, lambda d: subs.apply_op(d, {"op": "delete", "id": "missing"}))
    assert subs.load(p)["rev"] == 2                     # a failed edit writes nothing
    with pytest.raises(ValueError):
        subs.doc_path(tmp_path, "../escape")


def test_completeness_counts_gaps_and_translations():
    clips = TABLE["cases"][0]["clips"]
    c = subs.completeness(clips, TABLE["docs"], "en")
    assert c["cues"] == 3 and c["missing"] == 0 and c["stale"] == 1
    assert c["uncovered_sec"] == 0
    c = subs.completeness(clips, {"s": {"cues": [], "coverage": [[10, 15]]}}, "mixed")
    assert c["uncovered_sec"] == pytest.approx(4.5 + 9.5, abs=0.01)   # clip a shows 10–19.5, clip b 40.5–50


def test_delete_range_removes_lines_and_coverage():
    doc = {"cues": [subs.make_cue(1, 3, "a"), subs.make_cue(5, 7, "b"), subs.make_cue(9, 11, "c")],
           "coverage": [[0, 20]]}
    subs.apply_op(doc, {"op": "delete_range", "start": 4, "end": 8})
    assert [c["text"] for c in doc["cues"]] == ["a", "c"] and doc["coverage"] == [[0, 4], [8, 20]]
    assert subs.remove_coverage([[0, 10]], -5, 30) == []


def test_put_restores_a_cue_exactly():
    doc = {"cues": [subs.make_cue(1, 3, "a")], "coverage": []}
    gone = subs.make_cue(5, 7, "b")
    gone["tr"] = {"en": {"text": "B", "src": "b", "edited": True}}
    subs.apply_op(doc, {"op": "put", "cue": gone})
    assert doc["cues"][1] == gone
    with pytest.raises(ValueError):
        subs.apply_op(doc, {"op": "put", "cue": {**gone, "id": "other"}})
