"""--progress json: the event stream the GUI drives the CLIs with (docs/gui-design.md §4)."""
import io
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from video_audio_cleaner import events, tune
from video_audio_cleaner.tune import Candidate

from .test_tune_resume import fake_render, run  # noqa: F401  (fixture)

BIN = Path(sys.executable).parent


def stream(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True)
    lines = out.stdout.splitlines()
    return out, [json.loads(line) for line in lines]   # any non-JSON line fails here


def test_clean_audio_emits_only_json_events(fx, tmp_path):
    out, evs = stream([str(BIN / "clean-audio"), str(fx.video), "--output_dir",
                       str(tmp_path), "--skip_first_n_sec", "2", "--process_n_seconds", "4",
                       "--normalize", "--progress", "json"])
    assert out.returncode == 0, out.stderr
    kinds = [e["event"] for e in evs]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert all(e["v"] == 1 for e in evs)
    steps = [e for e in evs if e["event"] == "step"]
    assert [s["index"] for s in steps] == list(range(1, steps[0]["total"] + 1))
    assert any(e["event"] == "progress" and e["step"] for e in evs)
    result = next(e for e in evs if e["event"] == "result")
    assert Path(result["output"]).exists() and Path(result["sidecar"]).exists()
    assert result["replaced"] is False
    assert -20 < result["results"]["final_lufs"] < -12
    record = json.loads(Path(result["sidecar"]).read_text())
    assert "--progress" not in record["reproduce"]
    assert "progress" not in record["parameters"]


def test_failure_ends_with_an_error_event(tmp_path):
    out, evs = stream([str(BIN / "clean-audio"), str(tmp_path / "missing.mp4"),
                       "--output_dir", str(tmp_path), "--progress", "json"])
    assert out.returncode == 1
    assert evs[0]["event"] == "start"
    assert evs[-1] == {"v": 1, "event": "error", "returncode": 1,
                       "text": evs[-1]["text"]}
    assert "not found" in evs[-1]["text"]


def test_tune_refusal_is_an_error_event(tmp_path):
    out, evs = stream([str(BIN / "clean-audio-tune"), str(tmp_path / "v.mp4"),
                       "--out_dir", str(tmp_path), "--progress", "json"])
    assert out.returncode == 1
    assert evs[-1]["event"] == "error" and "at least one window" in evs[-1]["text"]


@pytest.fixture
def capture_events(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(events, "emitter", events.JsonEmitter(buf))
    return lambda: [json.loads(line) for line in buf.getvalue().splitlines()]


def test_tune_reports_every_candidate_and_the_pick(tmp_path, fake_render, capture_events):
    run(tmp_path, "07m00s-08m00s")
    evs = capture_events()
    cands = [e for e in evs if e["event"] == "candidate"]
    assert [c["index"] for c in cands] == [0, 1, 2, 3]
    assert {c["total"] for c in cands} == {4} and cands[0]["window"] == "07m00s-08m00s"
    window = next(e for e in evs if e["event"] == "window")
    assert window["picked"]["hiss"] == "profile:12"
    assert window["plan"].endswith("plan.json")


def test_nan_is_sent_as_null(capture_events):
    events.emit("candidate", candidate=asdict(Candidate(10.0, "off", False)))
    assert capture_events()[0]["candidate"]["fidelity"] is None


# ------------------------------------------------------------------ candidate audio
def test_kept_candidate_audio_is_recorded_and_remeasured_when_missing(
        tmp_path, fake_render, monkeypatch):
    kept = []

    def keep(work, candidates, out_dir, label):
        kept.append(label)
        return {"dir": f"candidates/{label}", "source": {"file": "source.m4a", "lufs": -30.0},
                "candidates": [{"file": f"cand_{i}.m4a", "lufs": -25.0}
                               for i in range(len(candidates))]}

    monkeypatch.setattr(tune, "keep_candidate_audio", keep)
    run(tmp_path, "07m00s-08m00s")                       # tuned without audio
    plan = run(tmp_path, "07m00s-08m00s", "10m00s-11m00s", extra=["--keep_candidates"])
    assert fake_render == ["07m00s-08m00s", "07m00s-08m00s", "10m00s-11m00s"]
    assert kept == ["07m00s-08m00s", "10m00s-11m00s"]
    assert [w["label"] for w in plan["windows"]] == ["07m00s-08m00s", "10m00s-11m00s"]
    assert all(len(w["candidate_files"]["candidates"]) == 4 for w in plan["windows"])
    run(tmp_path, "07m00s-08m00s", extra=["--keep_candidates"])
    assert len(fake_render) == 3                          # has audio now: reused


def test_keep_candidate_audio_encodes_and_measures(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for name, volume in (("raw", 0.05), ("cand_0", 0.2), ("cand_1", 0.4)):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration=1,volume={volume}", "-ac", "2",
                        str(work / f"{name}.wav")], check=True)
    files = tune.keep_candidate_audio(str(work), [object(), object()], tmp_path, "00m00s-00m01s")
    folder = tmp_path / files["dir"]
    assert sorted(p.name for p in folder.iterdir()) == ["cand_0.m4a", "cand_1.m4a", "source.m4a"]
    lufs = [files["source"]["lufs"]] + [c["lufs"] for c in files["candidates"]]
    assert lufs[0] < lufs[1] < lufs[2]
    assert abs((lufs[2] - lufs[1]) - 6.0) < 0.5


