import pytest

from video_audio_cleaner import ffmpeg_utils

LOUDNORM_STDERR = """
[Parsed_loudnorm_0 @ 0x1] 
{
	"input_i" : "-31.92",
	"input_tp" : "-15.47",
	"input_lra" : "6.10",
	"input_thresh" : "-42.30",
	"output_i" : "-16.02",
	"output_tp" : "-1.50",
	"output_lra" : "5.20",
	"output_thresh" : "-26.40",
	"normalization_type" : "dynamic",
	"target_offset" : "0.02"
}
"""


def test_parses_loudnorm_json():
    stats = ffmpeg_utils._parse_loudnorm_json(LOUDNORM_STDERR)
    assert stats["input_i"] == "-31.92"


@pytest.mark.xfail(strict=True, reason="takes the last {...}; anchored in Phase 2")
def test_ignores_decoy_braces_after_loudnorm():
    stats = ffmpeg_utils._parse_loudnorm_json(LOUDNORM_STDERR + "\nmetadata {x=1}\n")
    assert stats is not None and stats["input_i"] == "-31.92"


def test_probe_duration(fx):
    assert ffmpeg_utils.probe_duration(str(fx.video)) == pytest.approx(60.0, abs=0.1)


def test_has_video_stream(fx):
    assert ffmpeg_utils.has_video_stream(str(fx.video))
    assert not ffmpeg_utils.has_video_stream(str(fx.mix_noisy))


@pytest.mark.parametrize("requested, expected", [(9.0, 8.0), (8.0, 8.0), (0.0, 0.0), (17.5, 16.0)])
def test_keyframe_probe(fx, requested, expected):
    got = ffmpeg_utils.probe_keyframe_at_or_before(str(fx.video), requested)
    assert got == pytest.approx(expected, abs=0.001)
