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


def two_videos(fx, fx_probe, second_path, second_probe, clips2=({"in_sec": 4, "out_sec": 8},)):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}])
    p["sources"]["b"] = {"path": str(second_path), "probe": second_probe}
    p["tracks"]["video"] += [{"id": f"b{i}", "source": "b", "audio": None, **c}
                             for i, c in enumerate(clips2)]
    return p


def test_videos_of_one_format_are_copied(fx, fx_probe, fx_twin):
    p = E.plan(two_videos(fx, fx_probe, fx_twin, probe.probe(str(fx_twin))))
    assert p["mode"] == "copy" and p["reasons"] == []


def test_videos_of_different_formats_are_encoded(fx, fx_probe, fx_other, fx_twin):
    p = E.plan(two_videos(fx, fx_probe, fx_other, probe.probe(str(fx_other))))
    assert p["mode"] == "encode"
    assert any("different picture sizes (640×480 and 320×240)" in r for r in p["reasons"])
    twin = probe.probe(str(fx_twin))
    twin["video"]["extradata_md5"] = "0" * 32
    assert any("different encoder settings" in r
               for r in E.plan(two_videos(fx, fx_probe, fx_twin, twin))["reasons"])
    twin["video"]["extradata_md5"] = None
    assert any("not been compared" in r
               for r in E.plan(two_videos(fx, fx_probe, fx_twin, twin))["reasons"])


def test_ducking_is_a_sidechain_on_the_speech(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 4}],
                music=[{"file": str(fx.music), "out_sec": 10, "gain_db": -12}],
                tracks={"music_duck": {"enabled": True, "threshold_db": -30}})
    chains, label = E.audio_graph(p, E.Inputs())
    graph = ";".join(chains)
    assert label == "mix" and "[music][key]sidechaincompress=threshold=0.031623" in graph


def test_music_stays_level_when_lowering_is_off(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 4}],
                music=[{"file": str(fx.music), "out_sec": 10, "gain_db": -12}],
                tracks={"music_duck": {"enabled": False}})
    graph = ";".join(E.audio_graph(p, E.Inputs())[0])
    assert "sidechaincompress" not in graph and "[speech][music]amix" in graph


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


# ------------------------------------------------------------------ several videos, pixel shape
DATA = Path(__file__).parent / "data"


@pytest.mark.parametrize("case", json.loads((DATA / "canvas.json").read_text())["cases"],
                         ids=lambda c: c["name"])
def test_suggest_canvas(case):
    assert E.suggest_canvas(case["items"]) == case["expect"]


def test_probe_records_what_joining_and_shape_need(fx, fx_probe, fx_anamorphic):
    v = fx_probe["video"]
    assert fx_probe["probe_version"] == probe.PROBE_VERSION
    assert v["sar"] == 1.0 and v["profile"] and v["time_base"] and len(v["extradata_md5"]) == 32
    assert probe.probe(str(fx_anamorphic))["video"]["sar"] == pytest.approx(16 / 15, abs=1e-4)
    assert [probe._sar(t) for t in ("16:15", "0:1", None, "1:1")] == \
        [pytest.approx(1.066667), 1.0, 1.0, 1.0]


def test_non_square_pixels_are_squared(fx, fx_probe, fx_anamorphic, tmp_path):
    ana = probe.probe(str(fx_anamorphic))
    proj = {"schema": 1, "name": "ana", "canvas": None,
            "sources": {"a": {"path": str(fx_anamorphic), "probe": ana}},
            "tracks": {"video": [{"id": "c0", "source": "a", "in_sec": 1.0, "out_sec": 3.0,
                                  "audio": None}], "music": []}, "export": {}}
    # 360 stored pixels at 16:15 show as 384.
    assert E.canvas(proj) == {"width": 384, "height": 288, "fps": 25.0}
    chains, _ = E.video_graph(proj, E.Inputs(), E.canvas(proj))
    assert "scale=trunc(iw*sar/2)*2:ih,setsar=1" in chains[0]
    square, _ = E.video_graph(project(fx, fx_probe, [{"in_sec": 0, "out_sec": 2}]), E.Inputs(),
                              E.canvas(project(fx, fx_probe, [{"in_sec": 0, "out_sec": 2}])))
    assert "iw*sar" not in square[0]
    out = tmp_path / "ana.mp4"
    E.run(write(tmp_path, proj), out, force_encode=True)
    s = ffprobe(out, "stream=width,height,sample_aspect_ratio")["streams"][0]
    assert (s["width"], s["height"]) == (384, 288) and s.get("sample_aspect_ratio", "1:1") == "1:1"


