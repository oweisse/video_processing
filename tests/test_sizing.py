"""Export to a size budget (docs/stitch-and-size-budget-plan.md §10)."""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from video_audio_cleaner import export as E
from video_audio_cleaner import probe, sizing

BIN = Path(sys.executable).parent


@pytest.fixture(scope="module")
def fx_probe(fx):
    return probe.probe(str(fx.video))


def project(fx, fx_probe, clips, **export):
    return {"schema": 1, "name": "t", "canvas": None,
            "sources": {"fx": {"path": str(fx.video), "probe": fx_probe}},
            "tracks": {"video": [{"id": f"c{i}", "source": "fx", **c} for i, c in enumerate(clips)],
                       "music": []},
            "export": export}


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


@pytest.fixture
def small_samples(monkeypatch):
    monkeypatch.setattr(sizing, "SAMPLE_LEN", 2.0)
    monkeypatch.setattr(sizing, "SAMPLE_COUNT", 2)


# ------------------------------------------------------------------ arithmetic
@pytest.mark.parametrize("text, gb", [("2GB/h", 2.0), ("2 GB/hour", 2.0), ("1500MB/h", 1.5),
                                      ("0.5 gb per hour", 0.5), ("1.5GB/hr", 1.5)])
def test_budget_per_hour_parsing(text, gb):
    assert sizing.parse_budget(text) == {"per_hour_gb": gb}


@pytest.mark.parametrize("text, gb", [("2.5GB", 2.5), ("900MB", 0.9), ("900 mb", 0.9)])
def test_total_size_parsing(text, gb):
    assert sizing.parse_size(text) == {"total_gb": gb}


@pytest.mark.parametrize("bad", ["2GB", "lots/h", "0GB/h", "2 parsecs/h"])
def test_bad_budgets_are_refused(bad):
    with pytest.raises(ValueError):
        sizing.parse_budget(bad)


def test_bitrate_arithmetic():
    assert sizing.budget_bytes({"per_hour_gb": 2}, 80 * 60) == pytest.approx(2.6667e9, rel=1e-4)
    assert sizing.budget_bytes({"total_gb": 2.5}, 999) == 2.5e9
    # 2 GB per hour for an hour, less 1.5 % for the container and the sound.
    assert sizing.video_budget_bps(2e9, 3600, 192_000) == pytest.approx(4_185_778, rel=1e-6)
    with pytest.raises(E.ProjectError, match="too small"):
        sizing.video_budget_bps(20e6, 3600, 192_000)


def test_overrides_and_defaults():
    s = sizing.with_overrides(E.DEFAULT_EXPORT, {"budget": {"per_hour_gb": 2}, "codec": None})
    assert s["budget"] == {"per_hour_gb": 2} and s["codec"] == "hevc"   # HEVC under a budget
    plain = sizing.with_overrides(E.DEFAULT_EXPORT, {"budget": None})
    assert plain["budget"] is None and plain.get("codec") is None
    assert sizing.with_overrides({**E.DEFAULT_EXPORT, "budget": {"per_hour_gb": 0}})["budget"] is None
    with pytest.raises(E.ProjectError):
        sizing.with_overrides({**E.DEFAULT_EXPORT, "codec": "av1"})


def test_output_canvas_scales_down_only():
    cv = {"width": 3840, "height": 2160, "fps": 30}
    assert sizing.output_canvas(cv, 1080) == {"width": 1920, "height": 1080, "fps": 30}
    assert sizing.output_canvas(cv, 4320) == cv and sizing.output_canvas(cv, None) == cv
    assert sizing.output_canvas({"width": 1366, "height": 720, "fps": 30}, 480)["width"] == 910


