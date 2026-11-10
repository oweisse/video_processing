#!/usr/bin/env python3
"""Extract a window of audio from a video, clean it, and merge it back.

Pipeline order matters: audio is extracted completely unprocessed, denoised
(optionally after splitting music off), and only then loudness-normalised.
Normalising before denoising would amplify the noise the denoiser is meant to
remove, and can clip.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import events, ffmpeg_utils, graph, procs
from .console import print_error, print_info, print_step, print_success, print_warning
from .timeparse import format_time_filename, parse_time_string, positive_time

SAMPLE_RATE = 48000
# Defaults for the "Advanced thresholds" flags; stage logic reads the flags.
DEFAULT_TARGET_TP = -1.5
DEFAULT_TARGET_LRA = 11.0
DEFAULT_LIMITER_CEILING = 0.97
DEFAULT_SILENCE_WARN_LUFS = -45.0
DEFAULT_LOUDNESS_TOLERANCE_DB = 2.0
DEFAULT_SEPARATION_AUTO_SEGMENT_SEC = 600.0



# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
def parse_atten_lim(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("none", "off", "unlimited"):
        return None
    try:
        number = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid --atten_lim_db value: '{value}'. Expected a number of dB, "
            f"or 'none' for unlimited attenuation."
        )
    return None if number <= 0 else number


def parse_target_lufs(value):
    if str(value).strip().lower() == "auto":
        return "auto"
    try:
        return float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid --target_lufs value: '{value}'. Expected a number of LUFS or 'auto'.")


def parse_music_level(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("off", "none", "mute"):
        return None
    try:
        number = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid --music_level_db value: '{value}'. Expected a number of dB "
            f"(e.g. -12), or 'off' to drop the music entirely."
        )
    return None if number <= -100 else number


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clean-audio",
        description="Extract audio from a video, clean it with DeepFilterNet "
                    "(optionally after Demucs music separation), and merge it back.",
        allow_abbrev=False,
    )
    parser.add_argument("input_video", type=str,
                        help="Path to the input video (or audio) file.")
    parser.add_argument("--output_dir", type=str, required=True,
                        help="REQUIRED. Directory that every output file is written to. "
                             "Created if missing.")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output FILENAME (not a path) placed inside --output_dir. "
                             "Defaults to a name derived from the input and time window. "
                             "A time window written in the name (e.g. 09m10s-12m20s) must "
                             "match the window processed; to label an output, use --tag.")
    parser.add_argument("--tag", type=str, default=None,
                        help="Label appended to the automatic output name, e.g. --tag "
                             "no-music gives NAME_09m10s-09m20s_no-music.mp4. The window "
                             "part always follows --skip_first_n_sec/--process_n_seconds.")

    parser.add_argument("--skip_first_n_sec", type=parse_time_string, default=None,
                        help="Skip this much at the start (e.g. '8m50s', '90').")
    parser.add_argument("--process_n_seconds", type=positive_time, default=None,
                        help="Process only this much audio (e.g. '3m30s', '45s').")

    parser.add_argument("--filter_audio_only", action="store_true",
                        help="Write the cleaned WAV instead of merging back into video.")
    parser.add_argument("--compare", type=positive_time, nargs="?", const=20.0, default=None,
                        metavar="SECONDS",
                        help="Preview mode: process only the first SECONDS (default 20) "
                             "of the window and write original+processed into one WAV "
                             "for A/B auditioning. Skips the video merge.")

    parser.add_argument("--autotune", action="store_true",
                        help="Choose --atten_lim_db, hiss reduction and --declick "
                             "automatically for this window: try a grid of settings, score "
                             "each against the source, render with the best. Results are "
                             "cached per video and reused for the same window.")
    parser.add_argument("--source", choices=("tape", "digital"), default=None,
                        help="What the recording came from, for --autotune (asked "
                             "when not given). 'tape' = Super-8, VHS or other tape "
                             "transfer. 'digital' (a camera or phone video, not a tape "
                             "transfer) leaves hiss reduction and declick off: on such "
                             "recordings they only strip the room tone and reverb.")
    parser.add_argument("--keep_candidates", action="store_true",
                        help="With --autotune: when a window is tuned now, keep every "
                             "candidate's audio (AAC) in the tuning cache so it can be "
                             "auditioned later (clean-audio-gui does).")
    parser.add_argument("--from_candidate", nargs=3, default=None,
                        metavar=("PLAN", "WINDOW", "INDEX"),
                        help="Render with one tuned candidate instead of autotune's pick: "
                             "PLAN is a tuning plan.json, WINDOW its window label (e.g. "
                             "24m45s-29m45s), INDEX the candidate's position in that "
                             "window's list. Sets the window and music separation from "
                             "the plan.")
    parser.add_argument("--autotune_dir", type=str, default=None,
                        help="Tuning cache for --autotune (default: "
                             "OUTPUT_DIR/_tuning/<video name>, shared with clean-audio-tune "
                             "and clean-audio-batch).")
    parser.add_argument("--atten_lim_db", type=parse_atten_lim, default=20.0,
                        help="Limit noise attenuation to this many dB (default: 20). "
                             "Lower (e.g. 10) for quiet or distant speech, or if voices "
                             "sound hollow; raise to 30, or pass 'none', for genuinely "
                             "noisy material.")
    parser.add_argument("--chunk_len_sec", type=float, default=10.0,
                        help="Denoiser chunk length in seconds (default: 10).")
    parser.add_argument("--crossfade_sec", type=float, default=0.5,
                        help="Crossfade between denoised chunks (default: 0.5).")

    parser.add_argument("--normalize", action="store_true",
                        help="Two-pass loudness normalisation of the CLEANED audio.")
    parser.add_argument("--target_lufs", type=parse_target_lufs, default=-16.0,
                        help="Target integrated loudness for --normalize (default: -16). "
                             "'auto' picks the loudest target a plain volume change can "
                             "reach, so loudnorm never compresses the dynamics; on by "
                             "default with --autotune.")
    parser.add_argument("--audio_gain", type=str, default=None,
                        help="Extra gain applied AFTER denoising (e.g. '6dB' or '2.0'), "
                             "followed by a limiter. Usually unnecessary with --normalize.")

    parser.add_argument("--reduce_hiss", action="store_true",
                        help="Extra pass to reduce steady background hiss/tape noise. "
                             "Runs right after denoising, before normalisation. Much more "
                             "effective on hiss than raising --atten_lim_db, and gentler "
                             "on the voices.")
    parser.add_argument("--hiss_method", choices=("profile", "patch"), default="profile",
                        help="How to reduce hiss. 'profile' (default) learns this "
                             "recording's own noise fingerprint from a speech-free stretch "
                             "and subtracts it — the Audacity approach, and free of the "
                             "warbling 'musical noise'. 'patch' uses a time-domain "
                             "smoother that needs no sample and touches speech slightly "
                             "less, but leaves some warble.")
    parser.add_argument("--noise_profile_at", type=parse_time_string, default=None,
                        metavar="TIME",
                        help="Where to sample the noise for 'profile' mode, measured from "
                             "the start of the processed window (e.g. '12s'). Defaults to "
                             "the quietest second, found automatically.")
    parser.add_argument("--noise_profile_len", type=positive_time, default=1.0,
                        metavar="SECONDS",
                        help="Length of the noise sample for 'profile' mode (default: 1).")
    parser.add_argument("--noise_profile_max_excess_db", type=float, default=6.0,
                        help="Warn when the automatically chosen noise sample sits more "
                             "than this many dB above the window's quietest frames, i.e. "
                             "probably contains speech (default: 6).")
    parser.add_argument("--hiss_nr", type=float, default=12.0,
                        help="Strength for 'profile' mode in dB, 0.01-97 (default: 12). "
                             "Raise for more hiss removal.")
    parser.add_argument("--hiss_nf", type=float, default=-40.0,
                        help="Noise floor for 'profile' mode in dB, -80 to -20 "
                             "(default: -40).")
    parser.add_argument("--hiss_gain_smooth", type=float, default=0.0,
                        help="Gain smoothing for 'profile' mode, 0-50 (default: 0). "
                             "Raise if you still hear warbling.")
    parser.add_argument("--hiss_strength", type=float, default=0.0003,
                        help="Strength for 'patch' mode (default: 0.0003). 0.0006 removes "
                             "noticeably more; past ~0.001 you start to hear warbling.")
    parser.add_argument("--hiss_window", type=float, default=0.0025,
                        help="Patch search window for 'patch' mode, in seconds (default: "
                             "0.0025). Wider removes more hiss, with more warble.")

    parser.add_argument("--declick", action="store_true",
                        help="Extra spectral pass to reduce mechanical tick/click noise "
                             "(projector, camera motor, film transfer). Runs last, after "
                             "normalisation. Removes roughly a further 20%% of clicks that "
                             "DeepFilterNet leaves behind.")
    parser.add_argument("--declick_nr", type=float, default=12.0,
                        help="Declick strength in dB, 0.01-97 (default: 12). "
                             "Raise for more reduction, lower if speech dulls.")
    parser.add_argument("--declick_nf", type=float, default=-28.0,
                        help="Declick noise floor in dB, -80 to -20 (default: -28).")

    parser.add_argument("--separate_music", action="store_true",
                        help="Split speech from music with Demucs before denoising.")
    parser.add_argument("--music_level_db", type=parse_music_level, default=-12.0,
                        help="Level to mix the separated music back under the speech "
                             "(default: -12). Pass 'off' to drop music entirely. With "
                             "--autotune it is chosen per window unless you give it.")
    parser.add_argument("--separation_device", type=str, default="auto",
                        choices=("auto", "cpu", "mps"),
                        help="Device for Demucs (default: auto = try MPS, fall back to CPU).")
    parser.add_argument("--separation_segment", type=float, default=None,
                        help="Demucs segment length in seconds (memory guard). "
                             "Auto-selected for long inputs.")

    parser.add_argument("--precise_cut", action="store_true",
                        help="Re-encode the video so the cut lands exactly on the "
                             "requested timestamp instead of snapping to a keyframe. "
                             "Slower, and the video is no longer bit-identical.")
    parser.add_argument("--video_crf", type=int, default=18,
                        help="x264 CRF used by --precise_cut (default: 18).")
    parser.add_argument("--audio_bitrate", type=str, default="192k",
                        help="AAC bitrate for the merged video (default: 192k).")

    advanced = parser.add_argument_group(
        "Advanced thresholds",
        "Rarely needed. Every level and tolerance the pipeline uses is exposed here.")
    advanced.add_argument("--target_tp", type=float, default=DEFAULT_TARGET_TP,
                          help="True-peak ceiling for --normalize, dBTP (default: -1.5).")
    advanced.add_argument("--target_lra", type=float, default=DEFAULT_TARGET_LRA,
                          help="Loudness range target for --normalize, LU (default: 11).")
    advanced.add_argument("--target_lufs_max", type=float, default=-16.0,
                          help="Loudest target --target_lufs auto may choose (default: -16).")
    advanced.add_argument("--target_lufs_min", type=float, default=-24.0,
                          help="Quietest target --target_lufs auto may choose; below it, "
                               "gentle compression is accepted instead (default: -24).")
    advanced.add_argument("--limiter_ceiling", type=float, default=DEFAULT_LIMITER_CEILING,
                          help="Limiter ceiling after --audio_gain and the music mix, "
                               "linear 0-1 (default: 0.97).")
    advanced.add_argument("--silence_warn_lufs", type=float,
                          default=DEFAULT_SILENCE_WARN_LUFS,
                          help="Warn when the cleaned audio measures below this before "
                               "normalising, LUFS (default: -45).")
    advanced.add_argument("--loudness_tolerance_db", type=float,
                          default=DEFAULT_LOUDNESS_TOLERANCE_DB,
                          help="Warn when the result lands further than this from "
                               "--target_lufs, dB (default: 2).")
    advanced.add_argument("--separation_auto_segment_sec", type=float,
                          default=DEFAULT_SEPARATION_AUTO_SEGMENT_SEC,
                          help="Windows longer than this get a 7 s Demucs segment to "
                               "bound memory, seconds (default: 600).")

    parser.add_argument("--verbose", action="store_true",
                        help="Print every ffmpeg command line before running it.")
    events.add_argument(parser)
    parser.add_argument("--keep_temp", type=str, default=None, metavar="DIR",
                        help="Copy intermediate WAVs into DIR for debugging: 01_raw, "
                             "02_vocals/02_music (with --separate_music), 03_denoised, "
                             "04_final (everything after DeepFilterNet).")
    return parser


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _prepare_output_dir(path):
    output_dir = os.path.abspath(os.path.expanduser(path))
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Could not create --output_dir '{output_dir}': {exc}") from exc
    if not os.path.isdir(output_dir):
        raise RuntimeError(f"--output_dir '{output_dir}' exists but is not a directory.")

    probe = os.path.join(output_dir, f".write_test_{os.getpid()}")
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
        os.remove(probe)
    except OSError as exc:
        raise RuntimeError(f"--output_dir '{output_dir}' is not writable: {exc}") from exc
    return output_dir


def _validate_output_name(name):
    if name is None:
        return None
    separators = [os.sep] + ([os.altsep] if os.altsep else [])
    if not name.strip() or name in (".", "..") or os.path.basename(name) != name:
        raise RuntimeError(
            f"-o/--output must be a plain file name, got '{name}'. "
            "Pass the directory with --output_dir.")
    if any(sep in name for sep in separators):
        raise RuntimeError(
            "-o/--output is now a FILENAME placed inside --output_dir, not a path.\n"
            f"Got: '{name}'. Pass the directory with --output_dir instead."
        )
    return name


_NAME_WINDOW = re.compile(
    r"(?<![\dhms])((?:\d+h)?\d+m\d+s)-((?:\d+h)?\d+m\d+s|end)(?![\dhms])", re.I)


def _validate_tag(tag):
    if tag is None:
        return None
    separators = [os.sep] + ([os.altsep] if os.altsep else [])
    if not tag.strip() or any(sep in tag for sep in separators) or tag in (".", ".."):
        raise RuntimeError(f"--tag must be a short label without path separators, got '{tag}'.")
    return tag.strip()


def check_name_window(name, start, end):
    """Refuse a hand-given name whose written time window is not the one processed.

    start/end are seconds (end None = to the end of the file). A name with no
    window in it is fine. Returns None or raises RuntimeError.
    """
    match = _NAME_WINDOW.search(os.path.splitext(name)[0])
    if not match:
        return
    named_start = parse_time_string(match.group(1))
    named_end = None if match.group(2).lower() == "end" else parse_time_string(match.group(2))
    start_ok = abs(named_start - start) < 1
    end_ok = (named_end is None and end is None) or (
        named_end is not None and end is not None and abs(named_end - end) < 1)
    if start_ok and end_ok:
        return
    actual = f"{format_time_filename(start)}-{format_time_filename(end)}"
    raise RuntimeError(
        f"-o/--output says {match.group(0)}, but this run processes {actual}.\n"
        f"Drop -o to get the right name automatically, and add a label with --tag "
        f"(e.g. --tag no-music), or fix the time in the name.")


def plan_window(requested_start, actual_start, requested_duration, file_duration):
    """(snap_delta, actual_duration, work_duration) for the processed window.

    actual_duration is what is cut from the input: the requested length plus the
    keyframe snap, or everything to the end of the file. It is only None when the
    file's duration is unknown. Always passing it to the merge as -t matters:
    -shortest alone overshoots when video is stream-copied.
    """
    snap_delta = requested_start - actual_start
    if requested_duration is not None:
        actual_duration = requested_duration + snap_delta
    elif file_duration is not None:
        actual_duration = max(0.0, file_duration - actual_start)
    else:
        actual_duration = None
    return snap_delta, actual_duration, actual_duration


def derive_output_name(input_path, requested_start, requested_duration, file_duration,
                       has_window, compare_mode, audio_only, tag=None):
    """Output filename: the input's name plus the window the user asked for (+ --tag)."""
    base_name, _ = os.path.splitext(os.path.basename(input_path))
    time_suffix = ""
    if has_window:
        if requested_duration is not None:
            end_sec = requested_start + requested_duration
        else:
            end_sec = file_duration
        time_suffix = (f"_{format_time_filename(requested_start)}"
                       f"-{format_time_filename(end_sec)}")
    if tag:
        time_suffix += f"_{tag}"
    if compare_mode:
        return f"{base_name}_compare{time_suffix}.wav"
    if audio_only:
        return f"{base_name}{time_suffix}.wav"
    return f"{base_name}{time_suffix}.mp4"