def test_old_embedded_probes_are_refreshed(fx, fx_probe):
    old = {k: v for k, v in fx_probe.items() if k != "probe_version"}
    proj = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 2}])
    proj["sources"]["fx"]["probe"] = old
    assert E.ensure_probes(proj)["sources"]["fx"]["probe"]["probe_version"] == probe.PROBE_VERSION


def test_previews_show_the_true_shape(fx_anamorphic, tmp_path):
    from video_audio_cleaner import prep
    prep.thumbs(str(fx_anamorphic), tmp_path / "thumbs")
    first = sorted((tmp_path / "thumbs").glob("*.jpg"))[0]
    s = ffprobe(first, "stream=width,height")["streams"][0]
    assert s["width"] / s["height"] == pytest.approx(384 / 288, abs=0.02)


def test_lossless_stitch_of_two_videos(fx, fx_probe, fx_twin, tmp_path):
    p = two_videos(fx, fx_probe, fx_twin, probe.probe(str(fx_twin)))
    out, result = E.run(write(tmp_path, p))
    assert result["mode"] == "copy" and result["copy_verified"] is True
    info = ffprobe(out, "format=duration:stream=nb_frames")
    assert int(info["streams"][0]["nb_frames"]) == 8 * 24
    # The pictures are the sources' own, not re-encoded: compare decoded frame hashes.
    def frames(path, start, dur):
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i",
                              str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                             capture_output=True, text=True, check=True).stdout
        return [l.split(",")[-1].strip() for l in out.splitlines() if not l.startswith("#")]
    assert frames(out, 4, 4) == frames(fx_twin, 4, 4)
    assert frames(out, 0, 4) == frames(fx.video, 2, 4)


def test_failed_lossless_join_falls_back_to_encoding(fx, fx_probe, fx_twin, tmp_path, monkeypatch):
    monkeypatch.setattr(E, "verify_copy", lambda *a: "the picture does not decode cleanly at 0:04")
    p = two_videos(fx, fx_probe, fx_twin, probe.probe(str(fx_twin)))
    p["export"] = {"preset": "ultrafast"}
    out, result = E.run(write(tmp_path, p))
    assert result["mode"] == "encode" and result["copy_verified"] is False
    assert "lossless joining failed" in result["reasons"][0]
    assert abs(float(ffprobe(out, "format=duration")["format"]["duration"]) - 8.0) < 0.1


def test_sound_changes_inside_one_video_do_not_break_copying(fx, fx_probe, tmp_path):
    # 5 s is not a keyframe (every 2 s), but the picture does not jump there.
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 5},
                               {"in_sec": 5, "out_sec": 8,
                                "audio": {"file": str(fx.clean_speech), "start_sec": 0}}])
    assert [r["clips"] for r in E.picture_runs(p)] == [[0, 1]]
    assert E.plan(p)["mode"] == "copy"
    assert E.concat_list(p).count("file ") == 1
    out, result = E.run(write(tmp_path, p))
    assert result["mode"] == "copy"
    assert int(ffprobe(out, "stream=nb_frames")["streams"][0]["nb_frames"]) == 6 * 24
    # A jump inside the same video at a non-keyframe still needs re-encoding.
    jump = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 5}, {"in_sec": 9, "out_sec": 12}])
    assert any("keyframe" in r for r in E.plan(jump)["reasons"])


