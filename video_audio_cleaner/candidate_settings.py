"""Every setting a tuned candidate stands for, for reading in the GUI.

Built the way `clean-audio --from_candidate` builds them (what "Render this one" runs):
the clean-audio defaults, then what the tuning run fixed (the window, music separation,
loudness), then the candidate's own choices. Each setting says where its value came from
and whether it does anything with the others as they are.
"""
import shlex

from . import cli, provenance

# What the candidate chooses (cli._apply_candidate), what the tuning run fixes.
CANDIDATE = {"atten_lim_db", "reduce_hiss", "hiss_method", "hiss_nr", "hiss_strength",
             "declick", "declick_nr", "music_level_db"}
TUNING = {"skip_first_n_sec", "process_n_seconds", "separate_music", "normalize", "target_lufs"}
# How a run is asked for or reported, not how the sound is made.
HIDDEN = {"help", "input_video", "output_dir", "output", "tag", "compare", "autotune",
          "source", "keep_candidates", "from_candidate", "autotune_dir", "verbose",
          "progress", "keep_temp", "filter_audio_only"}

LABELS = {
    "skip_first_n_sec": "Window starts at",
    "process_n_seconds": "Window length",
    "atten_lim_db": "Noise reduction limit",
    "chunk_len_sec": "Denoise chunk length",
    "crossfade_sec": "Chunk crossfade",
    "normalize": "Loudness normalisation",
    "target_lufs": "Loudness target",
    "audio_gain": "Extra gain",
    "reduce_hiss": "Hiss reduction",
    "hiss_method": "Hiss method",
    "noise_profile_at": "Noise sample at",
    "noise_profile_len": "Noise sample length",
    "noise_profile_max_excess_db": "Noise sample: max excess",
    "hiss_nr": "Hiss reduction amount",
    "hiss_nf": "Hiss noise floor",
    "hiss_gain_smooth": "Hiss gain smoothing",
    "hiss_strength": "Hiss strength (patch)",
    "hiss_window": "Hiss window (patch)",
    "declick": "Click removal",
    "declick_nr": "Click removal amount",
    "declick_nf": "Click noise floor",
    "separate_music": "Music separation (Demucs)",
    "music_level_db": "Music bed level",
    "no_music_gate": "Mix the whole music stem back (no gate)",
    "separation_device": "Separation device",
    "separation_segment": "Separation segment",
    "separation_auto_segment_sec": "Separation: split above",
    "precise_cut": "Precise cut (re-encode video)",
    "video_crf": "Video quality (CRF)",
    "audio_bitrate": "Audio bitrate",
    "target_tp": "True-peak ceiling",
    "target_lra": "Loudness range",
    "target_lufs_max": "Loudest automatic target",
    "target_lufs_min": "Quietest automatic target",
    "limiter_ceiling": "Limiter ceiling",
    "silence_warn_lufs": "Warn when quieter than",
    "loudness_tolerance_db": "Loudness tolerance",
}
UNITS = {"skip_first_n_sec": "s", "process_n_seconds": "s", "atten_lim_db": "dB",
         "chunk_len_sec": "s", "crossfade_sec": "s", "target_lufs": "LUFS", "noise_profile_len": "s",
         "noise_profile_max_excess_db": "dB", "hiss_nr": "dB", "hiss_nf": "dB", "hiss_window": "s",
         "declick_nr": "dB", "declick_nf": "dB", "music_level_db": "dB", "separation_segment": "s",
         "separation_auto_segment_sec": "s", "target_tp": "dBTP", "target_lra": "LU",
         "target_lufs_max": "LUFS", "target_lufs_min": "LUFS", "silence_warn_lufs": "LUFS",
         "loudness_tolerance_db": "dB"}


