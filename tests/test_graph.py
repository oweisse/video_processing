"""graph.build() is pure; pin the exact graphs for representative option sets."""
import pytest

from video_audio_cleaner import graph
from video_audio_cleaner.postprocess import parse_ebur128_summary

LAT = {"hiss": 1200, "limiter": 239, "declick": 1200}
TRIM = ",asetpts=PTS-STARTPTS"


def g(mode="render", **kw):
    kw.setdefault("latency", LAT)
    return graph.build(graph.GraphOptions(**kw), mode)


def test_nothing_enabled_needs_no_render():
    assert not graph.needs_render(graph.GraphOptions())
    assert graph.stage_names(graph.GraphOptions()) == []


def test_normalize_only():
    assert g(normalize=True) == (
        "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[level];"
        "[level]ebur128=peak=true,aresample=48000[out]")


def test_normalize_uses_measured_values_linearly():
    m = dict(input_i="-31.9", input_tp="-15.5", input_lra="6.1",
             input_thresh="-42.3", target_offset="0.02")
    assert ("loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-31.9:measured_TP=-15.5:"
            "measured_LRA=6.1:measured_thresh=-42.3:offset=0.02:linear=true") in g(
                normalize=True, measured=m)


def test_patch_hiss_is_latency_aligned():
    assert g(hiss_method="patch", latency={"hiss": 216}).startswith(
        "[0:a]apad=pad_len=216,anlmdn@hiss=s=0.0003:p=0.002:r=0.0025,"
        "atrim=start_sample=216" + TRIM + "[hiss];")


def test_profile_hiss_prepends_sample_and_trims_it_with_the_latency():
    out = g(hiss_method="profile", noise_at=3.6)
    assert out.startswith(
        "[1:a]atrim=end_sample=48000" + TRIM + "[noise];"
        "[noise][0:a]concat=n=2:v=0:a=1[joined];"
        "[joined]apad=pad_len=1200,"
        "asendcmd=c='0.000 afftdn@hiss sn start; 0.900 afftdn@hiss sn stop',"
        "afftdn@hiss=nr=12:nf=-40:gs=0,atrim=start_sample=49200" + TRIM + "[hiss];")
    assert graph.inputs(graph.GraphOptions(hiss_method="profile", noise_at=3.6),
                        "d.wav") == ["-i", "d.wav", "-ss", "3.600000", "-i", "d.wav"]


def test_music_and_declick_chain():
    out = g(music_level_db=-12.0, declick=True)
    assert "[1:a]volume=-12dB[bed]" in out
    assert ("[0:a][bed]amix=inputs=2:duration=first:normalize=0,apad=pad_len=239,"
            "alimiter=limit=0.97:level=false,atrim=start_sample=239" + TRIM + "[mixed]") in out
    assert ("[mixed]apad=pad_len=1200,afftdn@click=nr=12:nf=-28,"
            "atrim=start_sample=1200" + TRIM + "[declicked]") in out
    assert out.endswith("[declicked]ebur128=peak=true,aresample=48000[out]")


def test_music_input_index_follows_profile_noise_input():
    o = graph.GraphOptions(hiss_method="profile", noise_at=1.0, music_level_db=-6.0,
                           latency=LAT)
    assert "[2:a]volume=-6dB[bed]" in graph.build(o, "render")
    assert graph.inputs(o, "d.wav", "m.wav")[-2:] == ["-i", "m.wav"]


def test_analysis_measures_after_hiss_only():
    out = g("analysis", hiss_method="patch", normalize=True, audio_gain="2dB",
            declick=True, latency={"hiss": 216})
    assert out.endswith("[hiss]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json[out]")
    assert "volume" not in out and "click" not in out


def test_analysis_takes_no_music_input():
    # Regression: analysis with a music bed asked for the music stem and failed.
    o = graph.GraphOptions(hiss_method="profile", noise_at=1.0, normalize=True,
                           music_level_db=-12.0, latency=LAT)
    assert graph.inputs(o, "d.wav", mode="analysis") == [
        "-i", "d.wav", "-ss", "1.000000", "-i", "d.wav"]
    assert "[2:a]" not in graph.build(o, "analysis")


def test_stage_names_order():
    o = graph.GraphOptions(hiss_method="patch", normalize=True, audio_gain="1dB",
                           music_level_db=-12.0, declick=True)
    assert graph.stage_names(o) == ["hiss", "loudnorm", "gain", "mix", "declick"]


def test_runtime_commands_shift_by_the_prepended_sample():
    out = g(hiss_method="profile", noise_at=0.0, noise_len=1.0,
            hiss_commands=((10.0, "nr 6"), (14.0, "nr 12")))
    assert "11.000 afftdn@hiss nr 6; 15.000 afftdn@hiss nr 12" in out


EBUR = """
[Parsed_ebur128_3 @ 0x1] t: 9.9  TARGET:-23 LUFS    M: -20.1 S: -21.0     I: -70.0 LUFS
[Parsed_ebur128_3 @ 0x1] Summary:

  Integrated loudness:
    I:         -16.1 LUFS
    Threshold: -26.4 LUFS

  True peak:
    Peak:       -1.4 dBFS
"""


def test_parses_ebur128_summary_not_running_lines():
    assert parse_ebur128_summary(EBUR) == (-16.1, -1.4)


def test_ebur128_missing_summary():
    assert parse_ebur128_summary("nothing here") == (None, None)


@pytest.mark.parametrize("text", [EBUR.replace("-16.1 LUFS", "-inf LUFS")])
def test_ebur128_silence(text):
    assert parse_ebur128_summary(text)[0] is None