def test_todays_encoder_arguments_are_unchanged():
    cv = {"width": 1280, "height": 720, "fps": 30}
    assert E.encoder_args(E.DEFAULT_EXPORT, cv) == [
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-profile:v", "high",
        "-pix_fmt", "yuv420p"]
    assert E.encoder_args({**E.DEFAULT_EXPORT, "encoder": "h264_videotoolbox"}, cv) == [
        "-c:v", "h264_videotoolbox", "-b:v", "8M", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    hevc = E.encoder_args({**E.DEFAULT_EXPORT, "codec": "hevc"}, cv,
                          {"bps": 2_000_000, "pass": 2, "log": "/w/stats"})
    assert hevc[:6] == ["-c:v", "libx265", "-preset", "medium", "-b:v", "2000000"]
    assert "pass=2:stats=/w/stats.log" in hevc[hevc.index("-x265-params") + 1]
    assert hevc[hevc.index("-tag:v") + 1] == "hvc1" and hevc[-2:] == ["-g", "60"]


# ------------------------------------------------------------------ samples
@pytest.mark.parametrize("seconds, height, shape", [
    (3600, 720, (3.0, 10)), (60, 1080, (3.0, 10)), (3600, 2160, (2.0, 6))])
def test_sample_shape(seconds, height, shape):
    assert sizing.sample_shape(seconds, height) == shape


def test_sizes_in_words():
    assert sizing.size_words(2.5e9) == "2.50 GB" and sizing.size_words(930e6) == "930 MB"


def test_samples_cover_long_clips_and_weigh_the_whole_timeline():
    spans = [(0, 100), (100, 1500), (1500, 1580), (1580, 1600), (1600, 1700), (1700, 3600)]
    samples = sizing.pick_samples(3600, spans, 8, 6)
    assert len(samples) == 8           # six parts, plus the 0–100 and 1600–1700 clips
    assert sum(s["weight"] for s in samples) == pytest.approx(3600)
    for a, b in spans:
        if b - a >= 60:
            assert any(a <= s["at_sec"] < b for s in samples), (a, b)
    extra = next(s for s in samples if s["at_sec"] == 1650)
    assert extra["weight"] == 100 and extra["t1"] - extra["t0"] == 8
    # Short timelines are one sample: all of it.
    assert sizing.pick_samples(50, [(0, 50)], 8, 6) == [
        {"t0": 0.0, "t1": 50, "at_sec": 25.0, "weight": 50}]
    many = sizing.pick_samples(36000, [(i * 100, i * 100 + 100) for i in range(360)], 8, 12)
    assert len(many) == 16 and sum(s["weight"] for s in many) == pytest.approx(36000)


def test_excerpt_trims_clips_and_turns_crossfades_into_cuts(fx, fx_probe):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 10},
                               {"in_sec": 20, "out_sec": 30,
                                "transition_in": {"type": "crossfade", "sec": 2}},
                               {"in_sec": 40, "out_sec": 45}])
    p["tracks"]["music"] = [{"file": str(fx.music), "out_sec": 10}]
    # Timeline: clip 0 [0, 10], clip 1 [8, 18] (crossfade: the cut is at 9), clip 2 [18, 23].
    ex = E.excerpt(p, 7, 20)
    got = [(c["source"], c["in_sec"], c["out_sec"]) for c in ex["tracks"]["video"]]
    assert got == [("fx", 7, 9), ("fx", 21, 31 - 1), ("fx", 40, 42)]
    assert all("transition_in" not in c for c in ex["tracks"]["video"])
    assert ex["tracks"]["music"] == [] and E.duration(ex) == pytest.approx(13)
    assert ex["canvas"] == E.canvas(p) and p["tracks"]["video"][1]["in_sec"] == 20   # pure


# ------------------------------------------------------------------ the CRF for a bitrate
@pytest.mark.parametrize("per_halving", [6.0, 4.5, 8.0])
def test_two_points_find_the_crf(per_halving):
    bps = lambda q: 8e6 * 2 ** (-q / per_halving)
    cap = bps(20) / 4
    q1 = sizing.crf_for(cap, 20.0, bps(20))            # the first guess: halving every 6
    assert q1 == pytest.approx(32, abs=0.01)
    assert sizing.crf_for(cap, 20.0, bps(20), q1, bps(q1)) == pytest.approx(20 + 2 * per_halving, abs=0.01)