def _applies(dest, a):
    """Whether a setting does anything, given the others."""
    hiss = a.reduce_hiss
    if dest in ("hiss_method", "noise_profile_at", "noise_profile_len", "noise_profile_max_excess_db"):
        return hiss and (dest == "hiss_method" or a.hiss_method == "profile")
    if dest in ("hiss_nr", "hiss_nf", "hiss_gain_smooth"):
        return hiss and a.hiss_method == "profile"
    if dest in ("hiss_strength", "hiss_window"):
        return hiss and a.hiss_method == "patch"
    if dest in ("declick_nr", "declick_nf"):
        return a.declick
    if dest in ("music_level_db", "no_music_gate", "separation_device", "separation_segment", "separation_auto_segment_sec"):
        return a.separate_music
    if dest in ("target_lufs", "target_tp", "target_lra", "target_lufs_max", "target_lufs_min",
                "limiter_ceiling", "loudness_tolerance_db"):
        return a.normalize
    if dest == "video_crf":
        return a.precise_cut
    return True


def _shown(dest, value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return {"atten_lim_db": "unlimited", "music_level_db": "off (music dropped)",
                "audio_gain": "none", "noise_profile_at": "found automatically",
                "separation_segment": "model default"}.get(dest, "automatic")
    if dest in ("skip_first_n_sec", "process_n_seconds") and isinstance(value, (int, float)):
        m, sec = divmod(float(value), 60)
        return f"{int(m)}:{sec:04.1f}".replace(".0", "") + (" into the video" if dest == "skip_first_n_sec" else "")
    if isinstance(value, float):
        value = f"{value:g}"
    unit = UNITS.get(dest)
    return f"{value} {unit}" if unit else str(value)


def candidate_settings(plan_path, label, index):
    plan_path, plan, entry, candidate = cli.load_candidate(plan_path, label, index)
    parser = cli.build_parser()
    flags = plan.get("output_flags") or {}
    argv = [plan["source"], "--output_dir", "."]
    if flags.get("normalize", True):
        argv.append("--normalize")
    defaults = parser.parse_args([plan["source"], "--output_dir", "."])
    args = parser.parse_args(argv)
    args.skip_first_n_sec = entry["start_sec"]
    args.process_n_seconds = entry["end_sec"] - entry["start_sec"]
    args.separate_music = bool((plan.get("grid") or {}).get("separate_music"))
    cli._apply_candidate(args, candidate)  # noqa: SLF001 — the same step a render takes
    target = flags.get("target_lufs", "auto")
    auto_loudness = args.normalize and (target in (None, "auto"))
    if args.normalize and not auto_loudness:
        args.target_lufs = float(target)

    groups = {a.dest: g.title for g in parser._action_groups for a in g._group_actions}  # noqa: SLF001
    rows = []
    for action in parser._actions:  # noqa: SLF001
        dest = action.dest
        if dest in HIDDEN:
            continue
        value = getattr(args, dest, None)
        applies = bool(_applies(dest, args))
        origin = "candidate" if dest in CANDIDATE and applies else "tuning" if dest in TUNING else "default"
        if origin == "default" and value != getattr(defaults, dest, None):
            origin = "tuning"
        shown = _shown(dest, value)
        if dest == "target_lufs" and auto_loudness:
            shown = f"automatic, between {args.target_lufs_min:g} and {args.target_lufs_max:g} LUFS"
            value = "auto"
        rows.append({
            "name": dest, "label": LABELS.get(dest, dest.replace("_", " ").capitalize()),
            "value": value, "shown": shown, "origin": origin,
            "applies": applies,
            "advanced": groups.get(dest) == "Advanced thresholds",
            "help": action.help or "",
        })

    render = ["clean-audio", plan["source"], "--output_dir", "OUTPUT_DIR"]
    if args.normalize:
        render.append("--normalize")
    render += ["--from_candidate", str(plan_path), entry["label"], str(int(index))]
    if auto_loudness:
        args.target_lufs = "auto"
    try:
        explicit = provenance.full_command(parser, args)
    except ValueError:
        explicit = None
    return {"index": int(index), "window": entry["label"], "describe": candidate.describe(),
            "rows": rows, "render_command": shlex.join(render),
            "explicit_command": shlex.join(x for x in explicit if not x.startswith(("--output_dir=", "--progress=")))
            if explicit else None}
