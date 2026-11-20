"""Subtitles: the per-video document and its pure operations (docs/subtitles-plan.md §2, §4).

A source video's subtitles live in OUTPUT_DIR/_subtitles/<source_id>/subtitles.json, in
the video's own time. Each cue holds the spoken ("mixed") text and optional English and
Hebrew translations of that same cue. A project's clips show the cues that fall inside
them (timeline_cues), which is also what export writes.
"""
import contextlib
import fcntl
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
LANGS = ("mixed", "en", "he")
HISTORY_KEEP = 20

# cue layout (§4.4)
LINE = 42
MAX_CHARS = 84
MAX_SEC = 6.0
MIN_SEC = 0.7
PAUSE = 0.5
GAP = 0.04
MIN_CUE = 0.3
READ_CPS = 20.0

_HEB = re.compile(r"[֐-׿]")
_LAT = re.compile(r"[A-Za-zÀ-ɏ]")
_SENTENCE_END = re.compile(r"[.?!…]['\"”’)]*$")
_CLAUSE_END = re.compile(r"[,;:]['\"”’)]*$")


# ------------------------------------------------------------------ paths and I/O
def docs_dir(output_dir):
    return Path(output_dir) / "_subtitles"


def doc_path(output_dir, source_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id or ""):
        raise ValueError("Bad source id")
    return docs_dir(output_dir) / source_id / "subtitles.json"


def glossary_path(output_dir):
    return docs_dir(output_dir) / "glossary.json"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty(source_id=None, path=None, duration=None):
    return {"schema": SCHEMA, "rev": 0,
            "source": {"id": source_id, "path": path, "duration_sec": duration},
            "coverage": [], "runs": [], "cues": []}


def load(path):
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty(path.parent.name)
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unknown subtitles schema {doc.get('schema')}")
    return doc


def _write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name("subtitles.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def update(path, fn):
    """Apply fn(doc) under the document's lock, keep the previous revision in history/,
    write atomically. fn mutates the document (its return value is passed back)."""
    path = Path(path)
    with _locked(path):
        doc = load(path)
        out = fn(doc)
        doc["rev"] = int(doc.get("rev") or 0) + 1
        if path.exists():
            hist = path.parent / "history"
            hist.mkdir(exist_ok=True)
            shutil.copyfile(path, hist / f"rev-{doc['rev'] - 1:06d}.json")
            old = sorted(hist.glob("rev-*.json"))
            for p in old[:-HISTORY_KEEP]:
                p.unlink(missing_ok=True)
        _write_json(path, doc)
        return out


def load_glossary(output_dir):
    try:
        data = json.loads(glossary_path(output_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"he": str(g.get("he") or ""), "en": str(g.get("en") or "")}
            for g in data if isinstance(g, dict)] if isinstance(data, list) else []


def save_glossary(output_dir, items):
    clean = [{"he": str(g.get("he") or "").strip(), "en": str(g.get("en") or "").strip()}
             for g in items or [] if isinstance(g, dict)]
    clean = [g for g in clean if g["he"] or g["en"]]
    p = glossary_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p, clean)
    return clean


def new_id():
    return "c" + secrets.token_hex(4)


def text_hash(text):
    """What a translation remembers of the text it was made from: the text itself
    (normalised), so the browser can compare it too. A translation is out of date when
    the cue's text no longer equals it."""
    return " ".join((text or "").split())


# ------------------------------------------------------------------ language of a text
def script_lang(text):
    """he / en / mixed / other, from the letters (§4.2)."""
    heb = len(_HEB.findall(text or ""))
    lat = len(_LAT.findall(text or ""))
    total = heb + lat
    if not total:
        return "other"
    if lat < 0.1 * total:
        return "he"
    if heb < 0.1 * total:
        return "en"
    return "mixed"


def is_stale(cue, lang):
    tr = (cue.get("tr") or {}).get(lang)
    return bool(tr and tr.get("text") and tr.get("src") != text_hash(cue.get("text")))


# ------------------------------------------------------------------ cue operations (§4.3)
def _sorted(cues):
    return sorted(cues, key=lambda c: (c["start"], c["end"]))


