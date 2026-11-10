"""An output folder seen as a project: its outputs, their sources and tuning plans.

Everything is read from files the CLIs write (the .params.json sidecars and
_tuning/*/plan.json), so the CLI and the GUI always see the same project. The
GUI's own files live in OUTPUT_DIR/_gui/.
"""
import hashlib
import json
import os
import re
import threading
import unicodedata
from pathlib import Path

from .. import probe as probe_mod
from ..events import clean as json_safe
from ..timeparse import format_time_filename

MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".wav"}
_WINDOW = re.compile(r"((?:\d+h)?\d+m\d+s)-((?:\d+h)?\d+m\d+s|end)", re.I)


def gui_dir(output_dir):
    return Path(output_dir) / "_gui"


def _load_json(path):
    """Parsed JSON with NaN/inf (which the CLIs' json.dumps writes) turned into null."""
    try:
        return json_safe(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def path_id(path):
    """Source id; falls back to the path alone when the file is gone."""
    try:
        return probe_mod.source_id(path)
    except OSError:
        return hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:12]


def name_parts(name, source_stem=None):
    """(window label, tag) written in an output's name."""
    stem = os.path.splitext(name)[0]
    matches = list(_WINDOW.finditer(stem))
    if not matches:
        return None, None
    m = matches[-1]
    rest = stem[m.end():]
    tag = rest[1:] if rest.startswith("_") and len(rest) > 1 else None
    return m.group(0), tag


def _label(window):
    start = window.get("requested_start_sec") or 0.0
    duration = window.get("requested_duration_sec")
    if duration is None:
        duration = window.get("actual_duration_sec")
        if duration is not None:
            duration -= window.get("keyframe_snap_sec") or 0.0
    end = None if duration is None else start + duration
    return f"{format_time_filename(start)}-{format_time_filename(end)}"


def _output_from_sidecar(output_dir, sidecar_path, record):
    params = record.get("parameters") or {}
    name = record.get("output") or Path(sidecar_path).name.replace(".params.json", ".mp4")
    path = Path(output_dir) / name
    window = record.get("window") or {}
    label, tag = name_parts(name)
    kind = "video"
    if params.get("compare") is not None:
        kind = "compare"
    elif params.get("filter_audio_only") or name.lower().endswith(".wav"):
        kind = "audio"
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return {
        "file": name,
        "path": str(path),
        "exists": size is not None,
        "sidecar": str(sidecar_path),
        "created": record.get("created"),
        "window": window,
        "label": label or _label(window),
        "tag": tag,
        "params": params,
        "results": record.get("results") or {},
        "reproduce": record.get("reproduce"),
        "size": size,
        "kind": kind,
        "source_path": params.get("input_video"),
    }


def _plan_summary(plan_path, plan):
    grid = plan.get("grid") or {}
    return {
        "path": str(plan_path),
        "dir": str(Path(plan_path).parent),
        "source_path": plan.get("source"),
        "separate_music": bool(grid.get("separate_music")),
        "source_kind": grid.get("source", "tape"),
        "method": plan.get("method"),
        "criteria": plan.get("criteria"),
        "windows": plan.get("windows", []),
    }


