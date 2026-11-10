"""The GUI's HTTP server: JSON API, job events (SSE) and media, on 127.0.0.1 only."""
import asyncio
import json
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, \
    StreamingResponse
from pydantic import BaseModel, Field

from .. import export as export_mod, prep, tune
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

    @asynccontextmanager
    async def lifespan(app):
        queue.start()
        if output_dir:
            open_project(output_dir)
        yield
        await queue.stop()

    app = FastAPI(title="Video Audio Cleaner", lifespan=lifespan)
    app.state.queue = queue
    app.state.project = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS and host != "testserver":
            return PlainTextResponse("Forbidden host", status_code=403)
        path = request.url.path
        if (path.startswith("/api/") and path != "/api/events"
                and request.headers.get(API_HEADER) != "1"):
            return PlainTextResponse("Missing API header", status_code=403)
        return await call_next(request)

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

    @app.get("/api/app")
    def app_info():
        from .. import __version__
        return {"native": dialogs is not None, "version": __version__}

    @app.get("/api/fs/pick")
    async def fs_pick(kind: Literal["file", "dir", "audio"] = "file", start: Optional[str] = None):
        if dialogs is None:
            raise HTTPException(501, "No native dialogs in a browser; use /api/fs/list.")
        path = await asyncio.to_thread(dialogs.pick, kind, start)
        return {"path": path}

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
        lib().rescan()
        return {"moved": moved}

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

    def _build(body):
        kind = body.get("type")
        out_dir = lib().output_dir
        try:
            if kind == "clean":
                req = CleanRequest(**body)
                source = _source(req.source_id)
                argv = commands.clean_argv(
                    source["path"], out_dir, req.start_sec, req.duration_sec, req.autotune,
                    req.source_kind, req.separate_music, (req.tag or "").strip() or None,
                    (req.output or "").strip() or None, req.normalize, req.overrides)
                bits = [_fmt_window(req.start_sec, req.duration_sec)]
                if req.autotune:
                    bits.append(f"autotune · {req.source_kind}")
                if req.separate_music:
                    bits.append("music")
                if req.tag:
                    bits.append(req.tag)
                title = f"Clean {source['name']} " + " · ".join(bits)
                meta = {"source_id": source["id"], **req.model_dump()}
            elif kind == "clean_candidate":
                req = CandidateRequest(**body)
                plan_path = _plan_path(req.plan_path)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                tag = (req.tag or "").strip() or f"cand{req.index}"
                argv = commands.candidate_argv(plan["source"], out_dir, plan_path, req.window,
                                               req.index, tag)
                title = f"Render candidate {req.index} of {Path(plan['source']).name} " \
                        f"{req.window}"
                meta = req.model_dump()
            elif kind == "tune":
                req = TuneRequest(**body)
                source = _source(req.source_id)
                windows = [w.model_dump() for w in req.windows]
                if any(w["end_sec"] <= w["start_sec"] for w in windows):
                    raise ValueError("Each window must end after it starts")
                argv = commands.tune_argv(source["path"], out_dir, windows, req.source_kind,
                                          req.separate_music)
                title = f"Tune {source['name']} " + ", ".join(
                    commands.window_label(w["start_sec"], w["end_sec"]) for w in windows)
                meta = {"source_id": source["id"], **req.model_dump()}
            elif kind == "prep":
                source = _source(body.get("source_id") or "")
                kinds = [k for k in body.get("kinds") or ("proxy", "thumbs", "peaks")
                         if k in prep.KINDS]
                if not kinds:
                    raise ValueError("Nothing to prepare")
                argv = commands.prep_argv(source["path"], library_mod.prep_cache(out_dir),
                                          source["id"], kinds)
                title = f"Prepare {source['name']} for editing"
                meta = {"source_id": source["id"], "kinds": kinds}
            elif kind == "export":
                name = body.get("project") or ""
                path = projects.path(out_dir, name)
                if not path.is_file():
                    raise HTTPException(404, "Unknown project")
                argv = commands.export_argv(path, bool(body.get("reencode")))
                title = f"Export {name}"
                meta = {"project": name}
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
                    yield f"event: job\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
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

    @app.put("/api/projects/{name}")
    def save_project(name: str, body: dict = Body(...)):
        saved = _project_errors(projects.save, lib().output_dir, name, body)
        return {"modified": saved["modified"]}

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

    @app.get("/api/projects/{name}/plan")
    async def export_plan(name: str):
        project = _project_errors(projects.load, lib().output_dir, name)
        try:
            p = await asyncio.to_thread(export_mod.plan, project)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        p["output"] = str(export_mod.output_path(project, projects.path(lib().output_dir, name)))
        p["output_exists"] = os.path.exists(p["output"])
        return p

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
    @app.get("/media/file")
    def media_file(path: str):
        if state["library"] is None or not lib().allowed(path) or not os.path.isfile(path):
            raise HTTPException(403, "Not a project file")
        return FileResponse(path)

    @app.get("/media/poster")
    async def media_poster(source: str, i: int = Query(0, ge=0), n: int = media.POSTER_FRAMES):
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
                                      probe.get("duration_sec"), n)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/frame")
    async def media_frame(source: str, t: float = Query(0.0, ge=0), w: int = media.FRAME_WIDTHS[0]):
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
            path = await media.frame(lib().output_dir, src["path"], src["id"], at, w)
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
                out = lib().output(file or "")
                if out is None or not out["exists"]:
                    raise HTTPException(404, "Unknown output")
                if kind == "output":
                    path = await media.output_audio(out_dir, out["path"])
                else:
                    window = out.get("window") or {}
                    src = lib().source_by_path(out["source_path"] or "")
                    if src is None or not src["exists"]:
                        raise HTTPException(410, "The source file is missing")
                    path = await media.original_audio(out_dir, src["path"],
                                                      window.get("actual_start_sec") or 0.0,
                                                      window.get("actual_duration_sec"))
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
        return HTMLResponse("<h1>Video Audio Cleaner</h1><p>The frontend is not built. Run "
                            "<code>cd gui &amp;&amp; npm ci &amp;&amp; npm run build</code> "
                            "(or <code>./setup.sh</code>).</p>", status_code=503)

    return app
