"""Tuning is saved after every window and resumed, so long runs are never repeated."""
import json

import pytest

from video_audio_cleaner import tune
from video_audio_cleaner.tune import Candidate


@pytest.fixture
def fake_render(monkeypatch):
    """Replace rendering with instant made-up measurements; record what was tuned."""
    tuned = []

    def evaluate(video, window, grid, separate_music, work, music=None):
        tuned.append(window.label)
        if window.label == "30m00s-31m00s":  # simulated Ctrl+C mid-run
            raise KeyboardInterrupt
        source = Candidate(None, "off", False, fidelity=1.0, speech_to_hiss=13.0,
                           warble=5.6, clicks=16.0)
        done = []
        for atten in grid[0]:
            for hiss in grid[1]:
                for dc in grid[2]:
                    c = Candidate(atten, hiss, dc, fidelity=0.97,
                                  speech_to_hiss=14.0 + (5 if hiss != "off" else 0),
                                  warble=5.6, clicks=12.0 if dc else 16.0)
                    done.append(c)
                    yield source, c, done

    monkeypatch.setattr(tune, "evaluate_window", evaluate)
    monkeypatch.setattr(tune, "write_listening_file", lambda *a: None)
    return tuned


def run(tmp_path, *windows, extra=()):
    tune.main(["/v/in.mp4", *windows, "--out_dir", str(tmp_path), "--source", "tape", "--atten", "10",
               "--hiss", "off,profile:12", "--declick", "off,on", *extra])
    return json.loads((tmp_path / "plan.json").read_text())


def test_second_run_tunes_only_new_windows(tmp_path, fake_render):
    run(tmp_path, "07m00s-08m00s", "10m00s-11m00s")
    assert fake_render == ["07m00s-08m00s", "10m00s-11m00s"]
    plan = run(tmp_path, "07m00s-08m00s", "10m00s-11m00s", "20m00s-21m00s")
    assert fake_render[2:] == ["20m00s-21m00s"]
    assert [w["label"] for w in plan["windows"]] == [
        "07m00s-08m00s", "10m00s-11m00s", "20m00s-21m00s"]


def test_nothing_new_means_no_tuning(tmp_path, fake_render, capsys):
    run(tmp_path, "07m00s-08m00s")
    run(tmp_path, "07m00s-08m00s")
    assert fake_render == ["07m00s-08m00s"]
    assert "Nothing new to tune" in capsys.readouterr().out


def test_progress_survives_an_interruption(tmp_path, fake_render):
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, "07m00s-08m00s", "30m00s-31m00s")
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert [w["label"] for w in plan["windows"]] == ["07m00s-08m00s"]
    assert not (tmp_path / "plan.json.partial").exists()


def test_retune_redoes_everything(tmp_path, fake_render):
    run(tmp_path, "07m00s-08m00s")
    run(tmp_path, "07m00s-08m00s", extra=["--retune"])
    assert fake_render == ["07m00s-08m00s", "07m00s-08m00s"]


def test_changed_criteria_rescore_without_rendering(tmp_path, fake_render, capsys):
    first = run(tmp_path, "07m00s-08m00s")
    # click weight 0 makes declick worthless, so the pick changes; nothing re-renders.
    second = run(tmp_path, "07m00s-08m00s", extra=["--click_weight", "0"])
    assert fake_render == ["07m00s-08m00s"]
    assert "--declick" in first["windows"][0]["flags"]
    assert second["criteria"]["click_weight"] == 0
    assert "rescored" in capsys.readouterr().out


def test_old_measurement_method_is_re_rendered(tmp_path, fake_render, capsys):
    run(tmp_path, "07m00s-08m00s")
    plan = json.loads((tmp_path / "plan.json").read_text())
    del plan["method"]                        # a plan from before method 2
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    run(tmp_path, "07m00s-08m00s")
    assert fake_render == ["07m00s-08m00s", "07m00s-08m00s"]
    assert "measured the old way" in capsys.readouterr().out


def test_changed_grid_redoes_everything(tmp_path, fake_render, capsys):
    run(tmp_path, "07m00s-08m00s")
    tune.main(["/v/in.mp4", "07m00s-08m00s", "--out_dir", str(tmp_path), "--source", "tape", "--atten", "20",
               "--hiss", "off,profile:12", "--declick", "off,on"])
    assert fake_render == ["07m00s-08m00s", "07m00s-08m00s"]
    assert "the candidate grid changed" in capsys.readouterr().out


