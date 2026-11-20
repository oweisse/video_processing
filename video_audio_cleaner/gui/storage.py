"""How much disk each video takes, and what the editor added on top of it.

Per video: the original file; the editing copies (light proxy, thumbnails, sound peaks,
cached frames and posters); the tuning audio (each tuning run's kept candidate audio and
listening files); the rendered versions (cleaned videos and their .params.json). Per
project: those of its videos plus its own folder (copied music, the project file) and the
exported video. And what waits in the library's _trash.
"""
import os
from pathlib import Path

from .. import prep
from . import library as library_mod, projects


def du(path):
    """Bytes a file or folder takes (0 if it is missing)."""
    path = Path(path)
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _prefixed(folder, prefix):
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    total = 0
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith(prefix):
                try:
                    total += e.stat().st_size
                except OSError:
                    pass
    return total


def source_usage(output_dir, source):
    """{original, editing, tuning, rendered} bytes for one library video."""
    output_dir = Path(output_dir)
    cache = library_mod.prep_cache(output_dir)
    t = prep.targets(cache, source["id"])
    sid = source["id"]
    editing = (du(t["proxy"]) + du(t["thumbs"]) + du(t["peaks"])
               + _prefixed(output_dir / "_gui" / "cache" / "frames", f"{sid}_")
               + _prefixed(output_dir / "_gui" / "cache" / "posters", f"{sid}_"))
    tuning = sum(du(d) for d in {p["dir"] for p in source.get("plans") or []})
    rendered = 0
    for o in source.get("outputs") or []:
        rendered += du(o["path"]) + (du(o["sidecar"]) if o.get("sidecar") else 0)
    try:
        original = os.stat(source["path"]).st_size
    except OSError:
        original = (source.get("probe") or {}).get("size") or 0
    subtitles = du(output_dir / "_subtitles" / sid)
    return {"original": original, "editing": editing, "tuning": tuning, "rendered": rendered,
            "subtitles": subtitles}


def usage(output_dir, library, project_name=None):
    """Per-video usage (of one project's videos, or the whole library), with totals."""
    output_dir = Path(output_dir)
    sources = library["sources"]
    project_files = exported = 0
    if project_name:
        project = projects.load(output_dir, project_name)
        ids = set((project.get("sources") or {}).keys())
        paths = {v.get("path") for v in (project.get("sources") or {}).values()}
        sources = [s for s in sources if s["id"] in ids or s["path"] in paths]
        folder = projects.path(output_dir, project_name).parent
        project_files = du(folder)
        from .. import export
        out = export.output_path(project, projects.path(output_dir, project_name))
        exported = du(out) if Path(out).parent != folder else 0   # inside the folder: counted already
    rows = []
    for s in sources:
        u = source_usage(output_dir, s)
        rows.append({"id": s["id"], "name": s["name"], "exists": s.get("exists", True),
                     "versions": sum(1 for o in s.get("outputs") or [] if o.get("exists")),
                     "tunings": sum(len(p.get("windows") or []) for p in s.get("plans") or []), **u,
                     "extra": u["editing"] + u["tuning"] + u["rendered"] + u["subtitles"]})
    totals = {k: sum(r[k] for r in rows) for k in ("original", "editing", "tuning", "rendered", "subtitles", "extra")}
    totals["project_files"] = project_files
    totals["exported"] = exported
    return {"project": project_name, "videos": rows, "totals": totals,
            "trash": du(output_dir / "_trash")}