def _partial_path(final_path):
    base, ext = os.path.splitext(final_path)
    return f"{base}.partial{ext}"


def _format_failure(exc):
    if isinstance(exc, subprocess.CalledProcessError):
        detail = ""
        if exc.stderr:
            lines = [line for line in str(exc.stderr).strip().splitlines() if line.strip()]
            if lines:
                detail = "\n" + "\n".join(lines[-12:])
        command = exc.cmd if isinstance(exc.cmd, str) else shlex.join(map(str, exc.cmd))
        return (f"Command failed with exit code {exc.returncode}.{detail}\n\n"
                f"Command:\n    {command}")
    return str(exc) or repr(exc)


# The knobs --autotune chooses; passing one explicitly alongside it is an error.
AUTOTUNED_OPTIONS = ("--atten_lim_db", "--reduce_hiss", "--hiss_method", "--hiss_nr",
                     "--hiss_strength", "--declick", "--declick_nr")


def _explicit(argv, option):
    return any(a == option or a.startswith(option + "=") for a in argv)


def _autotune(args, argv, input_path, output_dir, start, duration):
    """Pick the tuned settings for this window and apply them to args."""
    from . import tune

    clash = [o for o in AUTOTUNED_OPTIONS if _explicit(argv, o)]
    if clash:
        raise RuntimeError(
            f"--autotune chooses {', '.join(clash)} itself; drop "
            f"{'it' if len(clash) == 1 else 'them'}, or drop --autotune to set them by hand.")
    if duration is None:
        raise RuntimeError("--autotune needs a known window length "
                           "(--process_n_seconds, or a file whose duration ffprobe can read).")
    if duration > 600:
        print_warning(f"Autotuning {duration / 60:.0f} minutes of audio takes roughly "
                      f"{duration * 0.8 / 60:.0f} minutes. Shorter windows tune faster.")
    tune_dir = Path(args.autotune_dir) if args.autotune_dir else \
        tune.default_tune_dir(output_dir, input_path, args.separate_music)

    try:
        args.source = tune.resolve_source(args.source)
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from None
    print_info(f"\nAutotune: choosing settings for this window (cache: {tune_dir})")
    began = time.time()
    # With --separate_music autotune also picks the music bed level, unless the
    # user set --music_level_db: then it tunes the rest around that level.
    fixed_music = None
    if args.separate_music and _explicit(argv, "--music_level_db"):
        fixed_music = "off" if args.music_level_db is None else f"{args.music_level_db:g}"
    picked, entry, reused, plan_path = tune.autotune_window(
        input_path, start, start + duration, tune_dir, args.separate_music, fixed_music,
        args.source, keep_candidates=args.keep_candidates)
    _apply_candidate(args, picked)

    how = "reused earlier tuning" if reused else \
        f"tuned {len(entry['candidates'])} candidates in {time.time() - began:.0f}s"
    print_success(f"Autotune picked: {picked.describe()} ({how})")
    print_info(f"  fidelity {picked.fidelity:.3f}, hiss removed {picked.hiss_gain_db:+.1f} dB, "
               f"ticks {entry['source']['clicks']:.1f} → {picked.clicks:.1f}/s")
    record = _tuning_record(plan_path, entry, picked)
    record["reused"] = reused
    if entry.get("user_pick") is not None:
        record["chosen_by"] = "user"
    return record