def _index(cues, cue_id):
    for i, c in enumerate(cues):
        if c["id"] == cue_id:
            return i
    raise KeyError(cue_id)


def _round(t):
    return round(float(t), 3)


def make_cue(start, end, text, run=None, flags=()):
    text = (text or "").strip()
    return {"id": new_id(), "start": _round(start), "end": _round(end), "text": text,
            "lang": script_lang(text), "run": run, "edited": False,
            "flags": list(flags), "tr": {}}


def set_text(cues, cue_id, text, lang="mixed"):
    i = _index(cues, cue_id)
    c = cues[i]
    text = (text or "").strip()
    if lang == "mixed":
        if not text:
            raise ValueError("A subtitle cannot be empty; delete it instead")
        c["text"] = text
        c["lang"] = script_lang(text)
        c["edited"] = True
        c["flags"] = [f for f in c.get("flags") or [] if f not in ("low_confidence", "repeated")]
    elif lang in ("en", "he"):
        tr = c.setdefault("tr", {})
        if not text:
            tr.pop(lang, None)
        else:
            tr[lang] = {**(tr.get(lang) or {}), "text": text, "src": text_hash(c["text"]),
                        "edited": True, "flags": []}
    else:
        raise ValueError(f"Unknown language {lang}")
    return cues


def set_time(cues, cue_id, start, end, duration=None):
    cues = _sorted(cues)
    i = _index(cues, cue_id)
    lo = cues[i - 1]["end"] + GAP if i > 0 else 0.0
    hi = cues[i + 1]["start"] - GAP if i + 1 < len(cues) else (duration if duration else float("inf"))
    start, end = max(lo, float(start)), min(hi, float(end))
    if end - start < MIN_CUE:
        raise ValueError("A subtitle must last at least 0.3 s and not overlap its neighbours")
    cues[i]["start"], cues[i]["end"] = _round(start), _round(end)
    return cues


def split(cues, cue_id, at_char=None, at_sec=None):
    cues = _sorted(cues)
    i = _index(cues, cue_id)
    c = cues[i]
    text = c["text"]
    if at_char is None:
        at_char = len(text) // 2
        sp = text.rfind(" ", 0, at_char + 1)
        at_char = sp if sp > 0 else at_char
    a, b = text[:at_char].strip(), text[at_char:].strip()
    if not a or not b:
        raise ValueError("Put the cursor inside the text to split it there")
    if at_sec is None:
        at_sec = c["start"] + (c["end"] - c["start"]) * len(a) / max(1, len(a) + len(b))
    at_sec = min(max(float(at_sec), c["start"] + MIN_CUE / 2), c["end"] - MIN_CUE / 2)
    first = {**c, "end": _round(at_sec - GAP / 2), "text": a, "lang": script_lang(a),
             "edited": True, "tr": {}}
    second = {**make_cue(at_sec + GAP / 2, c["end"], b, c.get("run"), c.get("flags") or []),
              "edited": True}
    cues[i:i + 1] = [first, second]
    return cues


def merge(cues, cue_id):
    cues = _sorted(cues)
    i = _index(cues, cue_id)
    if i + 1 >= len(cues):
        raise ValueError("There is no next subtitle to merge with")
    a, b = cues[i], cues[i + 1]
    text = f"{a['text']} {b['text']}".strip()
    tr = {}
    for lang in ("en", "he"):
        ta, tb = (a.get("tr") or {}).get(lang), (b.get("tr") or {}).get(lang)
        if ta and tb and ta.get("text") and tb.get("text"):
            stale = is_stale(a, lang) or is_stale(b, lang)
            tr[lang] = {"text": f"{ta['text']} {tb['text']}",
                        "src": "stale" if stale else text_hash(text),
                        "edited": bool(ta.get("edited") or tb.get("edited")),
                        "run": ta.get("run"), "flags": []}
    flags = sorted(set(a.get("flags") or []) | set(b.get("flags") or []))
    cues[i:i + 2] = [{**a, "end": b["end"], "text": text, "lang": script_lang(text),
                      "edited": True, "tr": tr, "flags": flags}]
    return cues


def delete(cues, cue_id):
    i = _index(cues, cue_id)
    del cues[i]
    return cues


