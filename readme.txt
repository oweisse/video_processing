Video Audio Processing & Denoising Script
========================================

This script extracts the audio track from a video file, filters out background noise using DeepFilterNet, and optionally merges the clean audio back into the video.

Installation & Setup
--------------------
You can automatically install all required dependencies (FFmpeg, pipx, DeepFilterNet, and soundfile) by running the setup script in your terminal:

  chmod +x setup.sh
  ./setup.sh

Requirements (installed automatically by setup.sh)
--------------------------------------------------
1. Python 3
2. FFmpeg
3. DeepFilterNet (configured inside a pipx virtual environment)

Usage
-----
python3 extract_and_filter_audio.py [input_video] [options]

Arguments:
  input_video            Path to the input video or audio file.
                         (Default: /Users/ofir/Documents/video_processing_temp/04-.mp4)

Options:
  -o, --output           Path for the output file. Defaults to the same directory
                         as the input with a suffix.
  --filter_audio_only    Skip merging the audio back into the video, outputting
                         a filtered WAV file instead.
  --skip_first_n_sec     Skip the specified duration at the start of the video.
                         Supports seconds (e.g., 90) or time format (e.g., 1m30s, 10m).
  --process_n_seconds    Only extract and process the specified duration.
                         Supports seconds (e.g., 45) or time format (e.g., 1m, 45s).
  --normalize            Applies FFmpeg's loudnorm filter to amplify and normalize
                         the audio levels before filtering (useful for low voices).
  --audio_gain           Explicit volume boost parameter (e.g., 2.0 or 10dB).

Examples
--------

1. Process a specific slice (from minute 10 to minute 11) of a wedding video,
   boost/normalize the volume, and output a WAV file:

   python3 extract_and_filter_audio.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --filter_audio_only --skip_first_n_sec 10m --process_n_seconds 1m --normalize

2. Filter the default video file, keep only the audio track, and skip the first 30 seconds:

   python3 extract_and_filter_audio.py --filter_audio_only --skip_first_n_sec 30s

3. Process the first 10 seconds of a video, normalize and add a 6dB boost, then merge back to MP4:

   python3 extract_and_filter_audio.py /path/to/video.mp4 --process_n_seconds 10 --normalize --audio_gain 6dB
