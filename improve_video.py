#!/usr/bin/env python3
import argparse
import os
import shutil
import re
import shlex
import subprocess
import sys
import tempfile
import time

# ANSI escape codes for styling (only used if stdout is a TTY)
COLOR_BLUE = "\033[94m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

def print_step(step_num, total_steps, title):
    if sys.stdout.isatty():
        print(f"\n{COLOR_BOLD}{COLOR_BLUE}[{step_num}/{total_steps}] {title}{COLOR_RESET}", flush=True)
    else:
        print(f"\n[{step_num}/{total_steps}] {title}", flush=True)

def print_success(message):
    if sys.stdout.isatty():
        print(f"{COLOR_BOLD}{COLOR_GREEN}✔ {message}{COLOR_RESET}", flush=True)
    else:
        print(f"✔ {message}", flush=True)

def print_error(message):
    if sys.stdout.isatty():
        print(f"{COLOR_BOLD}{COLOR_RED}破坏/Error: {message}{COLOR_RESET}", file=sys.stderr, flush=True)
    else:
        print(f"✘ {message}", file=sys.stderr, flush=True)

def get_video_duration(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        print(f"Running command: {shlex.join(cmd)}", flush=True)
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        return None

def get_video_fps(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        print(f"Running command: {shlex.join(cmd)}", flush=True)
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        out = result.stdout.strip()
        if "/" in out:
            num, den = map(float, out.split("/"))
            return num / den
        return float(out)
    except Exception:
        return 25.0

def estimate_storage_usage(directory, total_frames, scale):
    if not directory or not os.path.exists(directory) or not total_frames:
        return None
    try:
        files = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".png")]
        if len(files) < 5:
            return None
        total_bytes = sum(os.path.getsize(f) for f in files)
        avg_size = total_bytes / len(files)
        est_input_bytes = avg_size * total_frames
        est_output_bytes = est_input_bytes * (scale ** 2)
        total_est_bytes = est_input_bytes + est_output_bytes
        mb = total_est_bytes / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.1f} GB"
        else:
            return f"{mb:.0f} MB"
    except Exception:
        return None

def run_ffmpeg_with_progress(cmd_args, duration, prefix, estimate_size_dir=None, total_frames_est=None, scale=4):
    # Append progress flags to make ffmpeg output periodic key-value updates
    cmd = cmd_args + ["-progress", "-", "-nostats"]
    print(f"Running command: {shlex.join(cmd)}", flush=True)
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    speed = "N/A"
    bar_len = 30
    last_log_pct = -5.0
    
    try:
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=")[1])
                    if duration and duration > 0:
                        sec = us / 1_000_000.0
                        pct = min(100.0, (sec / duration) * 100.0)
                        filled_len = int(bar_len * pct / 100)
                        bar = "█" * filled_len + "-" * (bar_len - filled_len)
                        
                        est_str = ""
                        if estimate_size_dir and total_frames_est:
                            est_storage = estimate_storage_usage(estimate_size_dir, total_frames_est, scale)
                            if est_storage:
                                est_str = f" (Est. Storage: {est_storage})"
                                
                        if sys.stdout.isatty():
                            sys.stdout.write(f"\r{prefix}: [{bar}] {pct:.1f}% ({sec:.1f}s/{duration:.1f}s){est_str} (Speed: {speed})")
                            sys.stdout.flush()
                        elif pct >= last_log_pct + 5.0 or pct >= 100.0:
                            print(f"{prefix}: {pct:.1f}% ({sec:.1f}s/{duration:.1f}s){est_str} (Speed: {speed})", flush=True)
                            last_log_pct = pct
                except Exception:
                    pass
            elif line.startswith("speed="):
                raw_speed = line.split("=")[1].strip()
                if raw_speed.endswith("x"):
                    speed = f"{raw_speed[:-1]} sec/sec"
                else:
                    speed = raw_speed
        
        process.wait()
        if process.returncode != 0:
            stderr_out = process.stderr.read()
            raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr_out)
        
        # Draw final 100% state
        if duration:
            bar = "█" * bar_len
            est_str = ""
            if estimate_size_dir and total_frames_est:
                est_storage = estimate_storage_usage(estimate_size_dir, total_frames_est, scale)
                if est_storage:
                    est_str = f" (Est. Storage: {est_storage})"
            sys.stdout.write(f"\r{prefix}: [{bar}] 100.0% ({duration:.1f}s/{duration:.1f}s){est_str} (Speed: {speed})\n")
        else:
            sys.stdout.write(f"\r{prefix}: Done!\n")
        sys.stdout.flush()
        
    except Exception as e:
        process.kill()
        raise e

