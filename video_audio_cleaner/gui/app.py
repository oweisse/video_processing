"""The GUI's HTTP server: JSON API, job events (SSE) and media, on 127.0.0.1 only."""
import asyncio
import json
import os
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, \
    StreamingResponse
from pydantic import BaseModel, Field

from .. import export as export_mod, memory, prep, probe as probe_mod, tune
from ..events import clean as json_safe
from . import commands, library as library_mod, media, projects, settings
from .jobs import JobQueue

STATIC = Path(__file__).parent / "static"
BROWSABLE = {".mp4", ".mov", ".m4v", ".mkv", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".aiff",
             ".ogg"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
API_HEADER = "x-vac"          # cross-site pages cannot send it without a CORS preflight


# ------------------------------------------------------------------ request bodies
class CleanRequest(BaseModel):
    type: Literal["clean"]
    source_id: str
    start_sec: float = Field(0.0, ge=0)
    duration_sec: Optional[float] = Field(None, gt=0)
    autotune: bool = True
    source_kind: Optional[Literal["tape", "digital"]] = None
    separate_music: bool = False
    # The form's choice: None = use separate_music as given (older callers); False = keep
    # the background music (separate it and put it back at its own level); True = remove it.
    remove_music: Optional[bool] = None
    tag: Optional[str] = None
    output: Optional[str] = None
    normalize: bool = True
    overrides: dict = Field(default_factory=dict)


class CandidateRequest(BaseModel):
    type: Literal["clean_candidate"]
    plan_path: str
    window: str
    index: int = Field(ge=0)
    tag: Optional[str] = None


class TuneWindow(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)


class TuneRequest(BaseModel):
    type: Literal["tune"]
    source_id: str
    windows: list[TuneWindow]
    source_kind: Literal["tape", "digital"]
    separate_music: bool = False
    remove_music: Optional[bool] = None


def _fmt_window(start, duration):
    end = None if duration is None else start + duration
    return f"{commands.seconds(start)}s" if end is None else \
        commands.window_label(start, end).replace("-", "–")


# ------------------------------------------------------------------ app
def create_app(output_dir=None, dialogs=None):
    """dialogs: native file dialogs (the pywebview window), or None in a browser."""
    state = {"library": None}

    def open_project(path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            raise HTTPException(400, f"Not a folder: {path}")
        state["library"] = library_mod.Library(path)
        queue.load(path)
        settings.remember("recent_output_dirs", path)
        settings.update(last_output_dir=path)
        return state["library"]

    def lib():
        if state["library"] is None:
            raise HTTPException(409, "No project folder is open.")
        return state["library"]

    def on_finished(job):
        if state["library"] is not None and job["output_dir"] == str(state["library"].output_dir):
            state["library"].rescan()

    queue = JobQueue(on_finished=on_finished)
    queue.cheap_stoppers.append(media.stop_all)

    @asynccontextmanager
    async def lifespan(app):
        queue.start()
        if output_dir:
            open_project(output_dir)
        yield
        await queue.stop()

    app = FastAPI(title="Video Master", lifespan=lifespan)
    app.state.queue = queue
    app.state.project = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS and host != "testserver":
            return PlainTextResponse("Forbidden host", status_code=403)
        path = request.url.path
        # Requests made by another website (or another app on this Mac) in the browser:
        # refused, so a page elsewhere cannot embed your videos (<video src=…/media/…>) or
        # listen to /api/events. Opening a link to the app (a navigation) still works;
        # tools without a browser send no Sec-Fetch-Site and are unaffected.
        if (request.headers.get("sec-fetch-site") in ("cross-site", "same-site")
                and request.headers.get("sec-fetch-mode") != "navigate"):
            return PlainTextResponse("Requests from other sites are refused", status_code=403)
        if (path.startswith("/api/") and path != "/api/events"
                and request.headers.get(API_HEADER) != "1"):
            return PlainTextResponse("Missing API header", status_code=403)
        response = await call_next(request)
        # Older browsers without Sec-Fetch-*: they still will not hand our files to another site.
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

    # -------------------------------------------------------------- library
    @app.get("/api/library")
    def get_library():
        if state["library"] is None:
            return {"output_dir": None, "sources": [], "unknown": [],
                    "recent_output_dirs": settings.load().get("recent_output_dirs", [])}
        data = dict(lib().get())
        data["projects"] = projects.list_projects(lib().output_dir)   # saved often: never cached
        data["recent_output_dirs"] = settings.load().get("recent_output_dirs", [])
        data["audio_cache_bytes"] = media.cache_size(lib().output_dir)
        return data

    @app.post("/api/library/rescan")
    def rescan():
        lib().rescan()
        return get_library()

    @app.post("/api/library/open")
    def open_library(body: dict = Body(...)):
        open_project(body.get("output_dir") or "")
        return get_library()

    @app.get("/api/sources/{source_id}/probe")
    async def probe_source(source_id: str):
        source = lib().source(source_id)
        if source is None:
            raise HTTPException(404, "Unknown source")
        if not source["exists"]:
            raise HTTPException(410, "The source file is missing")
        try:
            data = await asyncio.to_thread(library_mod.probe_source, lib().output_dir, source)
        except RuntimeError as exc:
            raise HTTPException(422, str(exc))
        source["probe"] = data
        return data

    @app.post("/api/sources")
    def add_source(body: dict = Body(...)):
        try:
            return lib().add_source(body.get("path") or "")
        except FileNotFoundError:
            raise HTTPException(404, f"No such file: {body.get('path')}")

    @app.post("/api/sources/many")
    def add_sources(body: dict = Body(...)):
        """Several videos at once: {"paths": [...]} and/or {"folder": path}."""
        try:
            return lib().add_sources(body.get("paths") or [], body.get("folder"))
        except FileNotFoundError as exc:
            raise HTTPException(404, f"No videos found: {exc}")

    @app.get("/api/memory")
    async def memory_now():
        """What the editor (the server and everything it started) uses, against its budget."""
        used = await asyncio.to_thread(memory.our_usage)
        return {"used": used, "budget": memory.budget_bytes(), "total": memory.total_bytes(),
                "machine_used": memory.machine_used_fraction()}

    @app.get("/api/app")
    def app_info():
        from .. import __version__
        return {"native": dialogs is not None, "version": __version__}

    @app.get("/api/fs/pick")
    async def fs_pick(kind: Literal["file", "dir", "audio"] = "file", start: Optional[str] = None,
                      multiple: bool = False):
        if dialogs is None:
            raise HTTPException(501, "No native dialogs in a browser; use /api/fs/list.")
        paths = await asyncio.to_thread(dialogs.pick, kind, start, multiple)
        paths = list(paths or [])
        return {"path": paths[0] if paths else None, "paths": paths}

    @app.get("/api/fs/list")
    def fs_list(dir: str = Query("~")):
        home = os.path.realpath(os.path.expanduser("~"))
        folder = os.path.realpath(os.path.expanduser(dir))
        if not (folder == home or folder.startswith(home + os.sep)) or not os.path.isdir(folder):
            raise HTTPException(400, "Only folders inside your home folder can be browsed.")
        entries = []
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError as exc:
            raise HTTPException(403, str(exc))
        for name in names:
            if name.startswith("."):
                continue
            path = os.path.join(folder, name)
            is_dir = os.path.isdir(path)
            if not is_dir and Path(name).suffix.lower() not in BROWSABLE:
                continue
            try:
                size = None if is_dir else os.path.getsize(path)
            except OSError:
                size = None
            entries.append({"name": name, "path": path, "is_dir": is_dir, "size": size})
        parent = os.path.dirname(folder) if folder != home else None
        return {"dir": folder, "parent": parent, "entries": entries}

    @app.post("/api/reveal")
    def reveal(body: dict = Body(...)):
        path = body.get("path") or ""
        if not lib().allowed(path) or not os.path.exists(path):
            raise HTTPException(403, "Not a project file")
        subprocess.run(["open", "-R", path], check=False)
        return {"ok": True}

    # -------------------------------------------------------------- outputs and plans
    @app.post("/api/outputs/{name}/delete")
    def delete_output(name: str):
        out = lib().output(name)
        if out is None:
            raise HTTPException(404, "Unknown output")
        trash = lib().output_dir / "_trash"
        trash.mkdir(exist_ok=True)
        moved = []
        for path in (out["path"], out["sidecar"]):
            if path and os.path.exists(path):
                dst = trash / Path(path).name
                if dst.exists():
                    dst = trash / f"{Path(path).stem}.{os.getpid()}{Path(path).suffix}"
                shutil.move(path, dst)
                moved.append(str(dst))
        from . import rename
        projects_changed = rename.retarget_clips(lib().output_dir, out["path"], None)
        _announce_projects(projects_changed)
        lib().rescan()
        return {"moved": moved, "projects": projects_changed}

    @app.get("/api/outputs/{name}/used-by")
    def output_used_by(name: str):
        """The project clips that play this output (asked before deleting it)."""
        out = lib().output(name)
        if out is None:
            raise HTTPException(404, "Unknown output")
        used = []
        for item in projects.list_projects(lib().output_dir):
            try:
                p = projects.load(lib().output_dir, item["name"])
            except (OSError, ValueError):
                continue
            n = sum(1 for c in (p.get("tracks") or {}).get("video") or []
                    if (c.get("audio") or {}).get("output") == name)
            if n:
                used.append({"project": item["name"], "clips": n})
        return {"used_by": used}

    # -------------------------------------------------------------- subtitles
    _setup_cache = {"at": 0.0, "value": None}

    @app.get("/api/subtitles/setup")
    async def subtitles_setup(fresh: bool = False):
        from .. import subtitles_cli
        if fresh or time.time() - _setup_cache["at"] > 30 or _setup_cache["value"] is None:
            _setup_cache["value"] = await asyncio.to_thread(subtitles_cli.check)
            _setup_cache["at"] = time.time()
        return _setup_cache["value"]

    def _subs_path(source_id):
        from .. import subs
        if lib().source(source_id) is None:
            raise HTTPException(404, "Unknown source")
        return subs.doc_path(lib().output_dir, source_id)

    @app.get("/api/subtitles/glossary")
    def get_glossary():
        from .. import subs
        return subs.load_glossary(lib().output_dir)

    @app.put("/api/subtitles/glossary")
    def put_glossary(body: list = Body(...)):
        from .. import subs
        return subs.save_glossary(lib().output_dir, body)

    @app.get("/api/subtitles/{source_id}")
    def get_subtitles(source_id: str, start: Optional[float] = None, end: Optional[float] = None):
        from .. import subs
        doc = subs.load(_subs_path(source_id))
        if start is not None and end is not None:
            doc = {**doc, "cues": [c for c in doc["cues"] if c["end"] > start and c["start"] < end]}
        return doc

    @app.post("/api/subtitles/{source_id}/edit")
    def edit_subtitles(source_id: str, body: dict = Body(...)):
        """Edits applied in order under the document's lock; all or none."""
        from .. import subs
        ops = body.get("ops") or []
        path = _subs_path(source_id)
        before = {c["id"]: c for c in subs.load(path)["cues"]}

        def apply(doc):
            for op in ops:
                subs.apply_op(doc, op)
        try:
            subs.update(path, apply)
        except KeyError as exc:
            raise HTTPException(404, f"No subtitle {exc.args[0]}; reload and try again")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        doc = subs.load(path)
        src = lib().source(source_id)
        if src is not None:          # the library's cached summary follows the edit
            src["subtitles"] = subs.summary(doc)
        after = {c["id"]: c for c in doc["cues"]}
        changed = [c for i, c in after.items() if before.get(i) != c]
        return {"rev": doc["rev"], "cues": changed, "removed": [i for i in before if i not in after],
                "coverage": doc.get("coverage") or []}

    def _translate_context(c):
        """The edit's lines around a stretch (from the editor), kept short and plain."""
        if not isinstance(c, dict):
            return None
        before = [{"source": str(b.get("source") or "")[:600], "translation": (str(b["translation"])[:600] if b.get("translation") else None)}
                  for b in (c.get("before") or [])[-2:] if isinstance(b, dict) and b.get("source")]
        after = str(c.get("after") or "")[:600]
        return {"before": before, "after": after} if before or after else None

    def _project_subtitles(name):
        from .. import subs
        project = projects.load(lib().output_dir, name)
        clips = (project.get("tracks") or {}).get("video") or []
        docs = {sid: subs.load(subs.doc_path(lib().output_dir, sid)) for sid in {c["source"] for c in clips}}
        return clips, docs

    @app.post("/api/projects/{name}/levels")
    async def project_levels(name: str, body: dict = Body(...)):
        """Each clip's automatic level (export.level_clips): {clip id: {lufs, gain_db}}.
        body: {"video": clips} as the editor has them now (the saved project's sources
        are used); measured once and cached in levels.json beside the project."""
        from .. import export
        path = projects.path(lib().output_dir, name)
        if not path.is_file():
            raise HTTPException(404, "Unknown project")
        proj = projects.load(lib().output_dir, name)
        proj["tracks"] = {**proj.get("tracks", {}), "video": body.get("video") or []}
        target = float(export.settings(proj)["target_lufs"])
        try:
            return await asyncio.to_thread(export.project_levels, proj, path, target)
        except (export.ProjectError, KeyError) as e:
            raise HTTPException(422, str(e))

    @app.get("/api/projects/{name}/subtitles")
    def project_subtitles(name: str, lang: str = "mixed"):
        from .. import subs
        if not projects.path(lib().output_dir, name).is_file():
            raise HTTPException(404, "Unknown project")
        clips, docs = _project_subtitles(name)
        if lang == "all":
            return {"completeness": {lg: subs.completeness(clips, docs, lg) for lg in subs.LANGS}}
        if lang not in subs.LANGS:
            raise HTTPException(422, "lang is mixed, en, he or all")
        return {"items": subs.timeline_cues(clips, docs, lang), "completeness": subs.completeness(clips, docs, lang)}

    @app.get("/api/projects/{name}/subtitles.srt")
    def project_subtitles_srt(name: str, lang: str = "mixed"):
        from .. import subs
        if not projects.path(lib().output_dir, name).is_file():
            raise HTTPException(404, "Unknown project")
        if lang not in subs.LANGS:
            raise HTTPException(422, "lang is mixed, en or he")
        clips, docs = _project_subtitles(name)
        body = subs.to_srt(subs.timeline_cues(clips, docs, lang))
        return PlainTextResponse(body, media_type="application/x-subrip",
                                 headers={"Content-Disposition": f'attachment; filename="{lang}.srt"'})

    @app.get("/api/storage")
    async def storage_usage(project: Optional[str] = None):
        """Disk used per video (original, editing copies, tuning audio, rendered versions),
        for one project's videos or the whole library."""
        from . import storage
        if project and not projects.path(lib().output_dir, project).is_file():
            raise HTTPException(404, "Unknown project")
        return await asyncio.to_thread(storage.usage, lib().output_dir, lib().get(), project)

    @app.post("/api/trash/empty")
    def empty_trash():
        """Delete what the library's _trash holds, for good."""
        from . import storage
        trash = lib().output_dir / "_trash"
        freed = storage.du(trash)
        if trash.is_dir():
            shutil.rmtree(trash)
        return {"freed": freed}

    @app.post("/api/outputs/{name}/rename")
    def rename_output(name: str, body: dict = Body(...)):
        """Change a cleaned version's label (the tag in its file name)."""
        from . import rename
        out = lib().output(name)
        if out is None:
            raise HTTPException(404, "Unknown output")
        if any(j["state"] in ("queued", "running") and (j.get("result") or {}).get("output") == out["path"]
               for j in queue.jobs.values()):
            raise HTTPException(409, "It is still being written")
        try:
            target, updated = rename.rename_output(lib().output_dir, out["path"], body.get("tag"))
        except FileExistsError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        _announce_projects(updated)
        lib().rescan()
        return {"file": target.name, "path": str(target), "projects": updated}

    @app.get("/api/outputs/preview-name")
    def preview_name(source_id: str, start_sec: float = 0.0,
                     duration_sec: Optional[float] = None, tag: Optional[str] = None):
        source = lib().source(source_id)
        if source is None:
            raise HTTPException(404, "Unknown source")
        file_duration = (source.get("probe") or {}).get("duration_sec")
        name = commands.preview_name(source["path"], start_sec, duration_sec, file_duration,
                                     tag or None)
        return {"name": name, "exists": (lib().output_dir / name).exists()}

    def _plan_path(path):
        path = os.path.realpath(path)
        root = os.path.realpath(lib().output_dir / "_tuning")
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise HTTPException(403, "Not a tuning plan of this project")
        return Path(path)

    @app.get("/api/plans/candidate_settings")
    def get_candidate_settings(plan: str, window: str, index: int = Query(ge=0)):
        """Every setting one tuned candidate stands for (what rendering it would use)."""
        from ..candidate_settings import candidate_settings
        try:
            return candidate_settings(_plan_path(plan), window, index)
        except RuntimeError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/plans/pick")
    def set_pick(body: dict = Body(...)):
        plan = _plan_path(body.get("plan_path") or "")
        try:
            entry = tune.set_user_pick(plan, body.get("window"), body.get("index"))
        except (StopIteration, IndexError, ValueError) as exc:
            raise HTTPException(400, f"Could not set the pick: {exc}")
        lib().rescan()
        return json_safe(entry)

    @app.post("/api/plans/candidates/delete")
    def delete_candidates(body: dict = Body(...)):
        plan_path = _plan_path(body.get("plan_path") or "")
        label = body.get("window")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        entry = next((w for w in plan["windows"] if w["label"] == label), None)
        if entry is None:
            raise HTTPException(404, "Unknown window")
        files = entry.get("candidate_files")
        if files:
            shutil.rmtree(plan_path.parent / files["dir"], ignore_errors=True)
        entry["candidate_files"] = None
        tmp = plan_path.with_name("plan.json.partial")
        tmp.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, plan_path)
        lib().rescan()
        return {"ok": True}

    @app.post("/api/plans/window/delete")
    def delete_tuning_window(body: dict = Body(...)):
        """Remove one tuned window (a blue bar) from its tuning run: its measurements, its
        kept candidate audio and its listening file go to the library's _trash. A run left
        with no windows goes there whole. Rendered versions stay."""
        plan_path = _plan_path(body.get("plan_path") or "")
        label = body.get("window")
        if any(j["state"] in ("queued", "running") and j["type"] in ("tune", "clean", "clean_candidate")
               and str(plan_path.parent) in " ".join(j["argv"]) for j in queue.jobs.values()):
            raise HTTPException(409, "A job is still using this tuning run")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        entry = next((w for w in plan["windows"] if w["label"] == label), None)
        if entry is None:
            raise HTTPException(404, "Unknown window")
        trash = lib().output_dir / "_trash" / f"{plan_path.parent.name}.{os.getpid()}.{int(time.time())}"
        def to_trash(path):
            if path.exists():
                trash.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(trash / path.name))
        plan["windows"] = [w for w in plan["windows"] if w is not entry]
        if not plan["windows"]:
            to_trash(plan_path.parent)
        else:
            files = entry.get("candidate_files")
            if files:
                to_trash(plan_path.parent / files["dir"])
            to_trash(plan_path.parent / "listen" / f"{label}.wav")
            tmp = plan_path.with_name("plan.json.partial")
            tmp.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, plan_path)
        lib().rescan()
        return {"ok": True, "trash": str(trash)}

    @app.get("/api/cli/schema")
    def cli_schema():
        return commands.cli_schema()

    # -------------------------------------------------------------- jobs
    def _source(source_id):
        source = lib().source(source_id)
        if source is None:
            raise HTTPException(404, "Unknown source")
        if not source["exists"]:
            raise HTTPException(410, f"The source file is missing: {source['path']}")
        return source

    @app.get("/api/jobs")
    def list_jobs():
        return [{k: v for k, v in j.items()} for j in
                queue.list(None if state["library"] is None else lib().output_dir)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = queue.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return job

    @app.get("/api/jobs/{job_id}/log")
    def get_log(job_id: str, tail: int = 400):
        job = queue.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        try:
            lines = Path(job["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        return PlainTextResponse("\n".join(lines[-tail:]))

    @app.post("/api/jobs/preview")
    def preview_job(body: dict = Body(...)):
        """The command a request would run, without queuing it (for the form)."""
        _, _, argv, _ = _build(body)
        return {"argv": argv, "display": commands.display(argv)}

    @app.post("/api/jobs")
    def create_job(body: dict = Body(...)):
        kind, title, argv, meta = _build(body)
        return queue.submit(kind, title, argv, lib().output_dir, commands.display(argv), meta)

    def _stretch_sec(source, start, duration):
        """How long a stretch of a source is, for estimating a job's memory."""
        if duration:
            return float(duration)
        total = ((source.get("probe") or {}).get("duration_sec") or 0.0)
        return max(0.0, total - float(start or 0.0)) if total else 3600.0

    def _build(body):
        kind = body.get("type")
        out_dir = lib().output_dir
        try:
            if kind == "clean":
                req = CleanRequest(**body)
                source = _source(req.source_id)
                if req.remove_music is not None:
                    req.separate_music = not req.remove_music
                argv = commands.clean_argv(
                    source["path"], out_dir, req.start_sec, req.duration_sec, req.autotune,
                    req.source_kind, req.separate_music, (req.tag or "").strip() or None,
                    (req.output or "").strip() or None, req.normalize, req.overrides,
                    commands.KEEP_MUSIC_LEVEL_DB if req.remove_music is False else None)
                bits = [_fmt_window(req.start_sec, req.duration_sec)]
                if req.autotune:
                    bits.append(f"autotune · {req.source_kind}")
                if req.remove_music:
                    bits.append("music removed")
                elif req.separate_music:
                    bits.append("music kept" if req.remove_music is False else "music")
                if req.tag:
                    bits.append(req.tag)
                title = f"Clean {source['name']} " + " · ".join(bits)
                meta = {"source_id": source["id"], **req.model_dump(),
                        "memory_gb": memory.clean_estimate_gb(_stretch_sec(source, req.start_sec, req.duration_sec))}
            elif kind == "clean_candidate":
                req = CandidateRequest(**body)
                plan_path = _plan_path(req.plan_path)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                tag = (req.tag or "").strip() or f"cand{req.index}"
                argv = commands.candidate_argv(plan["source"], out_dir, plan_path, req.window,
                                               req.index, tag)
                title = f"Render candidate {req.index} of {Path(plan['source']).name} " \
                        f"{req.window}"
                win = next((w for w in plan.get("windows") or [] if w.get("label") == req.window
                            or w.get("name") == req.window), None) if isinstance(plan, dict) else None
                length = (win.get("end_sec", 0) - win.get("start_sec", 0)) if win else 600
                meta = {**req.model_dump(), "memory_gb": memory.clean_estimate_gb(length)}
            elif kind == "tune":
                req = TuneRequest(**body)
                source = _source(req.source_id)
                windows = [w.model_dump() for w in req.windows]
                if any(w["end_sec"] <= w["start_sec"] for w in windows):
                    raise ValueError("Each window must end after it starts")
                if req.remove_music is not None:
                    req.separate_music = not req.remove_music
                argv = commands.tune_argv(source["path"], out_dir, windows, req.source_kind,
                                          req.separate_music,
                                          f"{commands.KEEP_MUSIC_LEVEL_DB:g}" if req.remove_music is False else None)
                title = f"Tune {source['name']} " + ", ".join(
                    commands.window_label(w["start_sec"], w["end_sec"]) for w in windows)
                meta = {"source_id": source["id"], **req.model_dump(), "memory_gb": memory.clean_estimate_gb(
                    max(w["end_sec"] - w["start_sec"] for w in windows))}
            elif kind == "prep":
                source = _source(body.get("source_id") or "")
                kinds = [k for k in body.get("kinds") or ("proxy", "thumbs", "peaks")
                         if k in prep.KINDS]
                if not kinds:
                    raise ValueError("Nothing to prepare")
                argv = commands.prep_argv(source["path"], library_mod.prep_cache(out_dir),
                                          source["id"], kinds)
                title = f"Prepare {source['name']} for editing"
                meta = {"source_id": source["id"], "kinds": kinds, "memory_gb": memory.LIGHT_JOB_GB}
            elif kind == "subtitles":
                source = _source(body.get("source_id") or "")
                start = float(body.get("start_sec") or 0.0)
                dur = body.get("duration_sec")
                end = start + float(dur) if dur else float((source.get("probe") or {}).get("duration_sec") or 0)
                if end <= start:
                    raise ValueError("Choose a stretch to transcribe")
                audio, audio_start = None, 0.0
                choice = body.get("audio") or "original"
                if choice != "original":
                    out = lib().output(choice)
                    if out is None or not out.get("exists"):
                        raise ValueError("That cleaned version is gone")
                    audio, audio_start = out["path"], float((out.get("window") or {}).get("actual_start_sec") or 0)
                argv = commands.subtitles_argv(source["path"], out_dir, source["id"], start, end,
                                               body.get("replace") or "missing", body.get("model") or "large-v3",
                                               body.get("language") or "auto", audio, audio_start,
                                               body.get("channel") or "mix")
                title = f"Subtitles for {source['name']} {_fmt_window(start, end - start)}"
                meta = {"source_id": source["id"], "start_sec": start, "duration_sec": end - start,
                        "memory_gb": memory.SUBTITLES_GB}
            elif kind == "subtitles_translate":
                source = _source(body.get("source_id") or "")
                to = body.get("to")
                if to not in ("en", "he"):
                    raise ValueError("Translate into en or he")
                start = body.get("start_sec")
                dur = body.get("duration_sec")
                end = float(start) + float(dur) if start is not None and dur else None
                argv = commands.translate_argv(out_dir, source["id"], to, start, end,
                                               body.get("mode") or "missing", bool(body.get("replace_edited")),
                                               _translate_context(body.get("context")))
                title = f"Translate {source['name']} to {'English' if to == 'en' else 'Hebrew'}" + \
                        (f" {_fmt_window(float(start), float(dur))}" if end is not None else "")
                meta = {"source_id": source["id"], "to": to, "start_sec": start, "duration_sec": dur,
                        "memory_gb": memory.TRANSLATE_GB}
            elif kind == "subtitles_setup":
                what = body.get("what")
                if what not in ("whisper", "ivrit", "translate"):
                    raise ValueError("Download whisper, ivrit or translate")
                argv = commands.subtitles_setup_argv(what)
                title = {"whisper": "Download the speech recognition model",
                         "ivrit": "Download the Hebrew-tuned speech model",
                         "translate": "Download the translation model"}[what]
                meta = {"what": what, "memory_gb": 0.5}
            elif kind in ("export", "export_estimate"):
                name = body.get("project") or ""
                path = projects.path(out_dir, name)
                if not path.is_file():
                    raise HTTPException(404, "Unknown project")
                estimating = kind == "export_estimate"
                argv = commands.export_argv(path, bool(body.get("reencode")), estimating)
                title = f"{'Estimate' if estimating else 'Export'} {name}"
                meta = {"project": name, "memory_gb": memory.EXPORT_GB}
            else:
                raise HTTPException(400, f"Unknown job type: {kind}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return kind, title, argv, meta

    @app.delete("/api/jobs/{job_id}")
    async def cancel_job(job_id: str):
        try:
            return await queue.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "Unknown job")

    @app.post("/api/jobs/clear")
    async def clear_jobs():
        return {"removed": await queue.clear_finished(lib().output_dir)}

    @app.post("/api/jobs/{job_id}/remove")
    async def remove_job(job_id: str):
        try:
            return {"removed": await queue.remove(job_id)}
        except KeyError:
            raise HTTPException(404, "Unknown job")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        job = queue.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return queue.submit(job["type"], job["title"], job["argv"], job["output_dir"],
                            job.get("display"), job.get("meta"))

    @app.get("/api/events")
    async def events_stream(request: Request):
        async def gen():
            q = queue.subscribe()
            try:
                yield "retry: 2000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        job = await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield "event: ping\ndata: {}\n\n"
                        continue
                    kind = job.pop("__event", "job") if isinstance(job, dict) else "job"
                    yield f"event: {kind}\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
            finally:
                queue.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # -------------------------------------------------------------- projects
    def _project_errors(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"Not found: {exc}")
        except FileExistsError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/projects")
    def list_projects():
        return projects.list_projects(lib().output_dir)

    @app.post("/api/projects")
    def create_project(body: dict = Body(...)):
        project = _project_errors(projects.create, lib().output_dir, body.get("name"))
        lib().rescan()
        return project

    @app.get("/api/projects/{name}")
    def get_project(name: str):
        return _project_errors(projects.load, lib().output_dir, name)

    def _announce_projects(names):
        """Tell open editors these projects changed on disk (they reload or re-apply)."""
        for n in names:
            try:
                rev = projects.load(lib().output_dir, n).get("rev") or 0
            except (OSError, ValueError):
                continue
            queue.announce("project", {"name": n, "rev": rev})

    @app.put("/api/projects/{name}")
    def save_project(name: str, body: dict = Body(...)):
        # "rev": the revision the editor loaded; an older one than on disk is refused (409,
        # with the project as it is now) instead of overwriting what changed since.
        expect = body.pop("rev", None)
        try:
            saved = _project_errors(projects.save, lib().output_dir, name, body, expect)
        except projects.Conflict as c:
            return JSONResponse({"detail": str(c), "project": c.current}, status_code=409)
        queue.announce("project", {"name": name, "rev": saved["rev"]})
        return {"modified": saved["modified"], "rev": saved["rev"]}

    @app.delete("/api/projects/{name}")
    def delete_project(name: str):
        moved = _project_errors(projects.delete, lib().output_dir, name)
        lib().rescan()
        return {"moved": moved}

    @app.post("/api/projects/{name}/music")
    def add_music(name: str, body: dict = Body(...)):
        stored = _project_errors(projects.add_music, lib().output_dir, name, body.get("path"))
        try:
            from ..probe import probe as probe_file
            length = probe_file(stored)["duration_sec"]
        except (RuntimeError, OSError, ValueError):
            length = None
        return {"path": stored, "duration_sec": length}

    def _fresh_probes(project):
        """Swap embedded probes older than the probe version for the library's cached ones
        (probing once when not cached yet); export.ensure_probes covers what is left."""
        for src in (project.get("sources") or {}).values():
            if ((src.get("probe") or {}).get("probe_version") or 1) >= probe_mod.PROBE_VERSION:
                continue
            known = lib().source_by_path(src["path"])
            if known and known["exists"]:
                try:
                    src["probe"] = library_mod.probe_source(lib().output_dir, known)
                except RuntimeError:
                    pass
        return export_mod.ensure_probes(project)

    @app.get("/api/projects/{name}/plan")
    async def export_plan(name: str):
        project = _project_errors(projects.load, lib().output_dir, name)
        try:
            p = await asyncio.to_thread(lambda: export_mod.plan(_fresh_probes(project)))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        p["output"] = str(export_mod.output_path(project, projects.path(lib().output_dir, name)))
        p["output_exists"] = os.path.exists(p["output"])
        p["missing"] = _missing(project)
        return p

    def _missing(project):
        """Files the export needs that are not there (sources, cleaned sound, music)."""
        paths = [(src or {}).get("path") for src in (project.get("sources") or {}).values()]
        tracks = project.get("tracks") or {}
        paths += [(c.get("audio") or {}).get("file") for c in tracks.get("video") or []]
        paths += [m.get("file") for m in tracks.get("music") or []]
        return sorted({p for p in paths if p and not os.path.exists(p)})

    @app.get("/api/projects/{name}/estimate")
    async def export_estimate(name: str):
        """The saved size estimate (export/estimate.json) and whether the project has
        changed since: stale when its key no longer matches the saved settings."""
        from .. import sizing
        project = _project_errors(projects.load, lib().output_dir, name)
        path = projects.path(lib().output_dir, name)
        try:
            est = json.loads(sizing.estimate_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"estimate": None, "stale": False}

        def key():
            s = sizing.with_overrides(export_mod.settings(project))
            if not s.get("budget"):
                return None
            return sizing.estimate_key(_fresh_probes(project), s)
        try:
            current = await asyncio.to_thread(key)
        except (ValueError, KeyError):
            current = None
        return {"estimate": est, "stale": current is None or current != est.get("key")}

    # -------------------------------------------------------------- editing aids
    def _prep_target(source_id, kind):
        source = lib().source(source_id)
        if source is None:
            raise HTTPException(404, "Unknown source")
        target = prep.targets(library_mod.prep_cache(lib().output_dir), source_id)[kind]
        if not target.exists():
            raise HTTPException(404, f"No {kind} yet for this source")
        return target

    @app.get("/media/proxy")
    def media_proxy(source: str):
        return FileResponse(_prep_target(source, "proxy"))

    @app.get("/media/peaks")
    def media_peaks(source: str):
        return FileResponse(_prep_target(source, "peaks"),
                            media_type="application/octet-stream")

    @app.get("/media/thumbs")
    def media_thumbs(source: str):
        folder = _prep_target(source, "thumbs")
        interval = float((folder / "interval.txt").read_text().strip())
        return {"interval": interval, "count": len(list(folder.glob("t*.jpg")))}

    @app.get("/media/thumb")
    def media_thumb(source: str, i: int = Query(ge=0)):
        path = _prep_target(source, "thumbs") / f"t{i:05d}.jpg"
        if not path.is_file():
            raise HTTPException(404, "No such thumbnail")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    # -------------------------------------------------------------- media
    def _still_wanted(request):
        async def wanted():
            return not await request.is_disconnected()
        return wanted

    def _frame_input(src, width):
        """Small frames come from the light editing copy when there is one: same timing,
        ~80 MB and a twentieth of a second to decode against ~0.5 GB for a 4K original."""
        if width <= 320:
            proxy = prep.targets(library_mod.prep_cache(lib().output_dir), src["id"])["proxy"]
            if proxy.is_file():
                return str(proxy)
        return src["path"]

    @app.get("/media/file")
    def media_file(path: str):
        if state["library"] is None or not lib().allowed(path) or not os.path.isfile(path):
            raise HTTPException(403, "Not a project file")
        return FileResponse(path)

    @app.get("/media/poster")
    async def media_poster(request: Request, source: str, i: int = Query(0, ge=0), n: int = media.POSTER_FRAMES):
        if n not in media.POSTER_SETS or i >= n:
            raise HTTPException(422, f"n must be one of {sorted(media.POSTER_SETS)} and i below n")
        src = _source(source)
        probe = src.get("probe")
        if not probe:
            try:
                probe = await asyncio.to_thread(library_mod.probe_source, lib().output_dir, src)
            except RuntimeError as exc:
                raise HTTPException(422, str(exc))
            src["probe"] = probe
        if not probe.get("video"):
            raise HTTPException(404, "The source has no picture")
        try:
            path = await media.poster(lib().output_dir, src["path"], src["id"], i,
                                      probe.get("duration_sec"), n, wanted=_still_wanted(request))
        except media.Abandoned:
            raise HTTPException(499, "The request was dropped")
        except media.Busy as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
        except RuntimeError as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/frame")
    async def media_frame(request: Request, source: str, t: float = Query(0.0, ge=0), w: int = media.FRAME_WIDTHS[0]):
        if w not in media.FRAME_WIDTHS:
            raise HTTPException(422, f"w must be one of {list(media.FRAME_WIDTHS)}")
        src = _source(source)
        probe = src.get("probe")
        if not probe:
            try:
                probe = await asyncio.to_thread(library_mod.probe_source, lib().output_dir, src)
            except RuntimeError as exc:
                raise HTTPException(422, str(exc))
            src["probe"] = probe
        if not probe.get("video"):
            raise HTTPException(404, "The source has no picture")
        duration = probe.get("duration_sec") or 0.0
        at = min(t, max(0.0, duration - 0.2))      # a frame exists just before the end
        try:
            path = await media.frame(lib().output_dir, _frame_input(src, w), src["id"], at, w,
                                     wanted=_still_wanted(request))
        except media.Abandoned:
            raise HTTPException(499, "The request was dropped")
        except media.Busy as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
        except RuntimeError as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/audio")
    async def media_audio(kind: Literal["output", "original_for", "original"],
                          file: Optional[str] = None, source: Optional[str] = None,
                          start: float = 0.0, duration: Optional[float] = None):
        out_dir = lib().output_dir
        try:
            if kind in ("output", "original_for"):
                # With a duration: only that stretch (start counted from the window's
                # start), for comparing long outputs a section at a time.
                out = lib().output(file or "")
                if out is None or not out["exists"]:
                    raise HTTPException(404, "Unknown output")
                section = duration is not None
                if kind == "output":
                    path = (await media.output_audio_section(out_dir, out["path"], start, duration)
                            if section else await media.output_audio(out_dir, out["path"]))
                else:
                    window = out.get("window") or {}
                    src = lib().source_by_path(out["source_path"] or "")
                    if src is None or not src["exists"]:
                        raise HTTPException(410, "The source file is missing")
                    begin = window.get("actual_start_sec") or 0.0
                    path = await media.original_audio(
                        out_dir, src["path"], begin + (start if section else 0.0),
                        duration if section else window.get("actual_duration_sec"))
            else:
                src = _source(source or "")
                path = await media.original_audio(out_dir, src["path"], start, duration)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    # -------------------------------------------------------------- frontend
    @app.get("/{rest:path}")
    def frontend(rest: str):
        target = (STATIC / rest).resolve()
        if rest and target.is_file() and str(target).startswith(str(STATIC.resolve())):
            return FileResponse(target)
        index = STATIC / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>Video Master</h1><p>The frontend is not built. Run "
                            "<code>cd gui &amp;&amp; npm ci &amp;&amp; npm run build</code> "
                            "(or <code>./setup.sh</code>).</p>", status_code=503)

    return app