# ------------------------------------------------------------------ soft sound between clips
def joins_case_project(case):
    return {"schema": 1, "name": "j", "canvas": None,
            "sources": {k: {"path": k, "probe": {"duration_sec": v}} for k, v in case["sources"].items()},
            "tracks": {"video": [{"id": f"c{i}", **c} for i, c in enumerate(case["clips"])],
                       "music": []}, "export": {}}


@pytest.mark.parametrize("case", json.loads((DATA / "joins.json").read_text())["cases"],
                         ids=lambda c: c["name"])
def test_joins(case):
    got = E.joins(joins_case_project(case), durations=lambda f: case["files"].get(f))
    assert got == case["expect"]


def test_unknown_transition_is_refused(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 2},
                               {"in_sec": 4, "out_sec": 6, "transition_in": {"type": "wipe", "sec": 1}}])
    with pytest.raises(E.ProjectError, match="unknown transition"):
        E.clips(p)


SOFT = {"type": "soft_cut", "sec": 1}


def test_soft_cut_graphs(fx, fx_probe, fx_twin):
    blend = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6},
                                   {"in_sec": 10, "out_sec": 14, "transition_in": SOFT}])
    assert [j["kind"] for j in E.joins(blend)] == ["cut", "blend"]
    graph = ";".join(E.audio_graph(blend, E.Inputs())[0])
    assert "acrossfade=d=1.000000:c1=qsin:c2=qsin" in graph
    assert "atrim=duration=4.500000" in graph            # each side borrows 0.5 s
    # The first video ends at the join and the next one starts there: nothing to overlap.
    dip = two_videos(fx, fx_probe, fx_twin, probe.probe(str(fx_twin)),
                     clips2=({"in_sec": 0, "out_sec": 4, "transition_in": SOFT},))
    dip["tracks"]["video"][0].update(in_sec=56, out_sec=60)
    assert [j["kind"] for j in E.joins(dip)] == ["cut", "dip"]
    graph = ";".join(E.audio_graph(dip, E.Inputs())[0])
    assert "afade=t=out:st=3.500000:d=0.500000:curve=qsin" in graph
    assert "afade=t=in:st=0:d=0.500000:curve=qsin" in graph and "acrossfade" not in graph
    # The sound alone changes: the picture can still be copied.
    assert E.plan(blend)["mode"] == "copy"


def rms_around(path, t, half=0.15):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t - half:.3f}", "-t", f"{2 * half:.3f}",
                          "-i", str(path), "-map", "0:a:0", "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    import numpy as np
    x = np.frombuffer(raw, dtype=np.float32)
    return float(np.sqrt(np.mean(x ** 2)))


def test_soft_cuts_keep_the_length_and_blend_without_a_gap(fx, fx_probe, fx_twin, tmp_path):
    p = two_videos(fx, fx_probe, fx_twin, probe.probe(str(fx_twin)),
                   clips2=({"in_sec": 0, "out_sec": 4, "transition_in": SOFT},
                           {"in_sec": 8, "out_sec": 12, "transition_in": SOFT}))
    p["tracks"]["video"][0].update(in_sec=56, out_sec=60)
    assert [j["kind"] for j in E.joins(p)] == ["cut", "dip", "blend"]
    out, result = E.run(write(tmp_path, p))
    assert result["mode"] == "copy"
    assert abs(float(ffprobe(out, "format=duration")["format"]["duration"]) - 12.0) < 0.05
    # The dip goes quiet at its cut (4 s); the blend at 8 s does not.
    assert rms_around(out, 8.0) > 3 * rms_around(out, 4.0, half=0.05)