_PICK_FIELDS = ("atten", "hiss", "declick", "music_level_db", "fidelity", "hiss_gain_db",
                "warble", "clicks", "score")


def _tuning_record(plan_path, entry, picked):
    return {
        "cache": str(plan_path),
        "window": entry["label"],
        "criteria": json.loads(Path(plan_path).read_text(encoding="utf-8")).get("criteria"),
        "music_bed": entry.get("music"),
        "picked": {k: getattr(picked, k) for k in _PICK_FIELDS},
        "candidates_tried": len(entry["candidates"]),
    }


def _apply_candidate(args, picked):
    """Set the options a tuned candidate stands for."""
    args.atten_lim_db = picked.atten
    args.reduce_hiss = picked.hiss != "off"
    if args.reduce_hiss:
        method, value = picked.hiss.split(":")
        args.hiss_method = method
        if method == "profile":
            args.hiss_nr = float(value)
        else:
            args.hiss_strength = float(value)
    if args.separate_music:
        args.music_level_db = picked.music_level_db
    args.declick = bool(picked.declick)
    if args.declick:
        args.declick_nr = picked.declick_nr


def load_candidate(plan_path, label, index):
    """(plan, window entry, Candidate) for --from_candidate; RuntimeError if absent."""
    from . import tune

    plan_path = Path(os.path.abspath(os.path.expanduser(plan_path)))
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read the tuning plan {plan_path}: {exc}") from None
    entry = next((w for w in plan.get("windows", []) if w["label"] == label), None)
    if entry is None:
        known = ", ".join(w["label"] for w in plan.get("windows", [])) or "none"
        raise RuntimeError(f"{plan_path.name} has no window {label} (it has: {known}).")
    try:
        candidate = tune.Candidate(**entry["candidates"][int(index)])
    except (ValueError, IndexError):
        raise RuntimeError(f"Window {label} has candidates 0-{len(entry['candidates']) - 1}; "
                           f"got '{index}'.") from None
    return plan_path, plan, entry, candidate


