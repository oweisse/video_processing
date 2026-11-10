"""clean-audio-export: an edit (project.json) into one MP4 (docs/gui-design.md §9)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from video_audio_cleaner import export as E
from video_audio_cleaner import probe

BIN = Path(sys.executable).parent


@pytest.fixture(scope="module")
def fx_probe(fx):
    return probe.probe(str(fx.video))


def project(fx, fx_probe, clips, music=(), canvas=None, **extra):
    return {"schema": 1, "name": "t", "canvas": canvas,
            "sources": {"fx": {"path": str(fx.video), "probe": fx_probe}},
            "tracks": {"video": [{"id": f"c{i}", "source": "fx", **c} for i, c in enumerate(clips)],
                       "music": list(music), **extra.pop("tracks", {})},
            "export": extra.pop("export", {}), **extra}


def write(tmp_path, proj):
    folder = tmp_path / "_projects" / proj["name"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "project.json"
    path.write_text(json.dumps(proj))
    return path


def ffprobe(path, entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def lufs(path):
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null",
                          "-"], capture_output=True, text=True).stderr
    from video_audio_cleaner.postprocess import parse_ebur128_summary
    return parse_ebur128_summary(err)[0]


# ------------------------------------------------------------------ the model
def test_timeline_positions_overlap_at_crossfades(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 4},
                               {"in_sec": 10, "out_sec": 13,
                                "transition_in": {"type": "crossfade", "sec": 1}},
                               {"in_sec": 20, "out_sec": 22}])
    assert [(t, n) for _, t, n in E.clips(p)] == [(0.0, 4.0), (3.0, 3.0), (6.0, 2.0)]
    assert E.duration(p) == 8.0


def test_bad_clip_is_refused(fx, fx_probe):
    with pytest.raises(E.ProjectError, match="ends before"):
        E.clips(project(fx, fx_probe, [{"in_sec": 5, "out_sec": 5}]))


@pytest.mark.parametrize("clips, canvas, reason", [
    ([{"in_sec": 2, "out_sec": 6}, {"in_sec": 10, "out_sec": 14}], None, None),
    ([{"in_sec": 3, "out_sec": 6}], None, "keyframe"),
    ([{"in_sec": 2, "out_sec": 6}], {"width": 320, "height": 240, "fps": 24}, "canvas"),
    ([{"in_sec": 2, "out_sec": 6}], {"width": 640, "height": 480, "fps": 30}, "frame rate"),
    ([{"in_sec": 2, "out_sec": 6},
      {"in_sec": 10, "out_sec": 14, "transition_in": {"type": "crossfade", "sec": 0.5}}],
     None, "crossfade"),
])
def test_copy_only_when_nothing_changes_the_picture(fx, fx_probe, clips, canvas, reason):
    p = E.plan(project(fx, fx_probe, clips, canvas=canvas))
    if reason is None:
        assert p["mode"] == "copy" and p["reasons"] == []
    else:
        assert p["mode"] == "encode" and any(reason in r for r in p["reasons"])


def test_two_sources_need_encoding(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}, {"in_sec": 2, "out_sec": 6}])
    p["sources"]["other"] = dict(p["sources"]["fx"])
    p["tracks"]["video"][1]["source"] = "other"
    assert "more than one video" in E.plan(p)["reasons"][0]


def test_ducking_is_a_sidechain_on_the_speech(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 4}],
                music=[{"file": str(fx.music), "out_sec": 10, "gain_db": -12}],
                tracks={"music_duck": {"enabled": True, "threshold_db": -30}})
    chains, label = E.audio_graph(p, E.Inputs())
    graph = ";".join(chains)
    assert label == "mix" and "[music][key]sidechaincompress=threshold=0.031623" in graph


def test_gain_respects_the_peak_ceiling():
    s = {"target_lufs": -16, "target_tp": -1.5}
    assert E.gain_db(s, (-20.0, -10.0)) == 4.0
    assert E.gain_db(s, (-30.0, -3.0)) == pytest.approx(4.5)   # 3 dB of overs for the limiter
    assert E.gain_db(s, (None, None)) == 0.0


# ------------------------------------------------------------------ end to end
def test_lossless_export_copies_the_video(fx, fx_probe, tmp_path):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6},
                               {"in_sec": 10, "out_sec": 14,
                                "audio": {"file": str(fx.clean_speech), "start_sec": 0}}])
    out, result = E.run(write(tmp_path, p))
    assert out == tmp_path / "t.mp4" and result["mode"] == "copy"
    info = ffprobe(out, "format=duration:stream=codec_name,nb_frames,width")
    video = info["streams"][0]
    assert video["codec_name"] == "h264" and video["width"] == 640
    assert int(video["nb_frames"]) == 8 * 24
    assert abs(float(info["format"]["duration"]) - 8.0) < 0.05
    assert lufs(out) == pytest.approx(-16, abs=0.5)
    sidecar = json.loads(out.with_suffix(".params.json").read_text())
    assert sidecar["kind"] == "export" and sidecar["project"]["name"] == "t"


def test_encoded_export_with_crossfade_music_and_canvas(fx, fx_probe, tmp_path):
    p = project(fx, fx_probe,
                [{"in_sec": 1, "out_sec": 5},
                 {"in_sec": 10, "out_sec": 14, "transition_in": {"type": "crossfade", "sec": 1}},
                 {"in_sec": 30, "out_sec": 32, "audio": {"mute": True}}],
                music=[{"file": str(fx.music), "at_sec": 1, "out_sec": 30, "gain_db": -15,
                        "fade_in_sec": 1, "fade_out_sec": 1}],
                canvas={"width": 320, "height": 240, "fps": 30},
                export={"output": str(tmp_path / "custom.mp4"), "preset": "veryfast",
                        "target_lufs": -18})
    out, result = E.run(write(tmp_path, p))
    assert out == tmp_path / "custom.mp4" and result["mode"] == "encode"
    info = ffprobe(out, "format=duration:stream=codec_name,profile,width,height,r_frame_rate")
    v, a = info["streams"]
    assert (v["width"], v["height"], v["r_frame_rate"], v["profile"]) == (320, 240, "30/1", "High")
    assert a["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 9.0) < 0.1      # 4 + 4 + 2 − 1 s fade
    assert lufs(out) == pytest.approx(-18, abs=0.5)


def test_cli_plan_and_failure_events(fx, fx_probe, tmp_path):
    path = write(tmp_path, project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}]))
    out = subprocess.run([str(BIN / "clean-audio-export"), str(path), "--plan", "--progress",
                          "json"], capture_output=True, text=True)
    evs = [json.loads(line) for line in out.stdout.splitlines()]
    assert evs[-1]["event"] == "done"
    assert next(e for e in evs if e["event"] == "result")["results"]["mode"] == "copy"
    broken = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6,
                                     "audio": {"file": str(tmp_path / "gone.wav")}}])
    broken["name"] = "broken"
    out = subprocess.run([str(BIN / "clean-audio-export"), str(write(tmp_path, broken)),
                          "--progress", "json"], capture_output=True, text=True)
    last = json.loads(out.stdout.splitlines()[-1])
    assert out.returncode == 1 and last["event"] == "error" and "missing file" in last["text"]