# ------------------------------------------------------------------ user picks
def test_user_pick_is_sticky_and_used_by_autotune(tmp_path, fake_render):
    plan = run(tmp_path, "07m00s-08m00s")
    auto = plan["windows"][0]["picked"]
    entry = tune.set_user_pick(tmp_path / "plan.json", "07m00s-08m00s", 0)
    assert entry["user_pick"] == 0 and entry["picked"]["hiss"] == "off" != auto["hiss"]
    assert "--reduce_hiss" not in entry["flags"]
    # Rescoring under other criteria keeps it.
    plan = run(tmp_path, "07m00s-08m00s", extra=["--click_weight", "2"])
    assert plan["windows"][0]["picked"]["hiss"] == "off"
    picked, entry, reused, _ = tune.autotune_window("/v/in.mp4", 420, 480, tmp_path,
                                                    source="tape")
    assert reused and picked.hiss == "off" and picked.declick is False
    # And it can be handed back to autotune.
    entry = tune.set_user_pick(tmp_path / "plan.json", "07m00s-08m00s", None)
    assert entry["user_pick"] is None
    assert [entry["picked"][k] for k in ("atten", "hiss", "declick")] == \
        [auto[k] for k in ("atten", "hiss", "declick")]


# ------------------------------------------------------------------ --from_candidate
def _plan_for(video, tmp_path):
    cands = [asdict(Candidate(None, "off", False, fidelity=1.0)),
             asdict(Candidate(10.0, "off", False, fidelity=0.95, score=1.0))]
    plan = {"schema": 1, "method": tune.METHOD, "source": str(video),
            "grid": {"separate_music": False}, "criteria": {"fidelity_tolerance": 0.03},
            "windows": [{"label": "00m02s-00m06s", "start_sec": 2.0, "end_sec": 6.0,
                         "candidates": cands, "picked": cands[0], "music": None,
                         "source": {"speech_to_hiss": 1, "warble": 1, "clicks": 1}}]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_from_candidate_renders_that_candidate(fx, tmp_path):
    plan = _plan_for(fx.video, tmp_path)
    out = tmp_path / "out"
    _, evs = stream([str(BIN / "clean-audio"), str(fx.video), "--output_dir", str(out),
                     "--normalize", "--from_candidate", str(plan), "00m02s-00m06s", "1",
                     "--tag", "cand1", "--progress", "json"])
    assert evs[-1]["event"] == "done", evs[-3:]
    result = next(e for e in evs if e["event"] == "result")
    assert Path(result["output"]).name == "fixture_00m02s-00m06s_cand1.mp4"
    record = json.loads(Path(result["sidecar"]).read_text())
    assert record["parameters"]["atten_lim_db"] == 10.0
    assert record["parameters"]["from_candidate"] is None
    assert "--from_candidate" not in record["reproduce"]
    tuning = record["results"]["autotune"]
    assert tuning["chosen_by"] == "user" and tuning["candidate_index"] == 1
    assert tuning["autotune_pick"]["atten"] is None


@pytest.mark.parametrize("extra, message", [
    (["00m09s-00m10s", "0"], "has no window 00m09s-00m10s"),
    (["00m02s-00m06s", "7"], "candidates 0-1"),
    (["00m02s-00m06s", "0", "--autotune"], "use one"),
    (["00m02s-00m06s", "0", "--skip_first_n_sec", "3"], "disagrees"),
])
def test_from_candidate_refusals(fx, tmp_path, extra, message):
    plan = _plan_for(fx.video, tmp_path)
    _, evs = stream([str(BIN / "clean-audio"), str(fx.video), "--output_dir", str(tmp_path),
                     "--from_candidate", str(plan), *extra, "--progress", "json"])
    assert evs[-1]["event"] == "error" and message in evs[-1]["text"]


def test_from_candidate_refuses_another_video(fx, tmp_path):
    plan = _plan_for(tmp_path / "other.mp4", tmp_path)
    _, evs = stream([str(BIN / "clean-audio"), str(fx.video), "--output_dir", str(tmp_path),
                     "--from_candidate", str(plan), "00m02s-00m06s", "0", "--progress", "json"])
    assert "was tuned on" in evs[-1]["text"]