def add(cues, start, end, text):
    start, end = float(start), float(end)
    if end - start < MIN_CUE:
        raise ValueError("A subtitle must last at least 0.3 s")
    if any(c["start"] < end and c["end"] > start for c in cues):
        raise ValueError("It would overlap another subtitle")
    c = {**make_cue(start, end, text), "edited": True}
    if not c["text"]:
        raise ValueError("A subtitle cannot be empty")
    return _sorted(cues + [c])


def clear_tr(cues, cue_id, lang):
    (cues[_index(cues, cue_id)].get("tr") or {}).pop(lang, None)
    return cues


def replace_range(cues, start, end, new_cues):
    """Cues whose middle is in [start, end) give way to new_cues; neighbours are trimmed."""
    keep = [c for c in cues if not (start <= (c["start"] + c["end"]) / 2 < end)]
    out = _sorted(keep + list(new_cues))
    for i in range(1, len(out)):
        if out[i - 1]["end"] > out[i]["start"] - GAP:
            out[i - 1]["end"] = _round(out[i]["start"] - GAP)
    return [c for c in out if c["end"] - c["start"] >= MIN_CUE / 2]


def add_coverage(coverage, start, end):
    spans = sorted([list(s) for s in coverage] + [[float(start), float(end)]])
    out = []
    for a, b in spans:
        if out and a <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[_round(a), _round(b)] for a, b in out]


def uncovered(coverage, start, end):
    """Parts of [start, end] outside coverage, as [[a, b], …]."""
    out, t = [], float(start)
    for a, b in sorted(coverage):
        if b <= t:
            continue
        if a >= end:
            break
        if a > t:
            out.append([t, min(a, end)])
        t = max(t, b)
    if t < end:
        out.append([t, float(end)])
    return [[_round(a), _round(b)] for a, b in out if b - a > 0.05]


def remove_coverage(coverage, start, end):
    out = []
    for a, b in coverage:
        if b <= start or a >= end:
            out.append([a, b])
            continue
        if a < start:
            out.append([a, start])
        if b > end:
            out.append([end, b])
    return [[_round(a), _round(b)] for a, b in out if b - a > 0.05]


def delete_range(doc, start, end):
    """Remove the cues whose middle is in [start, end) and mark that stretch as not
    transcribed. Returns how many cues went."""
    before = len(doc["cues"])
    doc["cues"] = [c for c in doc["cues"] if not (start <= (c["start"] + c["end"]) / 2 < end)]
    doc["coverage"] = remove_coverage(doc.get("coverage") or [], start, end)
    return before - len(doc["cues"])


def apply_op(doc, op):
    """One edit from the GUI (§6.2). Raises KeyError for an unknown cue, ValueError when
    the edit is impossible."""
    cues = doc["cues"]
    kind = op.get("op")
    dur = (doc.get("source") or {}).get("duration_sec")
    if kind == "text":
        cues = set_text(cues, op["id"], op.get("text", ""), op.get("lang", "mixed"))
    elif kind == "time":
        cues = set_time(cues, op["id"], op["start"], op["end"], dur)
    elif kind == "split":
        cues = split(cues, op["id"], op.get("at_char"), op.get("at_sec"))
    elif kind == "merge":
        cues = merge(cues, op["id"])
    elif kind == "delete":
        cues = delete(cues, op["id"])
    elif kind == "add":
        cues = add(cues, op["start"], op["end"], op.get("text", ""))
    elif kind == "clear_tr":
        cues = clear_tr(cues, op["id"], op["lang"])
    elif kind == "delete_range":
        delete_range(doc, float(op["start"]), float(op["end"]))
        cues = doc["cues"]
    elif kind == "put":
        # a cue put back exactly as it was (undo): its id, times, text and translations
        c = op["cue"]
        if any(x["id"] == c["id"] for x in cues):
            raise ValueError("That subtitle is already there")
        if any(x["start"] < c["end"] - 1e-6 and x["end"] > c["start"] + 1e-6 for x in cues):
            raise ValueError("It would overlap another subtitle")
        cues = cues + [{**make_cue(c["start"], c["end"], c["text"]), **c}]
    elif kind == "cover":
        doc["coverage"] = add_coverage(doc.get("coverage") or [], float(op["start"]), float(op["end"]))
    else:
        raise ValueError(f"Unknown edit {kind!r}")
    doc["cues"] = _sorted(cues)
    return doc


