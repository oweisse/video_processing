#!/usr/bin/env python3
"""Extract a window of audio from a video, clean it, and merge it back.

Pipeline order matters: audio is extracted completely unprocessed, denoised
(optionally after splitting music off), and only then loudness-normalised.
Normalising before denoising would amplify the noise the denoiser is meant to
remove, and can clip.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from . import ffmpeg_utils, procs
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


def limiter(ceiling):
    return f"alimiter=limit={ceiling:g}:level=false"


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
                             "Defaults to a name derived from the input and time window.")

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
    parser.add_argument("--target_lufs", type=float, default=-16.0,
                        help="Target integrated loudness for --normalize (default: -16).")
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
                             "smoother that needs no sample but smears slightly.")
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
                             "0.0025). This is what causes echo-like smearing.")

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
                             "(default: -12). Pass 'off' to drop music entirely.")
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

    parser.add_argument("--keep_temp", type=str, default=None, metavar="DIR",
                        help="Copy intermediate WAVs into DIR for debugging.")
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
                       has_window, compare_mode, audio_only):
    """Output filename: the input's name plus the window the user asked for."""
    base_name, _ = os.path.splitext(os.path.basename(input_path))
    time_suffix = ""
    if has_window:
        if requested_duration is not None:
            end_sec = requested_start + requested_duration
        else:
            end_sec = file_duration
        time_suffix = (f"_{format_time_filename(requested_start)}"
                       f"-{format_time_filename(end_sec)}")
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
        return f"Command failed with exit code {exc.returncode}.{detail}"
    return str(exc) or repr(exc)


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
def _run(args):
    input_path = os.path.abspath(os.path.expanduser(args.input_video))
    if not os.path.exists(input_path):
        raise RuntimeError(f"Input file not found at: {input_path}")

    output_dir = _prepare_output_dir(args.output_dir)
    _validate_output_name(args.output)

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
    )
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
    if work_duration:
        print_info(f"Window:       {work_duration:.2f}s starting at {actual_start:.2f}s")
    else:
        print_warning("Could not determine duration; progress bars will be limited.")
    if compare_mode:
        print_info(f"Compare mode: auditioning the first {requested_duration:.1f}s only.")
    if args.audio_gain and args.normalize:
        print_warning("--audio_gain on top of --normalize is usually unnecessary; "
                      "the gain is applied after normalisation and then limited.")

    do_normalize = args.normalize or args.audio_gain is not None
    mix_music = args.separate_music and args.music_level_db is not None

    total_steps = 2  # extract + denoise
    if args.separate_music:
        total_steps += 1
    if do_normalize:
        total_steps += 1
    if mix_music:
        total_steps += 1
    if args.reduce_hiss:
        total_steps += 1
    if args.declick:
        total_steps += 1
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
        dehissed_wav = os.path.join(temp_dir, "03b_dehissed.wav")
        normalized_wav = os.path.join(temp_dir, "04_normalized.wav")
        mixed_wav = os.path.join(temp_dir, "05_mixed.wav")
        declicked_wav = os.path.join(temp_dir, "06_declicked.wav")

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

        # -- Steady hiss / tape noise ---------------------------------------
        # Placed before normalisation so the filter sees the recording's own
        # noise floor rather than one boosted by tens of dB.
        if args.reduce_hiss:
            steps.start("Reducing steady background hiss")
            from . import hiss

            if args.hiss_method == "profile":
                hiss.reduce_hiss_profile(
                    input_wav=processed_wav,
                    output_wav=dehissed_wav,
                    temp_dir=temp_dir,
                    noise_reduction=args.hiss_nr,
                    gain_smooth=args.hiss_gain_smooth,
                    noise_floor=args.hiss_nf,
                    noise_at=noise_at,
                    noise_len=args.noise_profile_len,
                    max_excess_db=args.noise_profile_max_excess_db,
                    duration=work_duration,
                    sample_rate=SAMPLE_RATE,
                )
                print_success(f"Hiss reduction applied "
                              f"(learned profile, nr={args.hiss_nr:g}).")
            else:
                hiss.reduce_hiss_patch(
                    input_wav=processed_wav,
                    output_wav=dehissed_wav,
                    strength=args.hiss_strength,
                    window=args.hiss_window,
                    duration=work_duration,
                )
                print_success(f"Hiss reduction applied "
                              f"(patch, strength {args.hiss_strength:g}).")
            processed_wav = dehissed_wav

        # -- Loudness normalisation (after denoising) -----------------------
        if do_normalize:
            steps.start("Normalising loudness of the cleaned audio")
            filters = []
            if args.normalize:
                measured = ffmpeg_utils.measure_loudness(
                    processed_wav, args.target_lufs, args.target_tp, args.target_lra, work_duration)
                if measured is None:
                    print_warning("Loudness measurement unusable (near-silent input?); "
                                  "falling back to single-pass loudnorm.")
                else:
                    try:
                        if float(measured["input_i"]) < args.silence_warn_lufs:
                            print_warning(
                                f"Cleaned audio measures {float(measured['input_i']):.1f} LUFS, "
                                f"which is close to silence. Normalising will apply a very "
                                f"large boost — check --atten_lim_db is not stripping the "
                                f"speech."
                            )
                    except (KeyError, TypeError, ValueError):
                        pass
                filters.append(ffmpeg_utils.loudnorm_filter(
                    args.target_lufs, args.target_tp, args.target_lra, measured))
                # loudnorm works at 192 kHz internally; come back down before any
                # sample-counted filter (the latency compensation counts samples).
                filters.append(f"aresample={SAMPLE_RATE}")
            if args.audio_gain:
                filters.append(f"volume={args.audio_gain}")
                filters.append(ffmpeg_utils.compensated(limiter(args.limiter_ceiling), SAMPLE_RATE))

            norm_cmd = ["ffmpeg", "-y", "-i", processed_wav,
                        "-filter:a", ",".join(filters),
                        "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE),
                        normalized_wav]
            ffmpeg_utils.run_ffmpeg(norm_cmd, work_duration, "Normalizing")
            processed_wav = normalized_wav
            if args.normalize:
                # loudnorm silently switches between linear and dynamic modes
                # depending on available headroom, so the only trustworthy figure
                # is one measured off the result.
                achieved = ffmpeg_utils.measure_loudness(
                    processed_wav, args.target_lufs, args.target_tp, args.target_lra,
                    work_duration, label="Verifying loudness")
                actual = None
                if achieved:
                    try:
                        actual = float(achieved["input_i"])
                    except (KeyError, TypeError, ValueError):
                        actual = None
                if actual is None:
                    print_success("Loudness normalised.")
                else:
                    print_success(f"Loudness normalised: {actual:.1f} LUFS "
                                  f"(target {args.target_lufs:g}).")
                    if abs(actual - args.target_lufs) > args.loudness_tolerance_db:
                        print_warning(
                            f"That is {abs(actual - args.target_lufs):.1f} dB off the target. "
                            f"loudnorm caps its gain to keep the true peak under "
                            f"{args.target_tp:g} dBTP, so quiet, peaky source material lands short. "
                            f"Use --audio_gain for a limited extra boost if you need it louder."
                        )
            else:
                print_success("Gain applied and limited.")

        # -- Mix the music bed back under the speech ------------------------
        if mix_music:
            steps.start("Mixing music bed back under the cleaned speech")
            level = args.music_level_db
            filter_complex = (
                f"[1:a]volume={level:g}dB[music];"
                f"[0:a][music]amix=inputs=2:duration=first:normalize=0[mixed];"
                f"[mixed]{ffmpeg_utils.compensated(limiter(args.limiter_ceiling), SAMPLE_RATE)}[out]"
            )
            mix_cmd = ["ffmpeg", "-y", "-i", processed_wav, "-i", music_wav,
                       "-filter_complex", filter_complex, "-map", "[out]",
                       "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE), mixed_wav]
            ffmpeg_utils.run_ffmpeg(mix_cmd, work_duration, "Mixing")
            processed_wav = mixed_wav
            print_success(f"Music mixed back at {level:g} dB.")
        elif args.separate_music:
            print_info("Music dropped entirely (--music_level_db off).")

        # -- Mechanical tick/click reduction --------------------------------
        # Runs last, on the normalised signal: afftdn's noise floor (nf) is an
        # absolute level, so it must see the levels it was tuned against.
        if args.declick:
            steps.start("Reducing mechanical tick/click noise")
            declick_cmd = ["ffmpeg", "-y", "-i", processed_wav,
                           "-filter:a",
                           ffmpeg_utils.compensated(
                               f"afftdn=nr={args.declick_nr:g}:nf={args.declick_nf:g}",
                               SAMPLE_RATE),
                           "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE),
                           declicked_wav]
            ffmpeg_utils.run_ffmpeg(declick_cmd, work_duration, "Declicking")
            processed_wav = declicked_wav
            print_success(f"Click reduction applied (nr={args.declick_nr:g} dB).")

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
    try:
        _run(args)
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