def get_current_frame_progress(log_path):
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-10:]
            for line in reversed(lines):
                line = line.strip()
                if re.match(r"^\d+(?:\.\d+)?%$", line):
                    return float(line[:-1])
    except Exception:
        pass
    return None

def format_duration(seconds):
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    mins = seconds // 60
    secs = seconds % 60
    if mins > 0:
        return f"{mins}m{secs:02d}s"
    return f"{secs}s"

def format_upscaling_time_metrics(processed, total_frames, elapsed, sub_pct=None):
    elapsed_str = format_duration(elapsed)
    fraction = (sub_pct / 100.0) if sub_pct is not None else 0.0
    effective = processed + fraction
    if effective <= 0 or total_frames <= 0:
        return f"Elapsed/Tot/Remaining: {elapsed_str}/N/A/N/A"
    
    fps = effective / elapsed
    total_est = total_frames / fps
    remaining = max(0.0, total_est - elapsed)
    
    total_str = format_duration(total_est)
    remaining_str = format_duration(remaining)
    return f"Elapsed/Tot/Remaining: {elapsed_str}/{total_str}/{remaining_str}"

def format_upscaling_speed(processed, elapsed, sub_pct=None):
    if elapsed <= 0:
        return "N/A"
    fraction = (sub_pct / 100.0) if sub_pct is not None else 0.0
    effective = processed + fraction
    if effective <= 0:
        return "N/A"
    fps = effective / elapsed
    if fps < 1.0:
        sec_per_frame = elapsed / effective
        return f"{sec_per_frame:.1f} sec/frame"
    return f"{fps:.1f} fps"

def rename_completed_output_frames(output_dir, finished=False):
    try:
        files = sorted([f for f in os.listdir(output_dir) if f.startswith("input_frame_") and f.endswith(".png")])
        if not files:
            return
        to_rename = files if finished else files[:-1]
        for filename in to_rename:
            new_filename = filename.replace("input_frame_", "output_frame_", 1)
            old_path = os.path.join(output_dir, filename)
            new_path = os.path.join(output_dir, new_filename)
            try:
                os.rename(old_path, new_path)
            except Exception:
                pass
    except Exception:
        pass

