#!/usr/bin/env python3
"""Backward-compatible entry point.

The implementation now lives in the video_audio_cleaner package; this shim keeps
`python3 extract_and_filter_audio.py ...` working. The installed console script
is `clean-audio`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from video_audio_cleaner.cli import main
except ImportError as exc:
    sys.stderr.write(
        f"✘ Could not load the video_audio_cleaner package: {exc}\n\n"
        "This usually means the dependencies are not installed in the Python you "
        "are using.\nRun ./setup.sh once, then use either:\n"
        "    source venv/bin/activate && python3 extract_and_filter_audio.py ...\n"
        "or the installed command directly:\n"
        "    ./venv/bin/clean-audio ...\n"
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