def test_crf_stays_between_the_ceiling_and_40():
    assert sizing.crf_for(1e3, 20.0, 8e6) == 40.0
    assert sizing.crf_for(9e6, 20.0, 8e6) == 20.0


# ------------------------------------------------------------------ quality words
@pytest.mark.parametrize("metric, score, text", [
    ("vmaf", 95, "Excellent"), ("vmaf", 93, "Excellent"), ("vmaf", 88, "Good"),
    ("vmaf", 80, "Fair"), ("vmaf", 60, "Soft"),
    ("ssim", 0.985, "Excellent"), ("ssim", 0.97, "Good"), ("ssim", 0.94, "Fair"),
    ("ssim", 0.9, "Soft")])
def test_words(metric, score, text):
    assert sizing.words(metric, score).startswith(text)


def test_a_weak_part_is_named():
    assert sizing.words("vmaf", 90, 75, 2470).endswith("one part is noticeably weaker (at 41:10)")
    assert "weaker" not in sizing.words("vmaf", 90, 85, 2470)


def test_vmaf_is_used_only_when_ffmpeg_has_it():
    assert sizing.has_vmaf(" .. libvmaf           VV->V      Calculate the VMAF")
    assert not sizing.has_vmaf(" TS ssim              VV->V      Calculate the SSIM")
    assert sizing.parse_quality("ssim", "[Parsed_ssim_0 @ 0x1] SSIM Y:0.99 U:0.98 V:0.98 "
                                        "All:0.986 (18.5)") == 0.986


# ------------------------------------------------------------------ source ceiling
def test_copy_is_chosen_when_it_fits(fx, fx_probe, fx_twin, tmp_path, monkeypatch):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}])
    p["sources"]["b"] = {"path": str(fx_twin), "probe": probe.probe(str(fx_twin))}
    p["tracks"]["video"].append({"id": "b0", "source": "b", "in_sec": 4, "out_sec": 8})
    assert E.plan(p)["mode"] == "copy"
    class Sampled(Exception):
        pass

    def sampled(*a):
        raise Sampled
    monkeypatch.setattr(sizing.Sampler, "reference", sampled)
    s = sizing.with_overrides(E.settings(p), {"budget": {"per_hour_gb": 50}})
    est = sizing.estimate(p, s, True, str(tmp_path))
    assert est["mode"] == "copy" and est["predicted_bytes"] < est["budget_bytes"]
    assert "joined without re-encoding" in est["notes"][0]
    # Over the budget, or not allowed by plan(): the samples decide.
    for budget, allowed in (({"per_hour_gb": 0.5}, True), ({"per_hour_gb": 50}, False)):
        s = sizing.with_overrides(E.settings(p), {"budget": budget})
        with pytest.raises(Sampled):
            sizing.estimate(p, s, allowed, str(tmp_path))


def test_budget_above_the_source_is_capped_by_it(fx, fx_probe, tmp_path, small_samples):
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": 8}], preset="ultrafast")
    s = sizing.with_overrides(E.settings(p), {"budget": {"per_hour_gb": 100}})
    est = sizing.estimate(p, s, False, str(tmp_path), metric="ssim")
    assert est["cap"] == "source" and est["source_bps"] == fx_probe["video"]["bit_rate"]
    assert est["mode"] == "quality" and est["crf"] == 20.0
    assert est["metric"] == "ssim" and 0.9 < est["quality_mean"] <= 1
    assert est["audio"]["bps"] == 192_000 and est["footage"] == {"height": 480, "seconds": 8.0}
    assert est["quality_low_video"] == "fixture.mp4"
    assert list(tmp_path.iterdir()) == []                  # references and trials are gone


# ------------------------------------------------------------------ real runs
def noisy(fx, fx_probe, seconds=12):
    """A picture with grain, so the budget bites even on a small frame."""
    p = project(fx, fx_probe, [{"in_sec": 0, "out_sec": seconds,
                                "video_filters": ["noise=alls=20:allf=t"]}],
                preset="veryfast", audio_bitrate="64k")
    return p