def run_upscaler_with_progress(bin_path, models_dir, input_dir, output_dir, model_name, scale):
    cmd = [
        bin_path,
        "-i", input_dir,
        "-o", output_dir,
        "-n", model_name,
        "-s", str(scale),
        "-m", models_dir
    ]
    print(f"Running command: {shlex.join(cmd)}", flush=True)
    
    # Pre-count the frames to know the total work
    total_frames = len([f for f in os.listdir(input_dir) if f.endswith(".png")])
    if total_frames == 0:
        raise ValueError("No input frames extracted. Frame extraction may have failed.")
        
    upscaler_log_path = os.path.join(os.path.dirname(output_dir), "realesrgan_stdout_stderr.log")
    log_f = open(upscaler_log_path, "w", encoding="utf-8")
    process = subprocess.Popen(cmd, stdout=log_f, stderr=log_f)
    
    bar_len = 30
    start_time = time.time()
    last_log_pct = -5.0
    last_log_time = start_time
    
    try:
        while process.poll() is None:
            # Count the upscaled frames in output directory
            processed = len([f for f in os.listdir(output_dir) if f.endswith(".png")])
            pct = min(100.0, (processed / total_frames) * 100.0) if total_frames > 0 else 0.0
            filled_len = int(bar_len * pct / 100)
            bar = "█" * filled_len + "-" * (bar_len - filled_len)
            
            # Calculate actual size of directories
            try:
                input_bytes = sum(os.path.getsize(os.path.join(input_dir, f)) for f in os.listdir(input_dir) if f.endswith(".png"))
                output_bytes = sum(os.path.getsize(os.path.join(output_dir, f)) for f in os.listdir(output_dir) if f.endswith(".png"))
                current_mb = (input_bytes + output_bytes) / (1024 * 1024)
                if current_mb >= 1024:
                    storage_str = f"{current_mb / 1024:.1f} GB"
                else:
                    storage_str = f"{current_mb:.0f} MB"
            except Exception:
                storage_str = "N/A"
            
            # Parse current frame progress first to use in speed calculation
            sub_pct = get_current_frame_progress(upscaler_log_path)
            current_frame_str = ""
            if sub_pct is not None:
                sub_filled = int(10 * sub_pct / 100)
                sub_bar = "█" * sub_filled + "-" * (10 - sub_filled)
                current_frame_str = f" [Frame: {sub_bar} {sub_pct:.1f}%]"
            
            elapsed = time.time() - start_time
            speed_str = format_upscaling_speed(processed, elapsed, sub_pct)
            time_metrics = format_upscaling_time_metrics(processed, total_frames, elapsed, sub_pct)
                
            # Log throttle logic (either 5% progress or 10 seconds elapsed)
            current_time = time.time()
            should_log = False
            if pct >= last_log_pct + 5.0 or pct >= 100.0:
                should_log = True
                last_log_pct = pct
                last_log_time = current_time
            elif current_time - last_log_time >= 10.0:
                should_log = True
                last_log_time = current_time
                
            if sys.stdout.isatty():
                sys.stdout.write(f"\rUpscaling: [{bar}] {pct:.1f}% ({processed}/{total_frames} frames){current_frame_str} ({time_metrics}) (Storage: {storage_str}) (Speed: {speed_str})")
                sys.stdout.flush()
            elif should_log:
                print(f"Upscaling: {pct:.1f}% ({processed}/{total_frames} frames){current_frame_str} ({time_metrics}) (Storage: {storage_str}) (Speed: {speed_str})", flush=True)
                
            rename_completed_output_frames(output_dir, finished=False)
            time.sleep(0.5)
            
        # Final count and verification
        process.wait()
        log_f.close()
        rename_completed_output_frames(output_dir, finished=True)
        processed = len([f for f in os.listdir(output_dir) if f.endswith(".png")])
        if process.returncode != 0:
            try:
                with open(upscaler_log_path, "r", encoding="utf-8") as f:
                    stderr_out = f.read()
            except Exception:
                stderr_out = "Could not read upscaler log file."
            raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr_out)
            
        bar = "█" * bar_len
        elapsed = time.time() - start_time
        speed_str = format_upscaling_speed(processed, elapsed)
        time_metrics = format_upscaling_time_metrics(processed, total_frames, elapsed)
        
        try:
            input_bytes = sum(os.path.getsize(os.path.join(input_dir, f)) for f in os.listdir(input_dir) if f.endswith(".png"))
            output_bytes = sum(os.path.getsize(os.path.join(output_dir, f)) for f in os.listdir(output_dir) if f.endswith(".png"))
            current_mb = (input_bytes + output_bytes) / (1024 * 1024)
            if current_mb >= 1024:
                storage_str = f"{current_mb / 1024:.1f} GB"
            else:
                storage_str = f"{current_mb:.0f} MB"
        except Exception:
            storage_str = "N/A"
            
        sys.stdout.write(f"\rUpscaling: [{bar}] 100.0% ({processed}/{total_frames} frames) ({time_metrics}) (Storage: {storage_str}) (Speed: {speed_str})\n")
        sys.stdout.flush()
        
    except Exception as e:
        process.kill()
        raise e