def _from_candidate_window(args, argv, input_path):
    """Point args at the plan window --from_candidate names. Returns the loaded parts."""
    if args.autotune:
        raise RuntimeError("--from_candidate and --autotune both choose the settings; "
                           "use one.")
    clash = [o for o in AUTOTUNED_OPTIONS if _explicit(argv, o)]
    if clash:
        raise RuntimeError(f"--from_candidate sets {', '.join(clash)} itself; drop them.")
    plan_path, plan, entry, candidate = load_candidate(*args.from_candidate)
    if os.path.abspath(plan.get("source", "")) != input_path:
        raise RuntimeError(f"{plan_path} was tuned on {plan.get('source')}, "
                           f"not {input_path}.")
    start, length = entry["start_sec"], entry["end_sec"] - entry["start_sec"]
    for option, value, wanted in (("--skip_first_n_sec", args.skip_first_n_sec, start),
                                  ("--process_n_seconds", args.process_n_seconds, length)):
        if _explicit(argv, option) and value is not None and abs(value - wanted) > 1e-3:
            raise RuntimeError(f"{option} disagrees with window {entry['label']}; "
                               f"--from_candidate sets the window itself.")
    args.skip_first_n_sec, args.process_n_seconds = start, length
    args.separate_music = bool((plan.get("grid") or {}).get("separate_music"))
    return plan_path, entry, candidate


def _from_candidate(args, loaded):
    plan_path, entry, candidate = loaded
    _apply_candidate(args, candidate)
    index = int(args.from_candidate[2])
    print_success(f"Using candidate {index} of {entry['label']}: {candidate.describe()}")
    record = _tuning_record(plan_path, entry, candidate)
    record.update(chosen_by="user", candidate_index=index,
                  autotune_pick={k: entry["picked"].get(k) for k in _PICK_FIELDS})
    return record


