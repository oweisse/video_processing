"""Job queue: every long operation is a CLI subprocess with a JSON event stream.

One ML job runs at a time (Demucs and DeepFilterNet each want most of a 16 GB
machine); light jobs (ffmpeg-only) get a lane of their own. Jobs are persisted
as OUTPUT_DIR/_gui/jobs/<id>.json with their log beside them, so history
survives restarts.
"""
import asyncio
import json
import os
import secrets
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

# Heavy jobs share one lane; preparing sources (ffmpeg, light) runs beside them.
ML_TYPES = {"clean", "clean_candidate", "tune", "export"}
MAX_MESSAGES = 40
CANCEL_LADDER = ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0))
ACTIVE = ("queued", "running")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return f"{datetime.now():%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def jobs_dir(output_dir):
    return Path(output_dir) / "_gui" / "jobs"


class JobQueue:
    def __init__(self, on_finished=None):
        self.jobs = {}
        self.on_finished = on_finished
        self._subscribers = set()
        self._lanes = {"ml": asyncio.Queue(), "light": asyncio.Queue()}
        self._procs = {}
        self._workers = []

    # ------------------------------------------------------------ lifecycle
    def start(self):
        for lane in self._lanes:
            self._workers.append(asyncio.create_task(self._worker(lane)))

    async def stop(self):
        for job_id in list(self._procs):
            await self.cancel(job_id)
        for w in self._workers:
            w.cancel()

    def load(self, output_dir):
        """Read the history of a project. Jobs that were running when the server died
        are marked lost; queued ones are not restarted unasked."""
        folder = jobs_dir(output_dir)
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("id") in self.jobs:
                continue
            if job.get("state") in ACTIVE:
                job["state"] = "lost"
                job["error"] = "The GUI server stopped while this job was " + \
                               ("running." if job.get("started") else "waiting.")
                job["finished"] = job.get("finished") or _now()
                self._save(job)
            self.jobs[job["id"]] = job

    # ------------------------------------------------------------ public
    def list(self, output_dir=None):
        jobs = [j for j in self.jobs.values()
                if output_dir is None or j["output_dir"] == str(output_dir)
                or j["state"] in ACTIVE]
        return sorted(jobs, key=lambda j: j["id"], reverse=True)[:200]

    def submit(self, type_, title, argv, output_dir, display=None, meta=None):
        job = {"id": new_id(), "type": type_, "title": title, "argv": list(argv),
               "display": display, "output_dir": str(output_dir), "state": "queued",
               "created": _now(), "started": None, "finished": None, "returncode": None,
               "step": None, "progress": None, "candidates": None, "windows_done": [],
               "messages": [], "result": None, "error": None, "meta": meta or {},
               "log": str(jobs_dir(output_dir) / "placeholder.log")}
        job["log"] = str(jobs_dir(output_dir) / f"{job['id']}.log")
        self.jobs[job["id"]] = job
        self._save(job)
        self._lanes["ml" if type_ in ML_TYPES else "light"].put_nowait(job["id"])
        self._publish(job)
        return job

    async def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["state"] == "queued":
            self._finish(job, "cancelled", None)
            return job
        proc = self._procs.get(job_id)
        if job["state"] != "running" or proc is None:
            return job
        job["cancel_requested"] = True
        self._publish(job)
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            return job
        for sig, wait in CANCEL_LADDER:
            try:
                os.killpg(pgid, sig)
            except OSError:
                break
            try:
                await asyncio.wait_for(proc.wait(), wait)
                break
            except asyncio.TimeoutError:
                continue
        return job

    def subscribe(self):
        q = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    # ------------------------------------------------------------ internals
    def _publish(self, job):
        payload = {k: v for k, v in job.items() if k != "argv"}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _save(self, job):
        folder = jobs_dir(job["output_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{job['id']}.json"
        tmp = folder / f"{job['id']}.json.partial"
        tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _finish(self, job, state, returncode):
        job["state"] = state
        job["returncode"] = returncode
        job["finished"] = _now()
        job.pop("cancel_requested", None)
        self._save(job)
        self._publish(job)
        if self.on_finished:
            try:
                self.on_finished(job)
            except Exception:   # noqa: BLE001 — a refresh failure must not kill the queue
                pass

    async def _worker(self, lane):
        queue = self._lanes[lane]
        while True:
            job_id = await queue.get()
            job = self.jobs.get(job_id)
            if job is None or job["state"] != "queued":
                continue
            try:
                await self._run(job)
            except Exception as exc:   # noqa: BLE001
                job["error"] = f"{type(exc).__name__}: {exc}"
                self._finish(job, "failed", None)

    def _handle(self, job, ev):
        kind = ev.get("event")
        if kind == "step":
            job["step"] = {k: ev.get(k) for k in ("index", "total", "title")}
            job["progress"] = None
            job["candidates"] = None      # tuning (if any) is over once the pipeline steps start
        elif kind == "progress":
            job["progress"] = {k: ev.get(k) for k in ("prefix", "processed_sec", "total_sec",
                                                      "speed", "eta_sec")}
        elif kind == "message":
            job["messages"] = (job["messages"] + [{"level": ev.get("level"),
                                                   "text": ev.get("text")}])[-MAX_MESSAGES:]
        elif kind == "candidate":
            job["progress"] = None
            job["candidates"] = {"window": ev.get("window"), "done": (ev.get("index") or 0) + 1,
                                 "total": ev.get("total")}
        elif kind == "window":
            job["windows_done"] = job["windows_done"] + [ev.get("window")]
        elif kind == "result":
            job["result"] = {k: ev.get(k) for k in ("output", "sidecar", "replaced", "results")}
        elif kind == "error":
            job["error"] = ev.get("text")
        else:
            return False
        return True

    async def _run(self, job):
        job["state"] = "running"
        job["started"] = _now()
        self._save(job)
        self._publish(job)
        log_path = Path(job["log"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        last_publish = 0.0
        with open(log_path, "ab", buffering=0) as log:
            log.write(f"$ {job.get('display') or ' '.join(job['argv'])}\n".encode())
            proc = await asyncio.create_subprocess_exec(
                *job["argv"], stdout=asyncio.subprocess.PIPE, stderr=log,
                cwd=job["output_dir"], env=env, start_new_session=True, limit=8 * 1024 * 1024)
            self._procs[job["id"]] = proc
            job["pid"] = proc.pid
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    log.write(line)
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict) or not self._handle(job, ev):
                        continue
                    now = time.monotonic()
                    if ev.get("event") != "progress" or now - last_publish > 0.25:
                        last_publish = now
                        self._publish(job)
                rc = await proc.wait()
            finally:
                self._procs.pop(job["id"], None)
        if job.get("cancel_requested"):
            state = "cancelled"
        elif rc == 0:
            state = "done"
        else:
            state = "failed"
            job["error"] = job["error"] or f"Exited with code {rc}; see the log."
        self._finish(job, state, rc)
