import json

import pytest

from video_audio_cleaner import subs, translate


def _doc(tmp_path, lines):
    p = subs.doc_path(tmp_path, "src1")
    subs.update(p, lambda d: d.__setitem__("cues", [subs.make_cue(a, b, t) for a, b, t in lines]))
    return p


LINES = [(1, 3, "שלום לכולם."), (3.2, 5, "היום אנחנו חוגגים,"), (5.1, 7, "and we are happy."),
         (10, 12, "מתחילים בשעה 6.")]


def fake_chat(prefix="EN:", calls=None, bad=0):
    state = {"bad": bad}

    def chat(system, user, seed):
        if calls is not None:
            calls.append(user)
        if state["bad"]:
            state["bad"] -= 1
            return {"lines": [{"id": "9", "text": "?"}]}
        out = [{"id": ln["id"], "text": f"{prefix} {ln['text']}"} for ln in user["lines"]]
        return {"lines": out + [{"id": "99", "text": "the context, translated by mistake"}]}
    return chat


def test_passages_follow_sentences_and_gaps():
    cues = [subs.make_cue(a, b, t) for a, b, t in LINES]
    assert translate.passages(cues) == [[0], [1, 2], [3]]


def test_translates_due_lines_only_and_keeps_context_out(tmp_path):
    p = _doc(tmp_path, LINES)
    calls = []
    r = translate.translate(tmp_path, "src1", "en", chat=fake_chat("EN:", calls))
    assert r == {"translated": 3, "failed": 0, "flagged": 0}         # the English line is skipped
    cues = subs.load(p)["cues"]
    assert cues[0]["tr"]["en"]["text"] == "EN: שלום לכולם." and "en" not in cues[2]["tr"]
    assert all(not subs.is_stale(c, "en") for c in cues)
    # the passage with an English line still sends it, for the sentence to be whole
    assert [ln["text"] for ln in calls[1]["lines"]] == ["היום אנחנו חוגגים,", "and we are happy."]
    assert calls[1]["context_before_do_not_translate"][0]["source"] == "שלום לכולם."


def test_stale_and_edited(tmp_path):
    p = _doc(tmp_path, LINES)
    translate.translate(tmp_path, "src1", "en", chat=fake_chat())
    doc = subs.load(p)
    a, b = doc["cues"][0]["id"], doc["cues"][1]["id"]
    subs.update(p, lambda d: subs.apply_op(d, {"op": "text", "id": a, "text": "שלום לכולם, ברוכים הבאים."}))
    subs.update(p, lambda d: subs.apply_op(d, {"op": "text", "id": b, "lang": "en", "text": "my own words"}))
    assert translate.translate(tmp_path, "src1", "en", chat=fake_chat())["translated"] == 0      # missing: none
    r = translate.translate(tmp_path, "src1", "en", mode="stale", chat=fake_chat("NEW:"))
    assert r["translated"] == 1
    cues = {c["id"]: c for c in subs.load(p)["cues"]}
    assert cues[a]["tr"]["en"]["text"].startswith("NEW:") and cues[b]["tr"]["en"]["text"] == "my own words"
    translate.translate(tmp_path, "src1", "en", mode="all", chat=fake_chat("ALL:"))
    assert subs.load(p)["cues"][1]["tr"]["en"]["text"] == "my own words"        # hand edits survive
    translate.translate(tmp_path, "src1", "en", mode="all", replace_edited=True, chat=fake_chat("ALL:"))
    assert subs.load(p)["cues"][1]["tr"]["en"]["text"].startswith("ALL:")


def test_bad_replies_retry_then_flag(tmp_path):
    p = _doc(tmp_path, LINES[:1])
    assert translate.translate(tmp_path, "src1", "en", chat=fake_chat(bad=2))["translated"] == 1
    p = _doc(tmp_path, LINES[:1])
    r = translate.translate(tmp_path, "src1", "en", chat=fake_chat(bad=3))
    assert r["failed"] == 1 and subs.load(p)["cues"][0]["tr"]["en"]["flags"] == ["failed"]


def test_check_reply_flags():
    sent = [{"id": "1", "text": "בשעה 6 בערב"}]
    out = translate.check_reply(sent, {"lines": [{"id": "1", "text": "at seven in the evening"}]}, "en")
    assert out["1"][1] == ["number_changed"]
    out = translate.check_reply(sent, {"lines": [{"id": "1", "text": "בשעה 6 בערב"}]}, "en")
    assert "wrong_language" in out["1"][1]
    with pytest.raises(ValueError):
        translate.check_reply(sent, {"lines": []}, "en")
    with pytest.raises(ValueError):
        translate.check_reply(sent, {"lines": [{"id": "1", "text": "a"}, {"id": "1", "text": "b"}]}, "en")


def test_one_clip_reads_the_edits_neighbours_as_context(tmp_path):
    # a clip of 3.2–7 s: the video's own lines outside it give way to the clip before and
    # after it on the timeline
    _doc(tmp_path, LINES)
    calls = []
    ctx = {"before": [{"source": "Previous clip's words.", "translation": "מילים מהקטע הקודם."}],
           "after": "Next clip's words."}
    translate.translate(tmp_path, "src1", "en", start=3.2, end=7, chat=fake_chat("EN:", calls), context=ctx)
    assert len(calls) == 1
    assert calls[0]["context_before_do_not_translate"] == ctx["before"]
    assert calls[0]["context_after_do_not_translate"] == "Next clip's words."
    # without it, the video's own neighbours, as before
    calls.clear()
    translate.translate(tmp_path, "src1", "en", start=3.2, end=7, mode="all", chat=fake_chat("EN:", calls))
    assert calls[0]["context_before_do_not_translate"][0]["source"] == "שלום לכולם."
    assert calls[0]["context_after_do_not_translate"] == "מתחילים בשעה 6."


def test_translate_argv_carries_context(tmp_path):
    from video_audio_cleaner.gui import commands
    argv = commands.translate_argv(tmp_path, "src1", "he", 1, 2, context={"before": [], "after": "שלום"})
    assert json.loads(argv[argv.index("--context") + 1]) == {"before": [], "after": "שלום"}
