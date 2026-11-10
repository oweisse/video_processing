"""GUI requests → exact CLI command lines.

The GUI never does anything the CLI cannot: every job is one of these argv
lists, validated against the real parsers before it is queued, and shown to
the user so it can be copied and run by hand.
"""
import sys
from pathlib import Path

from .. import cli, tune
from ..timeparse import format_time_filename

PYTHON = sys.executable
# Set by the GUI itself; an override may not change them.
RESERVED = {"--output_dir", "--progress", "--autotune", "--source", "--keep_candidates",
            "--from_candidate", "--separate_music", "--tag", "--output", "-o",
            "--skip_first_n_sec", "--process_n_seconds", "--autotune_dir", "--keep_temp",
            "--help", "-h"}


def seconds(value):
    """A time for the command line: plain seconds, no float noise."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def window_label(start, end):
    return f"{format_time_filename(start)}-{format_time_filename(end)}"


def _check(parser, argv):
    try:
        parser.parse_args(argv)
    except SystemExit:
        raise ValueError("The command line would be refused: " + " ".join(argv)) from None


def _option_strings(parser):
    return {s for a in parser._actions for s in a.option_strings}  # noqa: SLF001


def overrides_argv(overrides, autotune):
    """{"--atten_lim_db": 10, "--declick": true} → ["--atten_lim_db=10", "--declick"]."""
    known = _option_strings(cli.build_parser())
    out = []
    for flag, value in (overrides or {}).items():
        if flag not in known:
            raise ValueError(f"Unknown option {flag}")
        if flag in RESERVED:
            raise ValueError(f"{flag} is set by the form itself")
        if autotune and flag in cli.AUTOTUNED_OPTIONS:
            raise ValueError(f"{flag} is chosen by autotune; turn autotune off to set it")
        if value is True:
            out.append(flag)
        elif value is False or value is None or value == "":
            continue
        else:
            out.append(f"{flag}={value}")
    return out


def clean_argv(source_path, output_dir, start_sec=None, duration_sec=None, autotune=True,
               source_kind=None, separate_music=False, tag=None, output=None,
               normalize=True, overrides=None):
    args = [str(source_path), "--output_dir", str(output_dir), "--progress", "json"]
    if normalize and "--normalize" not in (overrides or {}):
        args.append("--normalize")
    if start_sec:
        args += ["--skip_first_n_sec", seconds(start_sec)]
    if duration_sec:
        args += ["--process_n_seconds", seconds(duration_sec)]
    if autotune:
        if source_kind not in ("tape", "digital"):
            raise ValueError("Autotune needs the source kind: tape or digital")
        args += ["--autotune", "--source", source_kind, "--keep_candidates"]
    if separate_music:
        args.append("--separate_music")
    if tag:
        args += ["--tag", tag]
    if output:
        args += ["--output", output]
    args += overrides_argv(overrides, autotune)
    _check(cli.build_parser(), args)
    return [PYTHON, "-m", "video_audio_cleaner.cli", *args]


def candidate_argv(source_path, output_dir, plan_path, window, index, tag=None):
    args = [str(source_path), "--output_dir", str(output_dir), "--progress", "json",
            "--normalize", "--from_candidate", str(plan_path), window, str(int(index))]
    if tag:
        args += ["--tag", tag]
    _check(cli.build_parser(), args)
    return [PYTHON, "-m", "video_audio_cleaner.cli", *args]


def tune_argv(source_path, output_dir, windows, source_kind, separate_music=False):
    if source_kind not in ("tape", "digital"):
        raise ValueError("Tuning needs the source kind: tape or digital")
    if not windows:
        raise ValueError("Give at least one window")
    labels = [window_label(w["start_sec"], w["end_sec"]) for w in windows]
    out_dir = tune.default_tune_dir(output_dir, source_path, separate_music)
    args = [str(source_path), *labels, "--out_dir", str(out_dir), "--source", source_kind,
            "--keep_candidates", "--progress", "json"]
    if separate_music:
        args.append("--separate_music")
    _check(tune.build_parser(), args)
    return [PYTHON, "-m", "video_audio_cleaner.tune", *args]


def prep_argv(source_path, cache_dir, source_id, kinds=("proxy", "thumbs", "peaks")):
    return [PYTHON, "-m", "video_audio_cleaner.prep", *kinds, "--source", str(source_path),
            "--cache", str(cache_dir), "--id", source_id, "--progress", "json"]


def export_argv(project_path, reencode=False):
    args = [str(project_path), "--progress", "json"]
    if reencode:
        args.append("--reencode")
    return [PYTHON, "-m", "video_audio_cleaner.export", *args]


def display(argv):
    """The argv as the user would type it (clean-audio …, not python -m …)."""
    import shlex
    names = {"video_audio_cleaner.cli": "clean-audio", "video_audio_cleaner.tune":
             "clean-audio-tune", "video_audio_cleaner.export": "clean-audio-export",
             "video_audio_cleaner.prep": "python -m video_audio_cleaner.prep"}
    if len(argv) > 2 and argv[1] == "-m" and argv[2] in names:
        argv = [names[argv[2]], *argv[3:]]
    # --progress json is how the GUI listens, not something to type by hand.
    argv = [a for i, a in enumerate(argv)
            if not (a == "--progress" or (i and argv[i - 1] == "--progress"))]
    return shlex.join(argv)


def preview_name(source_path, start_sec, duration_sec, file_duration, tag=None):
    has_window = bool(start_sec) or duration_sec is not None
    return cli.derive_output_name(str(source_path), start_sec or 0.0, duration_sec,
                                  file_duration, has_window, False, False, tag)


def cli_schema():
    """The clean-audio options, for the form's Advanced section."""
    parser = cli.build_parser()
    groups = {}
    for group in parser._action_groups:  # noqa: SLF001
        for action in group._group_actions:  # noqa: SLF001
            groups[action.dest] = group.title
    out = []
    for a in parser._actions:  # noqa: SLF001
        if not a.option_strings or a.dest == "help":
            continue
        flag = max(a.option_strings, key=len)
        if flag in RESERVED:
            continue
        kind = "flag" if a.nargs == 0 else ("choice" if a.choices else "value")
        default = a.default
        if isinstance(default, float) and default != default:
            default = None
        out.append({"dest": a.dest, "flag": flag, "kind": kind,
                    "default": default if isinstance(default, (str, int, float, bool,
                                                               type(None))) else str(default),
                    "choices": list(a.choices) if a.choices else None,
                    "help": a.help, "group": groups.get(a.dest),
                    "autotuned": flag in cli.AUTOTUNED_OPTIONS})
    return out