# ------------------------------------------------------------------ building cues (§4.4)
def build_cues(words, run=None):
    """Readable cues from recognized words [{start, end, text, p}] in order."""
    groups, cur = [], []

    def text_of(ws):
        return " ".join(w["text"].strip() for w in ws if w["text"].strip())

    for w in words:
        if not w["text"].strip():
            continue
        if cur:
            gap = w["start"] - cur[-1]["end"]
            too_long = len(text_of(cur + [w])) > MAX_CHARS or w["end"] - cur[0]["start"] > MAX_SEC
            if gap >= PAUSE or _SENTENCE_END.search(cur[-1]["text"].strip()):
                groups.append(cur)
                cur = []
            elif too_long:
                # break at the last clause end, else the longest pause, else here
                cut = None
                for k in range(len(cur) - 1, 0, -1):
                    if _CLAUSE_END.search(cur[k - 1]["text"].strip()):
                        cut = k
                        break
                if cut is None:
                    gaps = [(cur[k]["start"] - cur[k - 1]["end"], k) for k in range(1, len(cur))]
                    best = max(gaps, default=(0, None))
                    cut = best[1] if best[0] >= 0.2 else None
                if cut:
                    groups.append(cur[:cut])
                    cur = cur[cut:]
                else:
                    groups.append(cur)
                    cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    cues = []
    for g in groups:
        text = text_of(g)
        p = sum(float(w.get("p", 1.0)) for w in g) / len(g)
        flags = ["low_confidence"] if p < 0.5 else []
        cues.append(make_cue(g[0]["start"] - 0.05, g[-1]["end"] + 0.15, text, run, flags))
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] - GAP if i + 1 < len(cues) else float("inf")
        if c["end"] - c["start"] < MIN_SEC:
            c["end"] = _round(min(c["start"] + MIN_SEC, nxt))
        c["end"] = _round(min(c["end"], nxt))
        if i:
            c["start"] = _round(max(c["start"], cues[i - 1]["end"] + GAP))
    out = []
    for c in cues:
        if out and len(out) >= 2 and c["text"] == out[-1]["text"] == out[-2]["text"]:
            if "repeated" not in out[-2]["flags"]:
                out[-2]["flags"].append("repeated")
            out.pop()
            continue
        flag_reading(c)
        out.append(c)
    return [c for c in out if c["end"] > c["start"]]


def flag_reading(c):
    secs = max(0.01, c["end"] - c["start"])
    flags = [f for f in c.get("flags") or [] if f not in ("too_fast", "too_long")]
    if len(c["text"]) / secs > READ_CPS:
        flags.append("too_fast")
    if len(c["text"]) > MAX_CHARS:
        flags.append("too_long")
    c["flags"] = flags
    return c


# ------------------------------------------------------------------ lines and SRT (§4.5)
def wrap(text, line=LINE):
    text = (text or "").strip()
    if "\n" in text or len(text) <= line:
        return text
    mid = len(text) / 2
    best = None
    for m in re.finditer(r" ", text):
        i = m.start()
        score = abs(i - mid) - (6 if i > 0 and text[i - 1] in ",;:.?!" else 0)
        if best is None or score < best[0]:
            best = (score, i)
    if best is None:
        return text
    return text[:best[1]].rstrip() + "\n" + text[best[1] + 1:].lstrip()


