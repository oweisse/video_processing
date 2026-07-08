#!/usr/bin/env python3
import argparse
import os
import shutil
import re
import subprocess
import sys
import tempfile

# ANSI escape codes for styling (only used if stdout is a TTY)
COLOR_BLUE = "\033[94m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

def print_step(step_num, total_steps, title):
    if sys.stdout.isatty():
        print(f"\n{COLOR_BOLD}{COLOR_BLUE}[{step_num}/{total_steps}] {title}{COLOR_RESET}")
    else:
        print(f"\n[{step_num}/{total_steps}] {title}")

def print_success(message):
    if sys.stdout.isatty():
        print(f"{COLOR_BOLD}{COLOR_GREEN}✔ {message}{COLOR_RESET}")
    else:
        print(f"✔ {message}")

def print_warning(message):
    if sys.stdout.isatty():
        print(f"{COLOR_BOLD}{COLOR_YELLOW}⚠ {message}{COLOR_RESET}")
    else:
        print(f"⚠ {message}")

def print_error(message):
    if sys.stdout.isatty():
        print(f"{COLOR_BOLD}{COLOR_RED}✘ {message}{COLOR_RESET}", file=sys.stderr)
    else:
        print(f"✘ {message}", file=sys.stderr)

def get_video_duration(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        return None

def run_ffmpeg_with_progress(cmd_args, duration, prefix):
    # Append progress flags to make ffmpeg output periodic key-value updates
    cmd = cmd_args + ["-progress", "-", "-nostats"]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    speed = "N/A"
    bar_len = 30
    
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
                        if sys.stdout.isatty():
                            sys.stdout.write(f"\r{prefix}: [{bar}] {pct:.1f}% (Speed: {speed})")
                            sys.stdout.flush()
                except Exception:
                    pass
            elif line.startswith("speed="):
                speed = line.split("=")[1].strip()
        
        process.wait()
        if process.returncode != 0:
            stderr_out = process.stderr.read()
            raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr_out)
        
        # Draw final 100% state
        if duration:
            bar = "█" * bar_len
            sys.stdout.write(f"\r{prefix}: [{bar}] 100.0% (Speed: {speed})\n")
        else:
            sys.stdout.write(f"\r{prefix}: Done!\n")
        sys.stdout.flush()
        
    except Exception as e:
        process.kill()
        raise e

