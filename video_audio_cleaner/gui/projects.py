"""Edit projects: OUTPUT_DIR/_projects/<name>/project.json (+ music/ for added songs).

The file format is export.py's; the GUI edits it and clean-audio-export renders it.
"""
import fcntl
import filecmp
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import export

_NAME = re.compile(r"^[^/\\\x00]{1,80}$")


def root(output_dir):
    return Path(output_dir) / "_projects"


def check_name(name):
    name = (name or "").strip()
    if not _NAME.match(name) or name in (".", "..") or name.startswith("."):
        raise ValueError("A project name is up to 80 characters, without / or \\.")
    return name


def path(output_dir, name):
    return root(output_dir) / check_name(name) / "project.json"


def new(name):
    return {"schema": export.SCHEMA, "name": name, "canvas": None, "sources": {},
            "tracks": {"video": [], "music": [], "music_duck": dict(export.DEFAULT_DUCK)},
            "export": {k: v for k, v in export.DEFAULT_EXPORT.items() if k != "output"},
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def create(output_dir, name):
    p = path(output_dir, name)
    if p.exists():
        raise FileExistsError(f"A project named {name} already exists.")
    p.parent.mkdir(parents=True)
    save(output_dir, name, new(check_name(name)))
    return load(output_dir, name)


def load(output_dir, name):
    return json.loads(path(output_dir, name).read_text(encoding="utf-8"))


def validate(project):
    if project.get("schema") != export.SCHEMA:
        raise ValueError("Unknown project schema")
    for key in ("sources", "tracks"):
        if not isinstance(project.get(key), dict):
            raise ValueError(f"The project needs a '{key}' object")
    export.clips(project)        # raises on impossible clips


class Conflict(Exception):
    """The project changed since the copy being saved was loaded (another tab, or the
    server pointing clips away from a deleted version). .current: the project now."""

    def __init__(self, current):
        super().__init__("The project was changed elsewhere")
        self.current = current


def save(output_dir, name, project, expect_rev=None):
    """Write the project, one writer at a time. Every save adds one to its "rev"; with
    expect_rev (the rev the editor loaded), a project changed since raises Conflict."""
    validate(project)
    p = path(output_dir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(".project.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except ValueError:
            current = None
        rev = int((current or {}).get("rev") or 0)
        if expect_rev is not None and current is not None and int(expect_rev) != rev:
            raise Conflict(current)
        project["name"] = check_name(name)
        project["modified"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        project["rev"] = rev + 1
        tmp = p.with_name("project.json.partial")
        tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    return project


def summary(output_dir, project_path):
    try:
        project = json.loads(Path(project_path).read_text(encoding="utf-8"))
        dur = export.duration(project)
        n = len(project.get("tracks", {}).get("video", []))
    except (OSError, ValueError, KeyError):
        return None
    out = export.output_path(project, project_path)
    return {"name": project.get("name") or Path(project_path).parent.name,
            "path": str(project_path), "modified": project.get("modified"),
            "duration_sec": dur, "clips": n, "export_path": str(out),
            "exported": out.exists(), "videos": video_ids(project)}


def video_ids(project):
    """The project's videos (source ids) in the order its Videos panel shows them."""
    ids = list((project.get("sources") or {}).keys())
    order = [i for i in project.get("video_order") or [] if i in ids]
    return order + [i for i in ids if i not in order]


def list_projects(output_dir):
    items = [summary(output_dir, p) for p in sorted(root(output_dir).glob("*/project.json"))]
    return sorted([i for i in items if i], key=lambda i: i.get("modified") or "", reverse=True)


def add_music(output_dir, name, source):
    """Copy a song into the project, so the project keeps working if the original moves."""
    source = Path(os.path.expanduser(source))
    if not source.is_file():
        raise FileNotFoundError(str(source))
    folder = path(output_dir, name).parent / "music"
    folder.mkdir(exist_ok=True)
    # The same song again reuses its copy; a different song with the same name gets
    # "name_2.mp3", "name_3.mp3"… so it never plays the first one's sound.
    dst, n = folder / source.name, 1
    while dst.exists() and not filecmp.cmp(source, dst, shallow=False):
        n += 1
        dst = folder / f"{source.stem}_{n}{source.suffix}"
    if not dst.exists():
        shutil.copy2(source, dst)
    return str(dst)


def delete(output_dir, name):
    folder = path(output_dir, name).parent
    trash = Path(output_dir) / "_trash" / "_projects"
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / f"{folder.name}.{datetime.now():%Y%m%d-%H%M%S}"
    shutil.move(str(folder), dst)
    return str(dst)
