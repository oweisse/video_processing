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

4. Process a wedding video starting at 8m50s for 3m30s, merging the denoised audio back into the video with normalization and a 12dB boost:

   python3 extract_and_filter_audio.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --skip_first_n_sec 8m50s --process_n_seconds 3m30s --normalize --audio_gain 12dB


Video Enhancement Tool (improve_video.py)
========================================

This script uses Real-ESRGAN (via Vulkan/Metal on macOS) and generative machine learning models to upscale and enhance the visual quality of old or low-resolution video recordings (such as VHS tape rips).

Installation & Setup
--------------------
1. To configure the portable macOS Real-ESRGAN upscaler binary and models:

     chmod +x setup_video_tool.sh
     ./setup_video_tool.sh

2. To initialize the Python AI/ML virtual environment and install dependencies (PyTorch, Diffusers, GFP-GAN):

     chmod +x setup_ai.sh
     ./setup_ai.sh

Usage
-----
python3 improve_video.py [input_video] [options]

Note: If using generative flags (--face_restoration or --generative_upscale), make sure to activate the AI environment first:
  source venv_ai/bin/activate

Options:
  -o, --output                 Path for the output file.
  --skip_first_n_sec           Skip the specified duration at the start of the video (e.g. '1m30s', '10m').
  --process_n_seconds          Only process the specified duration (e.g. '1m30s', '2m').
  -s, --scale                  Scaling factor (2, 3, or 4; default: 4).
  -m, --model                  Model name (default: realesrgan-x4plus).
  --deinterlace                De-interlace video frames using FFmpeg's yadif filter (critical for restoring VHS).
  --denoise                    Apply advanced temporal and spatial denoising (atadenoise + hqdn3d) before upscaling.
  --face_restoration           Apply Option 1: GFP-GAN face restoration on top of Real-ESRGAN upscaled frames to fix waxy faces.
  --generative_upscale         Apply Option 2: Stable Diffusion + ControlNet Tile generative upscaling.
  --only_ai_process_n_frames N For testing: Only process the first N frames, write output PNGs locally, and exit early.

Examples
--------
1. Process a slice of the wedding video from minute 8:30 to 12:30 (4 minutes duration):

   python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --skip_first_n_sec 8m30s --process_n_seconds 4m

2. Upscale a 10-second slice at 2x scale, with de-interlacing and denoising enabled:

   python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --skip_first_n_sec 9m30s --process_n_seconds 10s --scale 2 --deinterlace --denoise

3. Test face restoration (Option 1) on the first 3 frames of a wedding video scene:

   source venv_ai/bin/activate
   python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --skip_first_n_sec 9m30s --deinterlace --denoise --face_restoration --only_ai_process_n_frames 3

4. Test generative upscaling (Option 2) on a single frame:

   source venv_ai/bin/activate
   python3 improve_video.py "/Users/ofir/Documents/video_processing_temp/02-חתונה.mp4" --skip_first_n_sec 9m30s --deinterlace --denoise --generative_upscale --only_ai_process_n_frames 1



