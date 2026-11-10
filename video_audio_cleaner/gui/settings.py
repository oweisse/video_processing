"""Per-user GUI settings; everything per-project lives in the output folder."""
import json
import os
from pathlib import Path

MAX_RECENT = 10


def settings_path():
    override = os.environ.get("VAC_GUI_SETTINGS")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "video-audio-cleaner" / \
        "settings.json"


def load():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update(**changes):
    data = load()
    data.update(changes)
    save(data)
    return data


def remember(key, value):
    """Put value first in the recent list `key`."""
    data = load()
    items = [v for v in data.get(key, []) if v != value]
    data[key] = [value] + items[:MAX_RECENT - 1]
    save(data)
    return data