def _stage_chain(args, o, compare_mode, audio_only, auto_loudness=False):
    atten = "unlimited" if args.atten_lim_db is None else f"{args.atten_lim_db:g}"
    chain = ["autotune"] if args.autotune else []
    chain.append("extract")
    if args.separate_music:
        chain.append("separate (Demucs)")
    chain.append(f"denoise (atten {atten})")
    if o.hiss_method == "profile":
        chain.append(f"hiss (profile, nr {o.hiss_nr:g})")
    elif o.hiss_method == "patch":
        chain.append(f"hiss (patch, strength {o.hiss_strength:g})")
    if o.normalize:
        chain.append(f"loudnorm (auto, ≤ {o.target_lufs:g} LUFS)" if auto_loudness
                     else f"loudnorm ({o.target_lufs:g} LUFS)")
    if o.audio_gain:
        chain.append(f"gain {o.audio_gain}")
    if o.music_level_db is not None:
        chain.append(f"music bed ({o.music_level_db:g} dB)")
    elif args.separate_music:
        chain.append("music dropped")
    if o.declick:
        chain.append(f"declick (nr {o.declick_nr:g})")
    chain.append("A/B preview" if compare_mode else "write WAV" if audio_only else "merge")
    return chain


def _choose_loudness_target(o, measured, args, results, denoised_wav, duration):
    """Set the loudest plain-volume target, re-measure for it, and record the choice."""
    from . import postprocess

    pick = postprocess.auto_loudness_target(measured, args.target_lufs_max,
                                            args.target_lufs_min, o.target_tp, o.target_lra)
    o.target_lufs, o.target_lra = pick.target_lufs, pick.target_lra
    args.target_lufs, args.target_lra = pick.target_lufs, pick.target_lra   # for .params.json
    results["loudness_target"] = {"chosen_lufs": pick.target_lufs,
                                  "chosen_lra": pick.target_lra,
                                  "reachable_without_compression_lufs": round(
                                      pick.reachable_lufs, 2),
                                  "linear": pick.linear}
    if pick.linear:
        why = ("the cap" if pick.target_lufs == args.target_lufs_max else
               f"the loudest a plain volume change reaches with peaks under "
               f"{o.target_tp:g} dBTP")
        print_success(f"Loudness target chosen: {pick.target_lufs:g} LUFS ({why}).")
    else:
        # Don't predict compression: the margin is conservative, and the render reports
        # the mode loudnorm actually used.
        print_info(f"Loudness target chosen: {pick.target_lufs:g} LUFS, the floor "
                   f"(--target_lufs_min). A plain volume change reaches about "
                   f"{pick.reachable_lufs:.1f} LUFS here; the render reports whether "
                   f"loudnorm had to compress.")
    # loudnorm's second-pass correction depends on the target: measure again for it.
    remeasured = postprocess.analyse(o, denoised_wav, duration,
                                     label="Re-measuring for the chosen target")
    return remeasured or measured


def _report_render(o, result, args):
    """One line per stage the shared render pass performed, with what it did."""
    if o.hiss_method == "profile":
        print_success(f"Hiss reduction (profile, nr {o.hiss_nr:g}) — noise sampled at "
                      f"{o.noise_at:.2f}s")
    elif o.hiss_method == "patch":
        print_success(f"Hiss reduction (patch, strength {o.hiss_strength:g})")
    if o.normalize:
        mode = result.loudnorm_mode or "unknown mode"
        passes = "two-pass" if o.measured else "single-pass"
        print_success(f"Loudness normalised ({passes}, {mode}, target {o.target_lufs:g} LUFS)")
        if result.loudnorm_mode == "dynamic" and o.measured:
            need = o.target_lufs - float(o.measured["input_i"])
            over = float(o.measured["input_tp"]) + need - o.target_tp
            print_warning(
                f"loudnorm compressed the dynamics: reaching {o.target_lufs:g} LUFS needs "
                f"{need:+.1f} dB, which would put peaks {over:.1f} dB over the "
                f"{o.target_tp:g} dBTP ceiling. Quiet passages are brought up relative "
                f"to loud ones. For a plain volume change instead, lower --target_lufs "
                f"to about {o.target_lufs - over:.0f}.")
    if o.audio_gain:
        print_success(f"Gain {o.audio_gain}, limited at {o.limiter_ceiling:g}")
    if o.music_level_db is not None:
        print_success(f"Music bed mixed back at {o.music_level_db:g} dB")
    if o.declick:
        print_success(f"Declick (nr {o.declick_nr:g})")

    if result.lufs is None:
        print_warning("Could not measure the loudness of the final audio.")
        return
    peak = "" if result.true_peak is None else f", true peak {result.true_peak:.1f} dBTP"
    target = f" (target {o.target_lufs:g})" if o.normalize else ""
    print_success(f"Final audio: {result.lufs:.1f} LUFS{peak}{target}")
    if o.normalize and abs(result.lufs - o.target_lufs) > args.loudness_tolerance_db:
        print_warning(
            f"That is {abs(result.lufs - o.target_lufs):.1f} dB off the target. loudnorm "
            f"caps its gain to keep the true peak under {o.target_tp:g} dBTP, so quiet, "
            f"peaky material lands short. Use --audio_gain for a limited extra boost.")