def run_deepfilter_live(input_wav, output_dir):
    cmd = ["deepFilter", input_wav, "-o", output_dir, "--no-suffix"]
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Run the process and capture combined output
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    # Read output character-by-character to preserve formatting and progress updates in real-time
    try:
        while True:
            char = process.stdout.read(1)
            if not char and process.poll() is not None:
                break
            sys.stdout.write(char)
            sys.stdout.flush()
            
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
            
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
    parser = argparse.ArgumentParser(description="Extract audio, run DeepFilterNet, and optionally merge it back into a video.")
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
        "--filter_audio_only",
        action="store_true",
        help="Skip merging the audio track back into the video, outputting the filtered WAV instead."
    )
    parser.add_argument(
        "--process_n_seconds",
        type=parse_time_string,
        default=None,
        help="Only extract and process the specified duration (e.g. '1m30s', '2m', '45s', or '90' seconds)."
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Apply ffmpeg's loudnorm filter to normalize/amplify audio before filtering."
    )
    parser.add_argument(
        "--skip_first_n_sec",
        type=parse_time_string,
        default=None,
        help="Skip the specified duration at the beginning of the video file (e.g. '1m30s', '2m', '45s', or '90' seconds)."
    )
    parser.add_argument(
        "--audio_gain",
        type=str,
        default=None,
        help="Volume amplification parameter for ffmpeg (e.g. '2.0' to double volume, or '10dB' for +10dB boost)."
    )
    
    args = parser.parse_args()
    
    input_path = os.path.abspath(args.input_video)
    if not os.path.exists(input_path):
        print_error(f"Input file not found at: {input_path}")
        sys.exit(1)
        
    # Get video duration first so we can use it in the output filename if needed
    duration = get_video_duration(input_path)
    
    # Resolve the default output path if not specified
    input_dir, input_filename = os.path.split(input_path)
    base_name, _ = os.path.splitext(input_filename)
    
    time_suffix = ""
    if args.skip_first_n_sec is not None or args.process_n_seconds is not None:
        start_sec = args.skip_first_n_sec if args.skip_first_n_sec is not None else 0.0
        if args.process_n_seconds is not None:
            end_sec = start_sec + args.process_n_seconds
        elif duration is not None:
            end_sec = duration
        else:
            end_sec = None
            
        start_str = format_time_filename(start_sec)
        end_str = format_time_filename(end_sec)
        time_suffix = f"_{start_str}-{end_str}"
    
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        if args.filter_audio_only:
            output_path = os.path.join(input_dir, f"{base_name}_enhanced{time_suffix}.wav")
        else:
            output_path = os.path.join(input_dir, f"{base_name}_clean{time_suffix}.mp4")
            
    print(f"Input Video:  {input_path}")
    print(f"Output Path:  {output_path}")
    
    if args.skip_first_n_sec is not None:
        if duration is not None:
            duration = max(0.0, duration - args.skip_first_n_sec)
            print(f"Skipping the first {args.skip_first_n_sec} seconds of the video.")
            
    if args.process_n_seconds is not None:
        if duration is None or args.process_n_seconds < duration:
            duration = args.process_n_seconds
            print(f"Limiting processing to the first {args.process_n_seconds} seconds.")

    if duration:
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        print(f"Video Length: {minutes:02d}:{seconds:02d} ({duration:.2f}s)")
    else:
        print_warning("Could not determine video duration. Progress bar might be limited.")
        
    total_steps = 2 if args.filter_audio_only else 3
    
    # Use temporary directory for intermediate files so we don't clutter the user's workspace
    with tempfile.TemporaryDirectory() as temp_dir:
        noisy_audio_path = os.path.join(temp_dir, "noisy_audio.wav")
        cleaned_output_dir = os.path.join(temp_dir, "cleaned_output")
        enhanced_audio_path = os.path.join(cleaned_output_dir, "noisy_audio.wav")
        
        # ----------------------------------------------------
        # Step 1: Extract Audio
        # ----------------------------------------------------
        print_step(1, total_steps, "Extracting audio track from video")
        extract_cmd = ["ffmpeg", "-y"]
        if args.skip_first_n_sec is not None:
            extract_cmd.extend(["-ss", str(args.skip_first_n_sec)])
        extract_cmd.extend([
            "-i", input_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "48000"
        ])
        audio_filters = []
        if args.normalize:
            audio_filters.append("loudnorm")
        if args.audio_gain is not None:
            audio_filters.append(f"volume={args.audio_gain}")
            
        if audio_filters:
            extract_cmd.extend(["-filter:a", ",".join(audio_filters)])
        if args.process_n_seconds is not None:
            extract_cmd.extend(["-t", str(args.process_n_seconds)])
        # Append target output file to cmd
        extract_cmd.append(noisy_audio_path)
        
        try:
            run_ffmpeg_with_progress(extract_cmd, duration, "Extracting")
            print_success("Audio extracted successfully.")
        except Exception as e:
            print_error(f"Error during audio extraction: {e}")
            sys.exit(1)
            
        # ----------------------------------------------------
        # Step 2: Run DeepFilterNet
        # ----------------------------------------------------
        print_step(2, total_steps, "Filtering white noise using DeepFilterNet")
        try:
            run_deepfilter_live(noisy_audio_path, cleaned_output_dir)
            print_success("Audio filtered successfully.")
        except Exception as e:
            print_error(f"Error during audio filtering: {e}")
            sys.exit(1)
            
        # ----------------------------------------------------
        # Step 3: Optional Merge or Output Copy
        # ----------------------------------------------------
        if args.filter_audio_only:
            print(f"\nSkipping merge step (--filter_audio_only active). Saving WAV...")
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                shutil.copy(enhanced_audio_path, output_path)
                print_success(f"Cleaned audio track saved to: {output_path}")
            except Exception as e:
                print_error(f"Error copying filtered audio file: {e}")
                sys.exit(1)
        else:
            print_step(3, total_steps, "Merging cleaned audio track back into video")
            merge_cmd = ["ffmpeg", "-y"]
            if args.skip_first_n_sec is not None:
                merge_cmd.extend(["-ss", str(args.skip_first_n_sec)])
            merge_cmd.extend([
                "-i", input_path,
                "-i", enhanced_audio_path,
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_path
            ])
            
            try:
                run_ffmpeg_with_progress(merge_cmd, duration, "Merging")
                print_success(f"Processing complete! Cleaned video saved to: {output_path}")
            except Exception as e:
                print_error(f"Error during video merging: {e}")
                sys.exit(1)

if __name__ == "__main__":
    main()