def test_reused_windows_still_appear_in_the_report(tmp_path, fake_render):
    run(tmp_path, "07m00s-08m00s")
    run(tmp_path, "07m00s-08m00s", "10m00s-11m00s")
    report = (tmp_path / "report.md").read_text()
    assert "## 07m00s-08m00s" in report and "## 10m00s-11m00s" in report
    assert report.count("★") == 2


# ---------------------------------------------------------------- autotune cache
def test_autotune_reuses_a_window_tuned_under_another_label(tmp_path, fake_render):
    # Tuned via a windows file as "65m00s-65m25s"; autotune names it 01h05m00s-01h05m25s.
    run(tmp_path, "65m00s-65m25s")
    picked, entry, reused, _ = tune.autotune_window("/v/in.mp4", 3900.0, 3925.0, tmp_path)
    assert reused and fake_render == ["65m00s-65m25s"]
    assert entry["label"] == "65m00s-65m25s"
    assert picked.hiss == "profile:12"


def test_autotune_adds_new_windows_to_the_shared_cache(tmp_path, fake_render):
    run(tmp_path, "07m00s-08m00s")
    _, _, reused, plan_path = tune.autotune_window("/v/in.mp4", 600.0, 630.0, tmp_path)
    assert not reused
    labels = [w["label"] for w in json.loads(plan_path.read_text())["windows"]]
    assert labels == ["07m00s-08m00s", "10m00s-10m30s"]


def test_autotune_adopts_the_caches_grid(tmp_path, fake_render):
    run(tmp_path, "07m00s-08m00s")      # grid: atten 10 only (not the defaults)
    tune.autotune_window("/v/in.mp4", 600.0, 630.0, tmp_path)
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["grid"]["atten"] == "10" and len(plan["windows"]) == 2


def test_incompatible_tuning_is_archived_not_overwritten(tmp_path, fake_render, capsys):
    run(tmp_path, "07m00s-08m00s")
    tune.main(["/v/other.mp4", "07m00s-08m00s", "--out_dir", str(tmp_path), "--source", "tape", "--atten", "10",
               "--hiss", "off,profile:12", "--declick", "off,on"])
    archived = list(tmp_path.glob("plan.*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())["source"].endswith("in.mp4")
    assert "kept as" in capsys.readouterr().out


def test_default_tune_dir_is_per_video():
    from pathlib import Path
    assert tune.default_tune_dir("/out", "/v/04-.mp4") == Path("/out/_tuning/04-")
    assert tune.default_tune_dir("/out", "/v/a.mov", True) == Path("/out/_tuning/a-with-music")


def test_digital_source_tunes_without_hiss_or_declick(tmp_path, fake_render):
    tune.main(["/v/in.mp4", "07m00s-08m00s", "--out_dir", str(tmp_path), "--source", "digital"])
    plan = json.loads((tmp_path / "plan.json").read_text())
    cands = plan["windows"][0]["candidates"]
    assert len(cands) == 3                      # the three attens only
    assert {(c["hiss"], c["declick"]) for c in cands} == {("off", False)}
    assert plan["grid"]["source"] == "digital"


def test_autotune_does_not_reuse_tape_tuning_for_digital(tmp_path, fake_render, capsys):
    tune.autotune_window("/v/in.mp4", 420, 480, tmp_path)
    picked, entry, reused, _ = tune.autotune_window("/v/in.mp4", 420, 480, tmp_path,
                                                    source="digital")
    assert not reused and (picked.hiss, picked.declick) == ("off", False)
    assert list(tmp_path.glob("plan.*.json"))   # the tape tuning was kept aside


def test_missing_source_is_asked_or_refused(tmp_path, fake_render, monkeypatch):
    # No terminal to ask on: refuse with the flags to pass.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="--source digital"):
        tune.main(["/v/in.mp4", "07m00s-08m00s", "--out_dir", str(tmp_path)])
    # A terminal: ask until a valid answer.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["x", "d"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    tune.main(["/v/in.mp4", "07m00s-08m00s", "--out_dir", str(tmp_path)])
    assert json.loads((tmp_path / "plan.json").read_text())["grid"]["source"] == "digital"