def test_a_cleaned_piece_blends_into_the_original_that_goes_on(fx, fx_probe, tmp_path):
    # Cleaned sound for 10–20 s only, then the original: the whole overlap sits before
    # the cut, where the original sound already exists, and nothing goes quiet.
    p = project(fx, fx_probe, [
        {"in_sec": 10, "out_sec": 20, "audio": {"file": str(fx.clean_speech), "start_sec": 10,
                                                "duration_sec": 10}},
        {"in_sec": 20, "out_sec": 30, "transition_in": SOFT}])
    assert E.joins(p)[1] == {"kind": "blend", "sec": 1, "lead": 1}
    graph = ";".join(E.audio_graph(p, E.Inputs())[0])
    assert "acrossfade=d=1.000000:c1=qsin:c2=qsin" in graph
    out, _ = E.run(write(tmp_path, p))
    assert abs(float(ffprobe(out, "format=duration")["format"]["duration"]) - 20.0) < 0.05



def test_zoomed_clip_is_cropped_and_scaled_to_the_canvas(fx, fx_probe, tmp_path):
    clip = {"in_sec": 0, "out_sec": 2, "audio": None, "frame": {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.5}}
    proj = project(fx, fx_probe, [clip])
    chains, _ = E.video_graph(proj, E.Inputs(), E.canvas(proj))
    assert "crop=w=trunc(iw*0.500000/2)*2:h=trunc(ih*0.500000/2)*2:x=trunc(iw*0.250000):y=trunc(ih*0.500000)" in chains[0]
    assert ":flags=lanczos" in chains[0]
    assert "zoomed clips need re-encoding" in E.plan(proj)["reasons"]
    # a frame past the edge is pulled back inside; the whole picture is no zoom
    assert E.framing({"frame": {"x": 0.9, "y": -1, "w": 0.5, "h": 0.5}}) == {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}
    assert E.framing({"frame": {"x": 0, "y": 0, "w": 1, "h": 1}}) is None
    out = tmp_path / "zoom.mp4"
    E.run(write(tmp_path, proj), out)
    cv = E.canvas(proj)
    s = ffprobe(out, "stream=width,height")["streams"][0]
    assert (s["width"], s["height"]) == (cv["width"], cv["height"])


def test_zoom_glide_moves_the_framing_smoothly(fx, fx_probe, tmp_path):
    close = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
    wide = {"in_sec": 0, "out_sec": 1, "audio": None}
    zoomed = {"in_sec": 1, "out_sec": 3, "audio": None, "frame": close}
    proj = project(fx, fx_probe, [wide, zoomed])
    clips = proj["tracks"]["video"]
    g = E.zoom_glide(clips[0], clips[1])
    assert g == {"from": None, "to": close, "sec": 1.0}
    assert E.zoom_glide(clips[0], {**clips[1], "zoom_glide": False}) is None        # opted out
    assert E.zoom_glide(clips[0], {**clips[1], "in_sec": 1.5}) is None              # a gap: a real cut
    assert E.zoom_glide(clips[0], {**clips[1], "zoom_glide": {"sec": 0.5}})["sec"] == 0.5
    chains, _ = E.video_graph(proj, E.Inputs(), E.canvas(proj))
    assert "zoompan=" in chains[1] and "crop=" not in chains[1] and "zoompan" not in chains[0]

    out = tmp_path / "glide.mp4"
    E.run(write(tmp_path, proj), out)
    cv = E.canvas(proj)
    s = ffprobe(out, "stream=width,height,duration")["streams"][0]
    assert (s["width"], s["height"]) == (cv["width"], cv["height"])

    # the picture during the glide sits between the wide shot and the close-up
    import numpy as np

    def frame_at(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(out), "-frames:v", "1",
                              "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).astype(float)
    wide_f, mid_f, close_f = frame_at(0.5), frame_at(1.5), frame_at(2.5)
    d = lambda a, b: float(np.abs(a - b).mean())
    assert d(mid_f, wide_f) > 2 and d(mid_f, close_f) > 2          # neither end: moving
    assert d(close_f, frame_at(2.9)) < d(close_f, wide_f)           # after the glide: settled