def parse_time_string(val):
    if val is None:
        return None
    val = str(val).strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        pass
        
    val_lower = val.lower()
    minutes = 0.0
    seconds = 0.0
    
    m_match = re.search(r"(\d+(?:\.\d+)?)m", val_lower)
    s_match = re.search(r"(\d+(?:\.\d+)?)s", val_lower)
    
    if not m_match and not s_match:
        raise argparse.ArgumentTypeError(
            f"Invalid time format: '{val}'. Expected a number of seconds, or format like '1m30s', '2m', '45s'."
        )
        
    if m_match:
        minutes = float(m_match.group(1))
    if s_match:
        seconds = float(s_match.group(1))
        
    return minutes * 60.0 + seconds

def format_time_filename(seconds):
    if seconds is None:
        return "end"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}m{secs:02d}s"

def main():
    parser = argparse.ArgumentParser(
        description="Locally enhance older video recordings using Real-ESRGAN.",
        allow_abbrev=False
    )
    parser.add_argument(
        "input_video",
        type=str,
        nargs="?",
        default="/Users/ofir/Documents/video_processing_temp/04-.mp4",
        help="Path to the input video file (default: /Users/ofir/Documents/video_processing_temp/04-.mp4)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Path for the output file. Defaults to same directory as input with suffix."
    )
    parser.add_argument(
        "--skip_first_n_sec",
        type=parse_time_string,
        default=None,
        help="Skip the specified duration at the start of the video (e.g. '1m30s', '10m', '45s')."
    )
    parser.add_argument(
        "--process_n_seconds",
        type=parse_time_string,
        default=None,
        help="Only extract and process the specified duration (e.g. '1m30s', '2m', '45s')."
    )
    parser.add_argument(
        "-s", "--scale",
        type=int,
        default=4,
        choices=[2, 3, 4],
        help="Scaling factor for upscaling (default: 4)"
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
        default="realesrgan-x4plus",
        choices=["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesrnet-x4plus", "realesr-animevideov3"],
        help="AI model to use for upscaling (default: realesrgan-x4plus)"
    )
    parser.add_argument(
        "--deinterlace",
        action="store_true",
        help="Apply deinterlacing (yadif filter) to the input video frames before upscaling."
    )
    parser.add_argument(
        "--denoise",
        action="store_true",
        help="Apply high-quality spatial/temporal denoising (atadenoise + hqdn3d) before upscaling. (Recommended for noisy VHS footage)"
    )

    args = parser.parse_args()

    # 1. Validate Input File
    if not os.path.exists(args.input_video):
        print_error(f"Input file not found: {args.input_video}")
        sys.exit(1)

    # 2. Locate Real-ESRGAN Binary
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_bin = os.path.join(script_dir, "bin", "realesrgan-ncnn-vulkan", "realesrgan-ncnn-vulkan")
    
    bin_path = None
    if os.path.exists(local_bin):
        bin_path = local_bin
    else:
        bin_path = shutil.which("realesrgan-ncnn-vulkan")
        
    if not bin_path:
        print_error("realesrgan-ncnn-vulkan binary not found.")
        print_error("Please run the setup script first to set it up locally:")
        print_error("  chmod +x setup_video_tool.sh && ./setup_video_tool.sh")
        sys.exit(1)

    # 3. Analyze Video Duration and FPS
    original_duration = get_video_duration(args.input_video)
    if original_duration is None:
        print_error("Could not determine video duration using ffprobe.")
        sys.exit(1)
        
    fps = get_video_fps(args.input_video)
    
    # 4. Handle Slicing Durations
    start_sec = args.skip_first_n_sec if args.skip_first_n_sec is not None else 0.0
    if start_sec < 0 or start_sec >= original_duration:
        print_error(f"Invalid skip time ({start_sec}s). Original video length is {original_duration:.2f}s.")
        sys.exit(1)
        
    duration = args.process_n_seconds
    if duration is None:
        duration = original_duration - start_sec
    else:
        duration = min(duration, original_duration - start_sec)
        
    if duration <= 0:
        print_error("Processing duration must be greater than 0.")
        sys.exit(1)

    # 5. Resolve Output Path
    if args.output is None:
        base, ext = os.path.splitext(args.input_video)
        start_fmt = format_time_filename(start_sec)
        end_fmt = format_time_filename(start_sec + duration)
        args.output = f"{base}_enhanced_{start_fmt}-{end_fmt}{ext}"

    # Determine native upscale factor to avoid NCNN tile-misalignment/seams bug
    if args.model == "realesr-animevideov3":
        run_scale = args.scale
    else:
        run_scale = 4

    print(f"Input Video:  {args.input_video}", flush=True)
    print(f"Output Path:  {args.output}", flush=True)
    if args.skip_first_n_sec:
        print(f"Skipping the first {start_sec:.1f} seconds of the video.", flush=True)
    print(f"Limiting processing to {duration:.1f} seconds.", flush=True)
    print(f"Model:        {args.model} (Requested Scale: {args.scale}x, Native Upscaling Scale: {run_scale}x)", flush=True)
    print(f"Video FPS:    {fps:.2f}", flush=True)
    print(f"Deinterlace:  {'Enabled (yadif)' if args.deinterlace else 'Disabled'}", flush=True)
    print(f"Denoise:      {'Enabled (atadenoise + hqdn3d)' if args.denoise else 'Disabled'}", flush=True)

    # 6. Execute Upscaling Pipeline using Temporary Directory
    total_steps = 4
    with tempfile.TemporaryDirectory() as tmp_dir:
        input_frames_dir = os.path.join(tmp_dir, "input_frames")
        output_frames_dir = os.path.join(tmp_dir, "output_frames")
        os.makedirs(input_frames_dir, exist_ok=True)
        os.makedirs(output_frames_dir, exist_ok=True)
        
        # Step 1: Extract Frames
        print_step(1, total_steps, "Extracting video frames")
        extract_args = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", args.input_video,
            "-t", str(duration),
        ]
        filters = []
        if args.deinterlace:
            filters.append("yadif")
        if args.denoise:
            filters.append("atadenoise=0a=0.05:0b=0.05:1a=0.05:1b=0.05")
            filters.append("hqdn3d=3:2:5:3")
            
        if filters:
            extract_args += ["-vf", ",".join(filters)]

        extract_args += [
            "-q:v", "2",
            os.path.join(input_frames_dir, "input_frame_%08d.png")
        ]
        total_frames_est = int(duration * fps)
        run_ffmpeg_with_progress(
            extract_args,
            duration,
            "Extracting",
            estimate_size_dir=input_frames_dir,
            total_frames_est=total_frames_est,
            scale=run_scale
        )
        
        # Step 2: Upscale Frames (always running at the model's native scale)
        print_step(2, total_steps, "Upscaling video frames using Real-ESRGAN")
        models_dir = os.path.join(os.path.dirname(bin_path), "models")
        run_upscaler_with_progress(bin_path, models_dir, input_frames_dir, output_frames_dir, args.model, run_scale)
        
        # Step 3: Extract Audio Track Slice
        print_step(3, total_steps, "Extracting audio track slice")
        audio_slice_file = os.path.join(tmp_dir, "audio_slice.wav")
        audio_args = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", args.input_video,
            "-vn",
            "-acodec", "copy",
            "-t", str(duration),
            audio_slice_file
        ]
        # Copying audio is extremely fast, but we run it via the progress wrapper just in case
        run_ffmpeg_with_progress(audio_args, duration, "Extracting Audio")
        
        # Step 4: Reassemble frames into output video and merge audio
        print_step(4, total_steps, "Reassembling video frames and merging audio")
        merge_args = [
            "ffmpeg", "-y",
            "-f", "image2",
            "-framerate", str(fps),
            "-i", os.path.join(output_frames_dir, "output_frame_%08d.png"),
            "-i", audio_slice_file,
        ]
        
        # If requested scale is different from upscaling scale (e.g. upscaled at 4x but requested 2x),
        # downscale using FFmpeg's high-quality lanczos filter during merge to avoid grid seams.
        if args.scale != run_scale:
            merge_args += ["-vf", f"scale=iw*{args.scale}/{run_scale}:ih*{args.scale}/{run_scale}:flags=lanczos"]
            
        merge_args += [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-shortest",
            args.output
        ]
        run_ffmpeg_with_progress(merge_args, duration, "Merging")
        
    print_success(f"Processing complete! Enhanced video saved to: {args.output}")

if __name__ == "__main__":
    main()