def scan(output_dir, extra_sources=()):
    """The project in output_dir as a JSON-ready dict (see docs/gui-design.md §5.1)."""
    output_dir = Path(output_dir)
    sources = {}

    def source_for(path):
        # macOS hands out decomposed (NFD) Hebrew/accented names in some places and
        # composed ones in others; one source must not show up twice.
        path = unicodedata.normalize("NFC", os.path.abspath(path))
        if path not in sources:
            exists = os.path.exists(path)
            sources[path] = {"id": path_id(path), "path": path, "name": Path(path).name,
                             "exists": exists, "outputs": [], "plans": []}
        return sources[path]

    with_sidecar = set()
    unknown = []
    exports = []
    for sidecar in sorted(output_dir.glob("*.params.json")):
        record = _load_json(sidecar)
        if not isinstance(record, dict):
            continue
        if record.get("kind") == "export":       # an edit's export: listed with its project
            name = record.get("output") or ""
            with_sidecar.add(name)
            path = output_dir / name
            exists = bool(name) and path.is_file()
            exports.append({"file": name, "path": str(path), "exists": exists,
                            "size": path.stat().st_size if exists else None,
                            "created": record.get("created"),
                            "project": (record.get("project") or {}).get("name")
                            or Path(record.get("project_path") or "").parent.name or None})
            continue
        out = _output_from_sidecar(output_dir, sidecar, record)
        with_sidecar.add(out["file"])
        if out["source_path"]:
            source_for(out["source_path"])["outputs"].append(out)
        else:
            unknown.append(out)
    for path in sorted(output_dir.iterdir()) if output_dir.is_dir() else []:
        if (path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
                and path.name not in with_sidecar and not path.name.endswith(".partial")):
            label, tag = name_parts(path.name)
            unknown.append({"file": path.name, "path": str(path), "exists": True,
                            "sidecar": None, "created": None, "window": None,
                            "label": label, "tag": tag, "params": {}, "results": {},
                            "reproduce": None, "size": path.stat().st_size,
                            "kind": "audio" if path.suffix.lower() == ".wav" else "video",
                            "source_path": None})

    for plan_path in sorted((output_dir / "_tuning").glob("*/plan.json")):
        plan = _load_json(plan_path)
        if isinstance(plan, dict) and plan.get("source"):
            source_for(plan["source"])["plans"].append(_plan_summary(plan_path, plan))
    for path in extra_sources:
        source_for(path)

    for s in sources.values():
        s["outputs"].sort(key=lambda o: (o["window"] or {}).get("actual_start_sec") or 0)
        kinds = [p["source_kind"] for p in s["plans"]]
        kinds += [o["params"].get("source") for o in s["outputs"] if o["params"].get("source")]
        s["source_kind"] = kinds[-1] if kinds else None
        s["probe"] = cached_probe(output_dir, s)
        s["prep"] = prep_state(output_dir, s["id"])
    ordered = sorted(sources.values(), key=lambda s: s["name"].lower())
    from . import projects
    return {"output_dir": str(output_dir), "sources": ordered,
            "unknown": sorted(unknown, key=lambda o: o["file"]),
            "exports": sorted(exports, key=lambda o: o["created"] or "", reverse=True),
            "projects": projects.list_projects(output_dir)}


def prep_cache(output_dir):
    return gui_dir(output_dir) / "cache"


def prep_state(output_dir, source_id):
    """Which editing aids (proxy, thumbnails, waveform) exist for a source."""
    from ..prep import targets
    t = targets(prep_cache(output_dir), source_id)
    return {"proxy": t["proxy"].is_file(), "thumbs": (t["thumbs"] / "interval.txt").is_file(),
            "peaks": t["peaks"].is_file()}


def _probe_cache(output_dir, source_id):
    return gui_dir(output_dir) / "cache" / "probe" / f"{source_id}.json"


def cached_probe(output_dir, source):
    data = _load_json(_probe_cache(output_dir, source["id"]))
    return data if isinstance(data, dict) else None


def probe_source(output_dir, source):
    """ffprobe the source once; the result is cached per source id."""
    cached = cached_probe(output_dir, source)
    if cached:
        return cached
    data = probe_mod.probe(source["path"])
    path = _probe_cache(output_dir, source["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


class Library:
    """The open project, rescanned on demand; thread-safe."""

    def __init__(self, output_dir):
        self._lock = threading.Lock()
        self.output_dir = Path(os.path.abspath(os.path.expanduser(output_dir)))
        self._data = None

    @property
    def gui_dir(self):
        return gui_dir(self.output_dir)

    def _extra_sources_path(self):
        return self.gui_dir / "sources.json"

    def extra_sources(self):
        data = _load_json(self._extra_sources_path())
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []

    def add_source(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        extra = self.extra_sources()
        if path not in extra:
            extra.append(path)
            self.gui_dir.mkdir(parents=True, exist_ok=True)
            self._extra_sources_path().write_text(json.dumps(extra, indent=2,
                                                             ensure_ascii=False),
                                                  encoding="utf-8")
        self.rescan()
        return self.source_by_path(path)

    def get(self):
        with self._lock:
            if self._data is None:
                self._data = scan(self.output_dir, self.extra_sources())
            return self._data

    def rescan(self):
        with self._lock:
            self._data = None
        return self.get()

    def source(self, source_id):
        return next((s for s in self.get()["sources"] if s["id"] == source_id), None)

    def source_by_path(self, path):
        path = unicodedata.normalize("NFC", os.path.abspath(path))
        return next((s for s in self.get()["sources"] if s["path"] == path), None)

    def output(self, name):
        data = self.get()
        for s in data["sources"]:
            for o in s["outputs"]:
                if o["file"] == name:
                    return o
        return next((o for o in data["unknown"] if o["file"] == name), None)

    def plan(self, plan_path):
        plan_path = os.path.abspath(plan_path)
        for s in self.get()["sources"]:
            for p in s["plans"]:
                if p["path"] == plan_path:
                    return p
        return None

    def allowed(self, path):
        """Files the media endpoint may serve: inside the project, or a known source."""
        real = os.path.realpath(path)
        root = os.path.realpath(self.output_dir)
        if real == root or real.startswith(root + os.sep):
            return True
        return any(os.path.realpath(s["path"]) == real for s in self.get()["sources"])
