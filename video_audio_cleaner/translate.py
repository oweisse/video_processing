"""Translating the mixed subtitle track into English only or Hebrew only, locally
(docs/subtitles-plan.md §8).

The job starts its own `ollama serve` on a free local port, as a child process, so the
model's memory counts toward the editor's budget and goes away with the job. Cues are
sent passage by passage (a sentence or a few lines, with the passages around them as
context), each line keeping its id; the replies are checked and only the lines that were
due are stored. Nothing here talks to anything but 127.0.0.1.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import procs, subs
from .console import print_info, print_success, print_warning
from .events import emit

PROMPT_VERSION = 1
NAMES = {"en": "English", "he": "Hebrew"}
PASSAGE_CUES = 8
PASSAGE_CHARS = 600
PASSAGE_GAP = 2.0
_DIGITS = re.compile(r"\d+")
_HEB = re.compile(r"[֐-׿]")
_LAT = re.compile(r"[A-Za-z]")


# ------------------------------------------------------------------ our own Ollama server
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Ollama:
    """`ollama serve` as our child, on 127.0.0.1:<free port>."""

    def __init__(self):
        self.binary = shutil.which("ollama")
        if not self.binary:
            raise RuntimeError("Ollama is not installed. Run: brew install ollama")
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def __enter__(self):
        env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{self.port}", OLLAMA_KEEP_ALIVE="5m",
                   OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NUM_PARALLEL="1")
        self.proc = procs.popen([self.binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)   # its own process group: stopping it never stops us
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                self.get("/api/version")
                return self
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        self.__exit__(None, None, None)
        raise RuntimeError("Ollama did not start")

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.post("/api/generate", {"model": getattr(self, "model", ""), "keep_alive": 0}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            procs.terminate(self.proc)
            procs.untrack(self.proc)
            self.proc = None

    def get(self, path, timeout=5):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def post(self, path, body, timeout=600):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def has(self, model):
        names = {m.get("name") for m in self.get("/api/tags").get("models") or []}
        return model in names or (":" not in model and f"{model}:latest" in names)


def model_installed(model):
    with Ollama() as o:
        return o.has(model)


def pull(model):
    from .progress import ProgressReporter
    with Ollama() as o:
        req = urllib.request.Request(o.url + "/api/pull", data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
        rep = None
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                ev = json.loads(line or b"{}")
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                if ev.get("total") and ev.get("completed") is not None:
                    rep = rep or ProgressReporter(f"Downloading {model}", ev["total"] / 1e6)
                    rep.update(ev["completed"] / 1e6)
        if rep:
            rep.finish()


# ------------------------------------------------------------------ passages
def passages(cues):
    """Cues (sorted) → lists of indexes: a sentence or a few lines each."""
    out, cur, chars = [], [], 0
    for i, c in enumerate(cues):
        if cur and (c["start"] - cues[cur[-1]]["end"] >= PASSAGE_GAP or len(cur) >= PASSAGE_CUES
                    or chars + len(c["text"]) > PASSAGE_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(i)
        chars += len(c["text"])
        if subs._SENTENCE_END.search(c["text"].strip()):
            out.append(cur)
            cur, chars = [], 0
    if cur:
        out.append(cur)
    return out


def due(cue, to, mode, replace_edited=False):
    if cue.get("lang") == to:
        return False
    tr = (cue.get("tr") or {}).get(to) or {}
    if tr.get("edited") and not (mode == "all" and replace_edited):
        return False
    if mode == "all":
        return True
    if not tr.get("text"):
        return True
    return mode == "stale" and subs.is_stale(cue, to)


def system_prompt(to):
    name = NAMES[to]
    return (f"You translate subtitle lines of a family video into {name}. Rules: keep the meaning, "
            f"names, numbers and tone. Lines already in {name} stay as they are. Translate every other "
            f"language (Hebrew, English or a mix) into {name}. Translate only the entries of 'lines': the context fields are "
            "there to help you understand them. Return exactly one output line per input line, with the "
            "same id; never merge or drop lines. Words may move between neighbouring lines "
            "of the same sentence so each line reads naturally, but no line may be empty. Keep each line "
            "about as long as the original: subtitles are read fast. Use the glossary's spellings for "
            "names. Do not explain, do not add notes, do not add quotation marks. Keep [unclear] as it is.")


def context_around(cues, groups, k, to, inside=None, context=None):
    """(before, after) for passage k: the two passages before it (with what is already
    translated of them) and the text of the one after. With inside and context, the
    video's lines outside the stretch give way to the edit's neighbours (context)."""
    def usable(pg):
        return inside is None or all(inside(cues[i]) for i in pg)
    before = []
    for pg in groups[max(0, k - 2):k]:
        if not usable(pg):
            continue
        src = " ".join(cues[i]["text"] for i in pg)
        trs = [((cues[i].get("tr") or {}).get(to) or {}).get("text") for i in pg]
        before.append({"source": src, "translation": " ".join(t for t in trs if t) or None})
    nxt = groups[k + 1] if k + 1 < len(groups) else None
    after = " ".join(cues[i]["text"] for i in nxt) if nxt and usable(nxt) else ""
    if context is not None:
        if len(before) < 2:       # the passage is near the start of the stretch
            before = [b for b in (context.get("before") or []) if b.get("source")][-(2 - len(before)):] + before
        if not after:
            after = context.get("after") or ""
    return before, after


def request(cues, idxs, before, after, to, glossary):
    # short ids ("1", "2", …): models copy them back reliably, unlike long random ones
    lines = [{"id": str(k + 1), "text": cues[i]["text"]} for k, i in enumerate(idxs)]
    text = " ".join(cues[i]["text"] for i in idxs) + " " + after
    gl = [g for g in glossary if (g.get("he") and g["he"] in text) or (g.get("en") and g["en"].lower() in text.lower())]
    return {
        "target": NAMES[to],
        "glossary": gl,
        "context_before_do_not_translate": before,
        "lines": lines,
        "context_after_do_not_translate": after,
    }


SCHEMA = {"type": "object", "required": ["lines"], "properties": {"lines": {"type": "array", "items": {
    "type": "object", "required": ["id", "text"],
    "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}}}


def check_reply(sent, reply, to):
    """{id: (text, flags)} or raise ValueError."""
    got = reply.get("lines") if isinstance(reply, dict) else None
    if not isinstance(got, list):
        raise ValueError("no lines")
    ids = [ln["id"] for ln in sent]
    seen = {}
    for item in got:
        if not isinstance(item, dict):
            raise ValueError("a line that is not an object")
        if item.get("id") not in ids:
            continue                # e.g. the context translated too: not asked for, ignored
        if item["id"] in seen:
            raise ValueError(f"line {item['id']} twice")
        seen[item["id"]] = str(item.get("text") or "").strip()
    if set(seen) != set(ids):
        raise ValueError("missing lines")
    out = {}
    for ln in sent:
        t = seen[ln["id"]]
        if not t:
            raise ValueError("an empty line")
        flags = []
        if sorted(_DIGITS.findall(ln["text"])) != sorted(_DIGITS.findall(t)):
            flags.append("number_changed")
        letters_src = _HEB.search(ln["text"]) or _LAT.search(ln["text"])
        if letters_src and not (_HEB.search(t) if to == "he" else _LAT.search(t)):
            flags.append("wrong_language")
        ratio = len(t) / max(1, len(ln["text"]))
        if ratio > 2.5 or ratio < 0.4:
            if ratio > 4 or ratio < 0.2:
                raise ValueError("a line's length is far off")
            flags.append("too_long" if ratio > 1 else "too_short")
        out[ln["id"]] = (t, flags)
    return out


def ollama_chat(server, model):
    def chat(system, user, seed):
        r = server.post("/api/chat", {
            "model": model, "stream": False, "format": SCHEMA,
            "options": {"temperature": 0, "seed": seed, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]})
        return json.loads(r["message"]["content"])
    return chat


# ------------------------------------------------------------------ the job
def translate(output_dir, source_id, to, *, start=None, end=None, mode="missing", model="gemma3:27b",
              replace_edited=False, context=None, chat=None, log=print_info):
    """Translate the due cues of a document (optionally within [start, end]). chat(system,
    user, seed) -> reply dict can be given (tests); else our own Ollama is started.

    context: what the viewer sees around the stretch in the edit, {"before": [{"source",
    "translation"}, …], "after": "text"} (the end of the previous clip, the start of the
    next). Given, it stands in for the video's own lines outside [start, end] as context."""
    doc_file = subs.doc_path(output_dir, source_id)
    doc = subs.load(doc_file)
    cues = sorted(doc.get("cues") or [], key=lambda c: c["start"])
    inside = (lambda c: True) if start is None else (lambda c: c["end"] > start and c["start"] < (end or 1e12))
    groups = passages(cues)
    todo = [g for g in groups if any(inside(cues[i]) and due(cues[i], to, mode, replace_edited) for i in g)]
    if not todo:
        print_success(f"Nothing to translate into {NAMES[to]}.")
        emit("result", output=str(doc_file), results={"translated": 0, "failed": 0, "flagged": 0})
        return {"translated": 0, "failed": 0, "flagged": 0}
    run_id = "r" + subs.new_id()[1:]
    glossary = subs.load_glossary(output_dir)
    stats = {"translated": 0, "failed": 0, "flagged": 0}
    log(f"Translating {sum(len(g) for g in todo)} lines into {NAMES[to]} ({len(todo)} passages)")
    emit("step", index=1, total=1, title=f"Translating into {NAMES[to]}")
    server = None
    try:
        if chat is None:
            server = Ollama().__enter__()
            server.model = model
            if not server.has(model):
                raise RuntimeError(f"The translation model {model} is not installed. Run: "
                                   f"clean-audio-subtitles setup --download translate")
            chat = ollama_chat(server, model)
        system = system_prompt(to)
        g_index = {id(g): k for k, g in enumerate(groups)}
        for n, g in enumerate(todo, 1):
            k = g_index[id(g)]
            before, after = context_around(cues, groups, k, to, inside if context else None, context)
            req = request(cues, g, before, after, to, glossary)
            result = None
            for attempt in range(3):
                try:
                    result = check_reply(req["lines"], chat(system, req, attempt), to)
                    break
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    log(f"  passage {n}: reply not usable ({exc}); trying again" if attempt < 2 else
                        f"  passage {n}: no usable reply; marked for you to check")
            wanted = [i for i in g if inside(cues[i]) and due(cues[i], to, mode, replace_edited)]
            if result is not None:          # back from "1", "2", … to the cues' ids
                result = {cues[i]["id"]: result[str(k + 1)] for k, i in enumerate(g)}

            def store(d, wanted=wanted, result=result):
                by_id = {c["id"]: c for c in d["cues"]}
                for i in wanted:
                    c = by_id.get(cues[i]["id"])
                    if c is None or c["text"] != cues[i]["text"]:
                        continue          # edited meanwhile: leave it for next time
                    if result is None:
                        c.setdefault("tr", {})[to] = {"text": "", "src": subs.text_hash(c["text"]), "run": run_id,
                                                      "edited": False, "flags": ["failed"]}
                    else:
                        text, flags = result[c["id"]]
                        c.setdefault("tr", {})[to] = {"text": text, "src": subs.text_hash(c["text"]), "run": run_id,
                                                      "edited": False, "flags": flags}
                if n == 1:
                    d.setdefault("runs", []).append({"id": run_id, "kind": "translate", "created": subs.now(),
                                                     "to": to, "model": model, "prompt_version": PROMPT_VERSION,
                                                     "mode": mode})
            subs.update(doc_file, store)
            if result is None:
                stats["failed"] += len(wanted)
            else:
                stats["translated"] += len(wanted)
                stats["flagged"] += sum(1 for i in wanted if result[cues[i]["id"]][1])
            emit("progress", prefix="Translating", processed_sec=n, total_sec=len(todo))
        print_success(f"Translated {stats['translated']} lines into {NAMES[to]}"
                      + (f"; {stats['failed']} failed" if stats["failed"] else ""))
        emit("result", output=str(doc_file), results=stats)
        return stats
    finally:
        if server is not None:
            server.__exit__(None, None, None)
