import pytest

from video_audio_cleaner.cli import build_parser
from video_audio_cleaner.postprocess import auto_loudness_target
from video_audio_cleaner.tune import Candidate


def measured(i, tp, lra=6.0):
    return {"input_i": str(i), "input_tp": str(tp), "input_lra": str(lra)}


def test_loud_enough_material_keeps_the_cap():
    pick = auto_loudness_target(measured(-20, -12), -16, -24, -1.5, 11)
    # +4 dB to the cap leaves the peak at -8 dBTP: well inside the ceiling.
    assert (pick.target_lufs, pick.linear) == (-16, True)


def test_peaky_material_stops_where_the_peak_meets_the_ceiling():
    pick = auto_loudness_target(measured(-40, -20), -16, -24, -1.5, 11)
    # gain available: -1.5 - -20 = 18.5 dB, minus 0.5 margin -> -22.0 LUFS
    assert pick.target_lufs == -22.0 and pick.linear
    assert pick.reachable_lufs == pytest.approx(-22.0)


def test_rounds_down_to_half_db_steps():
    assert auto_loudness_target(measured(-40, -20.3), -16, -24, -1.5, 11).target_lufs == -22.0


def test_floor_applies_when_peaks_allow_too_little():
    pick = auto_loudness_target(measured(-48.6, -26.5), -16, -24, -1.5, 11)
    assert (pick.target_lufs, pick.linear) == (-24, False)


def test_range_target_raised_to_the_inputs_own_range():
    assert auto_loudness_target(measured(-30, -15, lra=14.37), -16, -24, -1.5, 11).target_lra \
        == pytest.approx(14.5)
    assert auto_loudness_target(measured(-30, -15, lra=6), -16, -24, -1.5, 11).target_lra == 11


def test_parser_accepts_auto_and_numbers():
    p = build_parser()
    assert p.parse_args(["i", "--output_dir", "o", "--target_lufs", "auto"]).target_lufs == "auto"
    assert p.parse_args(["i", "--output_dir", "o", "--target_lufs", "-20"]).target_lufs == -20.0
    with pytest.raises(SystemExit):
        p.parse_args(["i", "--output_dir", "o", "--target_lufs", "loud"])


def test_plan_flags_carry_auto():
    flags = Candidate(10.0, "off", False).flags(normalize=True, target_lufs="auto")
    assert flags[:3] == ["--normalize", "--target_lufs", "auto"]
