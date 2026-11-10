"""The .params.json written next to every output.

Records exactly how a file was made: every parameter including the defaults in
force at the time, a command that reproduces it, the code version and the
measured results. Defaults change between versions (atten 30 -> 20 in 0.3.0),
so "it used the defaults" is not enough to know how a file was processed.
"""
import json
import platform
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

SCHEMA = 1
_REPO = Path(__file__).resolve().parent.parent

# Options where None is a real choice rather than "automatic", with the word that
# parses back to None. Without these, a reproduce command would silently fall
# back to the default (e.g. unlimited attenuation would become 20 dB).
NONE_SPELLING = {"atten_lim_db": "none", "music_level_db": "off"}
# How a run reports itself, not what it makes: left out of the record.
NOT_RECORDED = {"progress"}


def sidecar_path(output_path):
    return Path(output_path).with_suffix(".params.json")


def full_command(parser, args):
    """argv that reproduces `args` with every option spelled out.

    On/off flags appear only when on (argparse has no "off" spelling), and options
    whose value is None ("automatic") are left out; both are still listed in the
    sidecar's "parameters". `--opt=value` form keeps negative numbers unambiguous.
    """
    argv = ["clean-audio"]
    positionals = []
    for action in parser._actions:  # noqa: SLF001 — argparse has no public walker
        if action.dest == "help" or action.dest in NOT_RECORDED:
            continue
        value = getattr(args, action.dest, None)
        if not action.option_strings:
            positionals.append(str(value))
            continue
        flag = max(action.option_strings, key=len)
        if action.nargs == 0:  # store_true / store_false: present only when it sets the value
            if value == action.const:
                argv.append(flag)
            continue
        if value is None:
            if action.default is None:
                continue  # automatic: nothing to pin
            if action.dest not in NONE_SPELLING:
                raise ValueError(f"--{action.dest} is None but its default is not; add it "
                                 f"to NONE_SPELLING so the command reproduces it")
            argv.append(f"{flag}={NONE_SPELLING[action.dest]}")
            continue
        argv.append(f"{flag}={value:g}" if isinstance(value, float) else f"{flag}={value}")
    return argv[:1] + positionals + argv[1:]


def _version(dist):
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", str(_REPO), *args], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ffmpeg_version():
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0] if out.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def environment():
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "video_audio_cleaner": _version("video-audio-cleaner"),
        "git_commit": commit,
        "git_dirty": bool(status) if status is not None else None,
        "python": platform.python_version(),
        "torch": _version("torch"),
        "torchaudio": _version("torchaudio"),
        "deepfilternet": _version("deepfilternet"),
        "demucs": _version("demucs"),
        "ffmpeg": _ffmpeg_version(),
        "platform": platform.platform(),
    }


def write(output_path, parser, args, window, results):
    """Write <output>.params.json. Returns its path."""
    record = {
        "schema": SCHEMA,
        "output": Path(output_path).name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command_as_typed": shlex.join(["clean-audio", *sys.argv[1:]]),
        "reproduce": shlex.join(full_command(parser, args)),
        "parameters": {k: v for k, v in sorted(vars(args).items())
                       if k not in NOT_RECORDED},
        "window": window,
        "results": results,
        "environment": environment(),
    }
    path = sidecar_path(output_path)
    path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
