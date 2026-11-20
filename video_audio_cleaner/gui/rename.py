"""Renaming a cleaned version: its tag is the part of the file name after the window
(`<video>_04m12s-06m47s_<tag>.mp4`). The video, its .params.json and every project clip
that plays it follow the new name."""
import json
import os
from pathlib import Path

from .. import provenance
from . import library as library_mod, projects


def new_name(name, tag):
    """The file name with its tag replaced (None or "" = no tag, "first version")."""
    stem, ext = os.path.splitext(name)
    matches = list(library_mod._WINDOW.finditer(stem))  # noqa: SLF001
    if not matches:
        raise ValueError("This file's name has no time window, so it has no tag to change")
    tag = (tag or "").strip()
    if any(sep in tag for sep in (os.sep, os.altsep or os.sep)) or tag in (".", ".."):
        raise ValueError("A label cannot contain / or be . or ..")
    return stem[:matches[-1].end()] + (f"_{tag}" if tag else "") + ext


def rename_output(output_dir, path, tag):
    """Rename one output. Returns (new path, projects updated)."""
    path = Path(path)
    target = path.with_name(new_name(path.name, tag))
    if target == path:
        return path, []
    if target.exists():
        raise FileExistsError(f"{target.name} already exists; choose another label")
    old_side, new_side = provenance.sidecar_path(path), provenance.sidecar_path(target)
    os.rename(path, target)
    if old_side.exists():
        try:
            side = json.loads(old_side.read_text(encoding="utf-8"))
            side["output"] = target.name
            if isinstance(side.get("parameters"), dict):
                side["parameters"]["tag"] = (tag or "").strip() or None
            if isinstance(side.get("reproduce"), str):
                side["reproduce"] = side["reproduce"].replace(path.name, target.name)
            tmp = new_side.with_suffix(".json.partial")
            tmp.write_text(json.dumps(side, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, new_side)
            old_side.unlink()
        except (OSError, ValueError):
            os.rename(old_side, new_side)
    return target, retarget_clips(output_dir, path, target)


def retarget_clips(output_dir, old, new):
    """Clips of every project that play `old`: now play `new`, or their original sound
    when new is None. Returns the names of the projects changed."""
    old = Path(old)
    updated = []
    for item in projects.list_projects(output_dir):
        name = item.get("name")
        try:
            project = projects.load(output_dir, name)
        except (OSError, ValueError):
            continue
        changed = False
        for clip in (project.get("tracks") or {}).get("video") or []:
            audio = clip.get("audio") or {}
            if audio.get("output") == old.name or audio.get("file") == str(old):
                if new is None:
                    gain = audio.get("gain_db") or 0
                    clip["audio"] = {"gain_db": gain} if gain else None
                else:
                    audio["output"] = Path(new).name
                    audio["file"] = str(new)
                changed = True
        if changed:
            projects.save(output_dir, name, project)
            updated.append(name)
    return updated
