"""Every threshold in fable_review_plan.md Appendix C is a real flag with its default.

Rule 8 of the plan: anything that changes the audio or triggers a warning is
configurable. This test keeps the documented table and the parser in sync.
"""
import pytest

from video_audio_cleaner.cli import build_parser

# flag -> documented default
APPENDIX_C = {
    "atten_lim_db": 20.0,
    "chunk_len_sec": 10.0,
    "crossfade_sec": 0.5,
    "target_lufs": -16.0,
    "target_tp": -1.5,
    "target_lra": 11.0,
    "target_lufs_max": -16.0,
    "target_lufs_min": -24.0,
    "limiter_ceiling": 0.97,
    "silence_warn_lufs": -45.0,
    "loudness_tolerance_db": 2.0,
    "hiss_nr": 12.0,
    "hiss_nf": -40.0,
    "hiss_gain_smooth": 0.0,
    "noise_profile_at": None,
    "noise_profile_len": 1.0,
    "noise_profile_max_excess_db": 6.0,
    "hiss_strength": 0.0003,
    "hiss_window": 0.0025,
    "declick_nr": 12.0,
    "declick_nf": -28.0,
    "music_level_db": -12.0,
    "separation_auto_segment_sec": 600.0,
    "separation_segment": None,
    "video_crf": 18,
    "audio_bitrate": "192k",
}


@pytest.fixture(scope="module")
def defaults():
    return vars(build_parser().parse_args(["in.mp4", "--output_dir", "out"]))


@pytest.mark.parametrize("flag, default", sorted(APPENDIX_C.items()))
def test_threshold_flag_exists_with_documented_default(defaults, flag, default):
    assert flag in defaults, f"--{flag} missing from the parser"
    assert defaults[flag] == default


def test_compare_default_preview_length():
    args = build_parser().parse_args(["in.mp4", "--output_dir", "o", "--compare"])
    assert args.compare == 20.0