def test_budgeted_export_fits_the_budget(fx, fx_probe, tmp_path, small_samples, monkeypatch):
    path = write(tmp_path, noisy(fx, fx_probe))
    budget = {"total_gb": 0.0006}                       # 600 kB for 12 s
    est = sizing.run_estimate(path, {"budget": budget})
    saved = json.loads((path.parent / "estimate.json").read_text())
    assert saved == json.loads(json.dumps(est)) and est["mode"] == "size"
    assert est["cap"] == "budget" and est["codec"] == "hevc" and est["passes"] == 2
    assert est["crf_equivalent"] > 20 and len(est["samples"]) == 2
    assert not list(tmp_path.glob("*.mp4"))             # an estimate writes no video
    calls = []
    real = sizing.estimate
    monkeypatch.setattr(sizing, "estimate", lambda *a, **k: calls.append(1) or real(*a, **k))
    out, result = E.run(path, overrides={"budget": budget})
    assert calls == []                                  # the saved estimate was reused
    info = ffprobe(out, "stream=codec_name,codec_tag_string")["streams"]
    assert (info[0]["codec_name"], info[0]["codec_tag_string"]) == ("hevc", "hvc1")
    size = out.stat().st_size
    assert abs(size / 600_000 - 1) < 0.10
    assert result["budget_mode"] == "size" and result["passes"] == 2
    assert result["size_bytes"] == size and result["budget_bytes"] == 600_000
    sidecar = json.loads(out.with_suffix(".params.json").read_text())
    assert sidecar["results"]["estimate"]["key"] == est["key"]
    assert len(sidecar["commands"]) == 2 and "pass=1" in sidecar["commands"][0]
    # Another budget: the estimate no longer matches and is made again.
    E.run(path, overrides={"budget": {"total_gb": 0.0008}, "one_pass": True})
    assert calls == [1]


def test_plain_export_honours_codec_and_height(fx, fx_probe, tmp_path):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}], preset="ultrafast")
    out, result = E.run(write(tmp_path, p), overrides={"codec": "hevc", "height": 240})
    v = ffprobe(out, "stream=codec_name,codec_tag_string,width,height")["streams"][0]
    assert (v["codec_name"], v["codec_tag_string"], v["width"], v["height"]) == \
        ("hevc", "hvc1", 320, 240)
    assert result["mode"] == "encode" and "smaller" in result["reasons"][0]
    assert "budget_mode" not in result


def test_cli_estimate_event(fx, fx_probe, fx_twin, tmp_path):
    p = project(fx, fx_probe, [{"in_sec": 2, "out_sec": 6}])
    p["sources"]["b"] = {"path": str(fx_twin), "probe": probe.probe(str(fx_twin))}
    p["tracks"]["video"].append({"id": "b0", "source": "b", "in_sec": 4, "out_sec": 8})
    path = write(tmp_path, p)
    out = subprocess.run([str(BIN / "clean-audio-export"), str(path), "--estimate",
                          "--budget", "20GB/h", "--progress", "json"],
                         capture_output=True, text=True)
    evs = [json.loads(line) for line in out.stdout.splitlines()]
    assert evs[-1]["event"] == "done", out.stderr
    result = next(e for e in evs if e["event"] == "result")
    assert result["output"] is None and result["results"]["estimate"]["mode"] == "copy"
    assert (path.parent / "estimate.json").exists() and not list(tmp_path.glob("*.mp4"))
    bad = subprocess.run([str(BIN / "clean-audio-export"), str(path), "--estimate"],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "needs a budget" in bad.stderr


def test_a_cancelled_estimate_leaves_nothing(fx, fx_probe, tmp_path, small_samples, monkeypatch):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    path = write(tmp_path, noisy(fx, fx_probe, 8))

    def stop(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(sizing.Sampler, "encode", stop)
    with pytest.raises(KeyboardInterrupt):
        sizing.run_estimate(path, {"budget": {"total_gb": 0.001}})
    assert list(scratch.iterdir()) == [] and not (path.parent / "estimate.json").exists()