def _srt_time(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


RLE, PDF = "‫", "‬"


def to_srt(items, direction_marks=False):
    out = []
    for n, it in enumerate(items, 1):
        lines = wrap(it["text"]).split("\n")
        if direction_marks:
            lines = [f"{RLE}{ln}{PDF}" if _HEB.match(ln.lstrip()) else ln for ln in lines]
        out.append(f"{n}\n{_srt_time(it['start'])} --> {_srt_time(it['end'])}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def from_srt(text):
    text = (text or "").lstrip("﻿").replace("\r\n", "\n")
    items = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        for k, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                body = "\n".join(lines[k + 1:]).replace(RLE, "").replace(PDF, "").strip()
                items.append({"start": start, "end": end, "text": body})
                break
    return items


# ------------------------------------------------------------------ onto the timeline (§4.6)
def _owned(clips):
    """[(clip, start_on_timeline, owned_from, owned_to)], crossfades split in the middle:
    export's own rule (export.owned_spans), so subtitles land where the export puts clips."""
    from . import export
    return export.owned_spans({"tracks": {"video": list(clips)}})


def _item_text(cue, lang):
    if lang == "mixed" or cue.get("lang") == lang:
        return cue["text"], False
    tr = (cue.get("tr") or {}).get(lang) or {}
    if tr.get("text"):
        return tr["text"], False
    return cue["text"], True


def timeline_cues(clips, docs, lang="mixed"):
    """The cues the timeline shows, on the timeline's clock (§4.6). clips: the project's
    video clips in order; docs: {source_id: document}."""
    items = []
    for c, start, own_a, own_b in _owned(clips):
        if (c.get("audio") or {}).get("mute"):
            continue
        doc = docs.get(c["source"])
        if not doc:
            continue
        s0 = float(c["in_sec"]) + (own_a - start)
        s1 = float(c["in_sec"]) + (own_b - start)
        for cue in doc.get("cues") or []:
            a, b = max(cue["start"], s0), min(cue["end"], s1)
            need = min(0.4, (cue["end"] - cue["start"]) / 2)
            if b - a < need - 1e-6:
                continue
            ta, tb = start + (a - float(c["in_sec"])), start + (b - float(c["in_sec"]))
            text, missing = _item_text(cue, lang)
            items.append({"start": ta, "end": tb, "text": text, "cue": cue["id"],
                          "source": c["source"], "clip": c.get("id"), "missing": missing,
                          "stale": lang != "mixed" and not missing and is_stale(cue, lang)})
    items.sort(key=lambda it: it["start"])
    out = []
    for it in items:
        # one line running on across a join (a split shot): one subtitle, not two
        if out and out[-1]["cue"] == it["cue"] and out[-1]["source"] == it["source"] \
                and it["start"] - out[-1]["end"] < 0.1:
            out[-1]["end"] = max(out[-1]["end"], it["end"])
            continue
        if out and it["start"] < out[-1]["end"] + GAP:
            out[-1]["end"] = it["start"] - GAP
            if out[-1]["end"] - out[-1]["start"] < MIN_CUE:
                out.pop()
        if it["end"] - it["start"] >= MIN_CUE:
            out.append(it)
    for it in out:
        it["start"], it["end"] = _round(it["start"]), _round(it["end"])
    return out


def completeness(clips, docs, lang="mixed"):
    items = timeline_cues(clips, docs, lang)
    uncovered_sec = 0.0
    for c, start, own_a, own_b in _owned(clips):
        if (c.get("audio") or {}).get("mute"):
            continue
        cov = (docs.get(c["source"]) or {}).get("coverage") or []
        s0 = float(c["in_sec"]) + (own_a - start)
        s1 = float(c["in_sec"]) + (own_b - start)
        uncovered_sec += sum(b - a for a, b in uncovered(cov, s0, s1))
    return {"cues": len(items), "missing": sum(1 for i in items if i["missing"]),
            "stale": sum(1 for i in items if i["stale"]),
            "translated": sum(1 for i in items if not i["missing"]),
            "uncovered_sec": round(uncovered_sec, 1)}


def summary(doc):
    """A small summary for the library scan."""
    cues = doc.get("cues") or []
    langs = {}
    for lang in ("en", "he"):
        need = [c for c in cues if c.get("lang") != lang]
        done = [c for c in need if ((c.get("tr") or {}).get(lang) or {}).get("text")]
        langs[lang] = {"done": len(done), "of": len(need),
                       "stale": sum(1 for c in done if is_stale(c, lang))}
    return {"rev": doc.get("rev", 0), "coverage": doc.get("coverage") or [], "cues": len(cues),
            "edited": sum(1 for c in cues if c.get("edited")),
            "flagged": sum(1 for c in cues if c.get("flags")), "langs": langs}