class _Steps:
    def __init__(self, total):
        self.total = total
        self.index = 0

    def start(self, title):
        self.index += 1
        print_step(self.index, self.total, title)


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def _run(args, argv=None):
    input_path = os.path.abspath(os.path.expanduser(args.input_video))
    if not os.path.exists(input_path):
        raise RuntimeError(f"Input file not found at: {input_path}")

    output_dir = _prepare_output_dir(args.output_dir)
    _validate_output_name(args.output)
    args.tag = _validate_tag(args.tag)
    if args.tag and args.output:
        raise RuntimeError("--tag labels the automatic name; with -o, write the label into "
                           "the name yourself (or drop -o).")

    from_candidate = None
    if args.from_candidate:
        from_candidate = _from_candidate_window(args, argv or [], input_path)
    if args.keep_candidates and not args.autotune:
        print_warning("--keep_candidates only applies with --autotune.")

    keep_temp_dir = None
    if args.keep_temp:
        keep_temp_dir = os.path.abspath(os.path.expanduser(args.keep_temp))
        os.makedirs(keep_temp_dir, exist_ok=True)

    file_duration = ffmpeg_utils.probe_duration(input_path)

    compare_mode = args.compare is not None
    audio_only = args.filter_audio_only
    wants_video = not audio_only and not compare_mode
    if wants_video and not ffmpeg_utils.has_video_stream(input_path):
        print_warning("Input has no video stream; writing a cleaned WAV instead.")
        wants_video = False
        audio_only = True

    requested_start = args.skip_first_n_sec or 0.0
    requested_duration = args.process_n_seconds
    if compare_mode:
        preview = max(0.1, float(args.compare))
        requested_duration = min(requested_duration, preview) if requested_duration else preview

    if file_duration is not None and requested_start >= file_duration:
        raise RuntimeError(
            f"--skip_first_n_sec ({requested_start:.2f}s) is at or past the end of the "
            f"input ({file_duration:.2f}s)."
        )

    # Keyframe alignment: stream-copy seeking lands on a keyframe, so cutting the
    # audio at an arbitrary timestamp would desync it from the copied video.
    actual_start = requested_start
    if wants_video and not args.precise_cut and requested_start > 0:
        keyframe = ffmpeg_utils.probe_keyframe_at_or_before(input_path, requested_start)
        if keyframe is None:
            print_warning("Could not locate a keyframe before the requested start; "
                          "audio and video may drift. Consider --precise_cut.")
        elif keyframe < requested_start - 1e-3:
            actual_start = keyframe
            print_info(
                f"Requested start {requested_start:.2f}s → snapped to keyframe at "
                f"{actual_start:.2f}s to keep the video lossless and in sync "
                f"(use --precise_cut for an exact cut)."
            )

    snap_delta, actual_duration, work_duration = plan_window(
        requested_start, actual_start, requested_duration, file_duration)

    default_name = derive_output_name(
        input_path=input_path,
        requested_start=requested_start,
        requested_duration=requested_duration,
        file_duration=file_duration,
        has_window=(args.skip_first_n_sec is not None
                    or args.process_n_seconds is not None or compare_mode),
        compare_mode=compare_mode,
        audio_only=audio_only,
        tag=args.tag,
    )
    if args.output:
        if requested_duration is not None:
            named_end = requested_start + requested_duration
        else:
            named_end = file_duration
        check_name_window(args.output, requested_start, named_end)
    output_path = os.path.join(output_dir, args.output or default_name)

    # The derived name is now just the input's name plus the window, so with no
    # window given it matches the input exactly. Never write over the source.
    if os.path.abspath(output_path) == input_path:
        raise RuntimeError(
            f"The output would overwrite the input file:\n    {input_path}\n"
            f"Give a different --output_dir, or name the file with -o."
        )

    print_info(f"Input Video:  {input_path}")
    print_info(f"Output Dir:   {output_dir}")
    print_info(f"Output File:  {os.path.basename(output_path)}")
    replacing = os.path.exists(output_path)
    if replacing:
        from .provenance import sidecar_path
        replaced = [os.path.basename(output_path)]
        if os.path.exists(sidecar_path(output_path)):
            replaced.append(os.path.basename(str(sidecar_path(output_path))))
        print_warning(f"{' and '.join(replaced)} already exist(s) in the output folder "
                      f"and will be replaced when this run finishes.")
    if work_duration:
        print_info(f"Window:       {work_duration:.2f}s starting at {actual_start:.2f}s")
    else:
        print_warning("Could not determine duration; progress bars will be limited.")
    if compare_mode:
        print_info(f"Compare mode: auditioning the first {requested_duration:.1f}s only.")
    if args.audio_gain and args.normalize:
        print_warning("--audio_gain on top of --normalize is usually unnecessary; "
                      "the gain is applied after normalisation and then limited.")

    if args.source and not args.autotune:
        print_warning("--source only steers --autotune; without it, your own settings apply.")

    results = {}
    if args.autotune:
        tune_length = requested_duration
        if tune_length is None and file_duration is not None:
            tune_length = max(0.0, file_duration - requested_start)
        results["autotune"] = _autotune(args, argv or [], input_path, output_dir,
                                        requested_start, tune_length)
    elif from_candidate:
        results["autotune"] = _from_candidate(args, from_candidate)

    # Loudness target: a number, or chosen from the cleaned audio ('auto').
    # --autotune implies auto unless the user set a target themselves.
    auto_loudness = args.normalize and (
        args.target_lufs == "auto"
        or ((args.autotune or args.from_candidate)
            and not _explicit(argv or [], "--target_lufs")))
    if auto_loudness:
        args.target_lufs = args.target_lufs_max      # provisional, for the first measurement

    graph_opts = graph.GraphOptions(
        sample_rate=SAMPLE_RATE,
        hiss_method=args.hiss_method if args.reduce_hiss else None,
        hiss_nr=args.hiss_nr,
        hiss_nf=args.hiss_nf,
        hiss_gain_smooth=args.hiss_gain_smooth,
        hiss_strength=args.hiss_strength,
        hiss_window=args.hiss_window,
        noise_len=args.noise_profile_len,
        normalize=args.normalize,
        target_lufs=args.target_lufs,
        target_tp=args.target_tp,
        target_lra=args.target_lra,
        audio_gain=args.audio_gain,
        limiter_ceiling=args.limiter_ceiling,
        music_level_db=args.music_level_db if args.separate_music else None,
        declick=args.declick,
        declick_nr=args.declick_nr,
        declick_nf=args.declick_nf,
    )
    render_stages = graph.stage_names(graph_opts)

    # Rule: the user can always see which stages run, which pass is active and
    # how far it is. Print the whole chain once, before any work starts.
    print_info("Stages:       " + " → ".join(_stage_chain(args, graph_opts, compare_mode,
                                                         audio_only, auto_loudness)))

    total_steps = 2  # extract + denoise
    if args.separate_music:
        total_steps += 1
    if args.normalize:
        total_steps += 1  # loudness analysis pass
    if render_stages:
        total_steps += 1  # render pass
    total_steps += 1  # final output stage
    steps = _Steps(total_steps)

    # --noise_profile_at is relative to the window the user asked for, but the
    # extracted audio starts snap_delta earlier, at the keyframe.
    noise_at = None
    if args.reduce_hiss and args.hiss_method == "profile" and args.noise_profile_at is not None:
        noise_at = args.noise_profile_at + snap_delta
        if work_duration is not None and noise_at + args.noise_profile_len > work_duration:
            latest = max(0.0, work_duration - snap_delta - args.noise_profile_len)
            raise RuntimeError(
                f"--noise_profile_at {args.noise_profile_at:g}s leaves no room for a "
                f"{args.noise_profile_len:g}s sample inside the window; use a value "
                f"between 0 and {latest:.2f}s.")

    temp_dir = tempfile.mkdtemp(prefix="video_audio_cleaner_")
    procs.register_path(temp_dir)
    try:
        raw_wav = os.path.join(temp_dir, "01_raw.wav")
        vocals_wav = os.path.join(temp_dir, "02_vocals.wav")
        music_wav = os.path.join(temp_dir, "02_music.wav")
        denoised_wav = os.path.join(temp_dir, "03_denoised.wav")
        final_wav = os.path.join(temp_dir, "04_final.wav")

        # -- Extract, completely unprocessed -------------------------------
        steps.start("Extracting audio track from video")
        extract_cmd = ["ffmpeg", "-y"]
        if actual_start > 0:
            extract_cmd += ["-ss", f"{actual_start:.6f}"]
        extract_cmd += ["-i", input_path, "-vn",
                        "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE)]
        if actual_duration is not None:
            extract_cmd += ["-t", f"{actual_duration:.6f}"]
        extract_cmd.append(raw_wav)
        ffmpeg_utils.run_ffmpeg(extract_cmd, work_duration, "Extracting")
        print_success("Audio extracted (unfiltered 32-bit float).")

        speech_wav = raw_wav

        # -- Optional Demucs separation ------------------------------------
        if args.separate_music:
            steps.start("Separating speech from music (Demucs)")
            from . import separation

            segment = args.separation_segment
            if segment is None and work_duration and work_duration > args.separation_auto_segment_sec:
                segment = 7.0  # htdemucs rejects segments longer than its training window
            separation.separate_to_files(
                input_wav=raw_wav,
                vocals_path=vocals_wav,
                music_path=music_wav,
                target_sr=SAMPLE_RATE,
                duration_sec=work_duration,
                device=args.separation_device,
                segment=segment,
            )
            speech_wav = vocals_wav
            print_success("Speech and music stems separated.")

        # -- Denoise --------------------------------------------------------
        steps.start("Filtering noise using DeepFilterNet")
        from . import denoise

        atten = args.atten_lim_db
        print_info(f"Attenuation limit: {'unlimited' if atten is None else f'{atten:g} dB'}")
        denoise.denoise_file(
            input_wav=speech_wav,
            output_wav=denoised_wav,
            atten_lim_db=atten,
            chunk_len_sec=args.chunk_len_sec,
            crossfade_sec=args.crossfade_sec,
        )
        print_success("Audio filtered successfully.")
        processed_wav = denoised_wav

        # -- Everything after DeepFilterNet: one analysis pass, one render pass
        if render_stages:
            from . import hiss, postprocess

            if graph_opts.hiss_method == "profile":
                graph_opts.noise_at = hiss.choose_noise_sample(
                    denoised_wav, noise_at, args.noise_profile_len,
                    args.noise_profile_max_excess_db)
            graph_opts.latency = postprocess.measure_latencies(graph_opts)

            if args.normalize:
                measured_on = ["hiss"] if graph_opts.hiss_method else []
                steps.start("Analysing loudness ("
                            + " → ".join(measured_on + ["loudnorm measure"]) + ")")
                measured = postprocess.analyse(graph_opts, denoised_wav, work_duration,
                                               label="Analysing loudness")
                if measured is None:
                    print_warning("Loudness measurement unusable (near-silent input?); "
                                  "falling back to single-pass loudnorm.")
                elif float(measured["input_i"]) < args.silence_warn_lufs:
                    print_warning(
                        f"Cleaned audio measures {float(measured['input_i']):.1f} LUFS, "
                        f"which is close to silence. Normalising will apply a very large "
                        f"boost — check --atten_lim_db is not stripping the speech.")
                else:
                    print_success(f"Measured {float(measured['input_i']):.1f} LUFS "
                                  f"before normalising.")
                if auto_loudness and measured is not None:
                    measured = _choose_loudness_target(graph_opts, measured, args, results,
                                                       denoised_wav, work_duration)
                graph_opts.measured = measured
                if measured is not None:
                    results["lufs_before_normalize"] = float(measured["input_i"])

            steps.start("Rendering: " + " → ".join(render_stages))
            result = postprocess.render(
                graph_opts, denoised_wav,
                music_wav if graph_opts.music_level_db is not None else None,
                final_wav, work_duration,
                label="Rendering " + "→".join(render_stages))
            _report_render(graph_opts, result, args)
            processed_wav = final_wav
            results["final_lufs"] = result.lufs
            results["loudnorm_mode"] = result.loudnorm_mode
            results["final_true_peak_dbtp"] = result.true_peak
            if graph_opts.noise_at is not None:
                results["noise_profile_sampled_at_sec"] = round(graph_opts.noise_at, 3)

        # -- Final output ---------------------------------------------------
        if compare_mode:
            steps.start("Building A/B comparison file")
            _write_compare(raw_wav, processed_wav, output_path, work_duration)
        elif audio_only:
            steps.start("Writing cleaned audio track")
            _write_wav_output(processed_wav, output_path, work_duration)
        else:
            steps.start("Merging cleaned audio track back into video")
            _merge_video(input_path, processed_wav, output_path, actual_start,
                         actual_duration, work_duration, args)

        if keep_temp_dir:
            _copy_intermediates(temp_dir, keep_temp_dir)

        sidecar = _write_sidecar(output_path, args, input_path, output_dir, requested_start,
                                 requested_duration, actual_start, actual_duration, results)
        events.emit("result", output=output_path,
                    sidecar=None if sidecar is None else str(sidecar),
                    replaced=replacing, results=results)
        print_success(f"Processing complete! Saved to: {output_path}")
        if compare_mode:
            print_success(f"A/B preview (original → 0.5s silence → processed):\n"
                          f"    {output_path}")
    finally:
        _cleanup_temp(temp_dir, keep_temp_dir)


def _cleanup_temp(temp_dir, keep_temp_dir=None):
    """Stop every child first, then copy intermediates, then delete.

    On an interrupt a child ffmpeg may still be writing here; copying or
    deleting before it has exited would copy half-written files.
    """
    procs.terminate_all()
    if keep_temp_dir and os.path.isdir(temp_dir):
        _copy_intermediates(temp_dir, keep_temp_dir)
    procs.unregister_path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _write_sidecar(output_path, args, input_path, output_dir, requested_start,
                   requested_duration, actual_start, actual_duration, results):
    """<output>.params.json: every parameter (defaults too) and how to reproduce it."""
    from . import provenance

    resolved = argparse.Namespace(**vars(args))
    # Record what actually ran: autotune's picks are already in these values, so
    # the reproduce command renders the same file without tuning again.
    resolved.autotune = False
    resolved.autotune_dir = None
    resolved.keep_candidates = False
    resolved.from_candidate = None
    resolved.input_video = input_path
    resolved.output_dir = output_dir
    resolved.output = os.path.basename(output_path)
    resolved.tag = None  # already part of the output name
    window = {
        "requested_start_sec": requested_start,
        "requested_duration_sec": requested_duration,
        "actual_start_sec": round(actual_start, 6),
        "actual_duration_sec": None if actual_duration is None else round(actual_duration, 6),
        "keyframe_snap_sec": round(requested_start - actual_start, 6),
    }
    try:
        path = provenance.write(output_path, build_parser(), resolved, window, results)
        print_info(f"Parameters recorded in {path.name}")
        return path
    except OSError as exc:
        print_warning(f"Could not write the parameters file: {exc}")
        return None


def _copy_intermediates(temp_dir, keep_temp_dir):
    try:
        for name in sorted(os.listdir(temp_dir)):
            source = os.path.join(temp_dir, name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(keep_temp_dir, name))
    except OSError as exc:
        print_warning(f"Could not copy intermediates to --keep_temp dir: {exc}")


def _write_wav_output(processed_wav, output_path, work_duration):
    partial = _partial_path(output_path)
    procs.register_path(partial)
    cmd = ["ffmpeg", "-y", "-i", processed_wav,
           "-acodec", "pcm_s24le", "-ar", str(SAMPLE_RATE), partial]
    ffmpeg_utils.run_ffmpeg(cmd, work_duration, "Writing")
    os.replace(partial, output_path)
    procs.unregister_path(partial)


def _write_compare(raw_wav, processed_wav, output_path, work_duration):
    partial = _partial_path(output_path)
    procs.register_path(partial)
    fmt = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    filter_complex = (
        f"[0:a]{fmt}[a0];"
        f"[1:a]{fmt}[a1];"
        f"[2:a]{fmt}[a2];"
        f"[a0][a1][a2]concat=n=3:v=0:a=1[out]"
    )
    cmd = ["ffmpeg", "-y",
           "-i", raw_wav,
           "-f", "lavfi", "-t", "0.5", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
           "-i", processed_wav,
           "-filter_complex", filter_complex, "-map", "[out]",
           "-acodec", "pcm_s24le", partial]
    total = (work_duration * 2 + 0.5) if work_duration else None
    ffmpeg_utils.run_ffmpeg(cmd, total, "Building preview")
    os.replace(partial, output_path)
    procs.unregister_path(partial)


def _merge_video(input_path, processed_wav, output_path, actual_start,
                 actual_duration, work_duration, args):
    partial = _partial_path(output_path)
    procs.register_path(partial)

    cmd = ["ffmpeg", "-y"]
    if actual_start > 0:
        cmd += ["-ss", f"{actual_start:.6f}"]
    cmd += ["-i", input_path, "-i", processed_wav]
    if args.precise_cut:
        cmd += ["-c:v", "libx264", "-crf", str(args.video_crf), "-preset", "medium",
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0",
            "-c:a", "aac", "-b:a", args.audio_bitrate]
    # -shortest alone overshoots when the video is stream-copied, leaving a tail
    # of silent video; an explicit -t cuts both streams at the same point.
    if actual_duration is not None:
        cmd += ["-t", f"{actual_duration:.6f}"]
    # No -avoid_negative_ts make_zero: it shifts every timestamp to absorb AAC's
    # encoder priming, which starts the video 21 ms late. The mp4 muxer's edit
    # list already handles the priming correctly.
    cmd += ["-shortest", partial]

    ffmpeg_utils.run_ffmpeg(cmd, work_duration, "Merging")
    os.replace(partial, output_path)
    procs.unregister_path(partial)


def main():
    procs.install_signal_handlers()
    args = build_parser().parse_args()
    ffmpeg_utils.VERBOSE = args.verbose
    with events.session("clean-audio", sys.argv[1:], args.progress):
        _main(args)


def _main(args):
    try:
        _run(args, sys.argv[1:])
    except KeyboardInterrupt:
        procs.cleanup_all()
        print_warning("Interrupted — cleaned up temporary files.")
        sys.exit(130)
    except ImportError as exc:
        procs.cleanup_all()
        print_error(
            f"A required package is missing: {exc}\n\n"
            f"You are running: {sys.executable}\n"
            "Install the dependencies with ./setup.sh, then run the tool with "
            "./venv/bin/clean-audio (or 'source venv/bin/activate' first)."
        )
        sys.exit(1)
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError) as exc:
        procs.cleanup_all()
        print_error(_format_failure(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
