import argparse
import json
import math

import pytest

from video_audio_cleaner import batch
from video_audio_cleaner.cli import build_parser as cli_parser
from video_audio_cleaner.tune import (Candidate, parse_grid, parse_window,
                                      score_candidates)


# ---------------------------------------------------------------- windows
@pytest.mark.parametrize("text, label, start, end", [
    ("07m00s-08m00s", "07m00s-08m00s", 420, 480),
    ("04-random_chat_w_dad_clean_65m00s-65m25s.mp4", "65m00s-65m25s", 3900, 3925),
    ("1h05m-1h05m25s", "01h05m00s-01h05m25s", 3900, 3925),
    ("90-120", "01m30s-02m00s", 90, 120),
])
def test_parse_window(text, label, start, end):
    w = parse_window(text)
    assert (w.label, w.start, w.end) == (label, start, end)


@pytest.mark.parametrize("bad", ["04-.mp4", "08m00s-07m00s", "nothing"])
def test_parse_window_rejects(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_window(bad)


def test_parse_grid():
    attens, hisses, declicks = parse_grid("10,none", "off,profile:12,patch:0.0003", "off,on,20")
    assert attens == [10.0, None]
    assert hisses == ["off", "profile:12", "patch:0.0003"]
    assert declicks == [False, 12.0, 20.0]
    with pytest.raises(argparse.ArgumentTypeError):
        parse_grid("10", "profile", "off")
    for bad in ("loud", "0", "-3"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_grid("10", "off", bad)


def test_declick_strength_reaches_the_flags():
    c = Candidate(atten=10.0, hiss="off", declick=20.0)
    args = cli_parser().parse_args(["in.mp4", "--output_dir", "o", *c.flags()])
    assert (args.declick, args.declick_nr) == (True, 20.0)
    assert c.describe() == "atten 10, hiss off, declick 20"
    old = Candidate(atten=10.0, hiss="off", declick=True)   # plans from before strengths
    assert old.declick_nr == 12.0


def test_candidate_flags_are_valid_clean_audio_flags():
    c = Candidate(atten=10.0, hiss="profile:20", declick=True, separate_music=True)
    args = cli_parser().parse_args(["in.mp4", "--output_dir", "o", *c.flags()])
    assert (args.atten_lim_db, args.reduce_hiss, args.hiss_method, args.hiss_nr,
            args.declick, args.separate_music, args.normalize) == (
        10.0, True, "profile", 20.0, True, True, True)


# ---------------------------------------------------------------- scoring
def cand(fid, hiss, warble, clicks, **kw):
    c = Candidate(atten=10.0, hiss=kw.pop("h", "off"), declick=kw.pop("d", False))
    c.fidelity, c.speech_to_hiss, c.warble, c.clicks = fid, hiss, warble, clicks
    return c


SOURCE = cand(1.0, 13.0, 5.6, 16.0)
CRIT = dict(fidelity_tolerance=0.03, max_warble_increase=0.5, click_weight=0.5)


def test_judged_against_the_windows_gentlest_candidate():
    gentle = cand(0.93, 14.0, 5.6, 16.0)                  # noisy tape: nothing near 0.95
    profile = cand(0.91, 22.0, 5.7, 16.0, h="profile:12")  # within 0.03, barely more warble
    harsh = cand(0.88, 30.0, 5.6, 16.0, h="profile:20")    # loses 0.05: too much
    warbly = cand(0.92, 25.0, 6.4, 16.0, h="patch:0.0003")  # +0.8 warble over the gentlest
    best, runner = score_candidates([gentle, profile, harsh, warbly], SOURCE, **CRIT)
    assert best is profile and runner is gentle
    assert gentle.qualifies and not harsh.qualifies and not warbly.qualifies


def test_warble_bar_ignores_candidates_that_lose_the_voices():
    # The wedding tape: atten 30 is the calmest but flattens the speech, so it
    # must not set the warble bar that the gentler declick is held to.
    gentle = cand(0.926, 13.0, 8.10, 19.4)
    declick = cand(0.900, 13.0, 8.35, 11.3, d=12.0)
    flattening = cand(0.850, 15.0, 7.83, 19.0)
    best, _ = score_candidates([gentle, declick, flattening], SOURCE, **CRIT)
    assert declick.qualifies and not flattening.qualifies
    assert best is declick


def test_gentlest_always_qualifies():
    only = cand(0.70, 13.0, 9.0, 16.0)
    best, _ = score_candidates([only], SOURCE, **CRIT)
    assert best is only and best.qualifies


def test_tick_removal_counts_with_its_weight():
    hiss_only = cand(0.97, 16.0, 5.6, 16.0, h="profile:12")
    with_declick = cand(0.97, 15.0, 5.6, 8.0, h="profile:12", d=True)   # 6 dB fewer ticks
    best, _ = score_candidates([hiss_only, with_declick], SOURCE, **CRIT)
    assert best is with_declick
    assert math.isclose(with_declick.score, 2.0 + 0.5 * 20 * math.log10(2))


def test_absolute_floor_falls_back_without_ignoring_warble():
    # Regression: the fallback used to take the highest fidelity even if it warbled.
    calm = cand(0.941, 30.7, 6.2, 17.4, h="profile:12")
    warbly = cand(0.944, 20.7, 8.7, 17.9, h="patch:0.0003")
    best, _ = score_candidates([calm, warbly], SOURCE, min_fidelity=0.95, **CRIT)
    assert best is calm and not best.qualifies


# ---------------------------------------------------------------- batch
PLAN = {"source": "/videos/in.mp4", "stem": "talk",
        "windows": [{"label": "07m00s-08m00s", "start_sec": 420, "end_sec": 480,
                     "flags": ["--normalize", "--atten_lim_db", "10"]},
                    {"label": "65m00s-65m25s", "start_sec": 3900, "end_sec": 3925,
                     "flags": ["--normalize", "--declick"]}]}


@pytest.fixture
def plan_file(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(PLAN))
    return path


def test_batch_commands_are_valid_clean_audio_invocations(tmp_path):
    jobs = batch.commands(PLAN, tmp_path)
    assert [name for _, name, _ in jobs] == ["talk_07m00s-08m00s.mp4", "talk_65m00s-65m25s.mp4"]
    args = cli_parser().parse_args(jobs[1][2][1:])
    assert (args.skip_first_n_sec, args.process_n_seconds, args.declick) == (3900, 25, True)


def test_batch_prints_every_command_before_running_any(plan_file, tmp_path, monkeypatch, capsys):
    events = []
    monkeypatch.setattr(batch.subprocess, "call",
                        lambda cmd: events.append(("run", capsys.readouterr().out)) or 0)
    batch.main([str(plan_file), "--output_dir", str(tmp_path / "out")])
    printed_before_first_run = events[0][1]
    assert printed_before_first_run.count("clean-audio ") == 2
    assert "[1/2]" in printed_before_first_run and "[2/2]" in printed_before_first_run
    assert len(events) == 2


def test_batch_dry_run_runs_nothing(plan_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(batch.subprocess, "call", lambda cmd: pytest.fail("ran in dry run"))
    batch.main([str(plan_file), "--output_dir", str(tmp_path), "--dry-run"])
    assert capsys.readouterr().out.count("--skip_first_n_sec") == 2


def test_batch_skips_existing_and_only(plan_file, tmp_path, monkeypatch):
    (tmp_path / "talk_07m00s-08m00s.mp4").write_text("x")
    ran = []
    monkeypatch.setattr(batch.subprocess, "call", lambda cmd: ran.append(cmd) or 0)
    batch.main([str(plan_file), "--output_dir", str(tmp_path)])
    assert len(ran) == 1 and "talk_65m00s-65m25s.mp4" in ran[0]
    ran.clear()
    batch.main([str(plan_file), "--output_dir", str(tmp_path), "--only", "65m00s-65m25s",
                "--force"])
    assert len(ran) == 1


# ---------------------------------------------------------------- tuning hand-off
TUNED = dict(PLAN, created="2026-10-12T22:00:00+00:00",
             criteria={"fidelity_tolerance": 0.03, "max_warble_increase": 0.5,
                       "click_weight": 0.5, "min_fidelity": None},
             windows=[dict(PLAN["windows"][0], met_criteria=True,
                           picked={"fidelity": 0.97, "hiss_gain_db": 6.6, "warble": 5.6,
                                   "clicks": 10.0, "click_gain_db": 1.0, "score": 7.1}),
                      dict(PLAN["windows"][1], met_criteria=False, noise_sample_suspect=True,
                           picked={"fidelity": 0.90})])


def test_batch_announces_the_tuned_settings(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(TUNED))
    batch.main([str(plan), "--output_dir", str(tmp_path / "o"), "--dry-run"])
    out = capsys.readouterr().out
    assert f"Using tuned parameters from {plan}" in out
    assert "criteria: per window, within 0.03 fidelity" in out
    assert "--normalize --atten_lim_db 10" in out
    assert "NO candidate met the criteria" in out
    assert "no clear pause" in out


def test_outputs_record_that_settings_came_from_tuning(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(TUNED))
    out = tmp_path / "o"
    out.mkdir()

    def fake_clean_audio(cmd):          # what clean-audio would leave behind
        name = cmd[cmd.index("--output") + 1]
        (out / name).write_text("video")
        (out / name).with_suffix(".params.json").write_text(json.dumps({"parameters": {}}))
        return 0

    monkeypatch.setattr(batch.subprocess, "call", fake_clean_audio)
    batch.main([str(plan), "--output_dir", str(out)])
    record = json.loads((out / "talk_07m00s-08m00s.params.json").read_text())
    assert record["parameters"] == {}                       # clean-audio's record kept
    assert record["tuning"]["plan"] == str(plan.resolve())
    assert record["tuning"]["met_criteria"] is True
    assert record["tuning"]["picked"]["hiss_gain_db"] == 6.6


def test_video_mode_reuses_existing_tuning(tmp_path, monkeypatch, capsys):
    from video_audio_cleaner import tune
    tune_dir = tmp_path / "t"
    tune_dir.mkdir()
    (tune_dir / "plan.json").write_text(json.dumps(TUNED))
    monkeypatch.setattr(tune, "main", lambda a: pytest.fail("re-tuned an existing plan"))
    batch.main(["--video", "/videos/in.mp4", "--window", "07m00s-08m00s",
                "--tune_dir", str(tune_dir), "--output_dir", str(tmp_path / "o"), "--dry-run"])
    out = capsys.readouterr().out
    assert "Tuning already done for these 1 window(s)" in out
    assert out.count("--skip_first_n_sec") == 1               # only the requested window


@pytest.mark.parametrize("video, windows, reason", [
    ("/videos/other.mp4", ["07m00s-08m00s"], "different video"),
    ("/videos/in.mp4", ["07m00s-08m00s", "10m00s-11m00s"], "lacks window"),
])
def test_video_mode_tunes_when_tuning_does_not_match(tmp_path, monkeypatch, capsys,
                                                     video, windows, reason):
    from video_audio_cleaner import tune
    tune_dir = tmp_path / "t"
    tune_dir.mkdir()
    (tune_dir / "plan.json").write_text(json.dumps(TUNED))
    called = []
    monkeypatch.setattr(tune, "main", lambda a: called.append(a))
    argv = ["--video", video, "--tune_dir", str(tune_dir), "--output_dir", str(tmp_path / "o"),
            "--dry-run"]
    for w in windows:
        argv += ["--window", w]
    try:
        batch.main(argv)
    except SystemExit:
        pass                                  # the stub tune wrote no new plan
    assert called, "should have tuned"
    assert reason in capsys.readouterr().out


def test_retune_forces_tuning(tmp_path, monkeypatch):
    from video_audio_cleaner import tune
    tune_dir = tmp_path / "t"
    tune_dir.mkdir()
    (tune_dir / "plan.json").write_text(json.dumps(TUNED))
    called = []
    monkeypatch.setattr(tune, "main", lambda a: called.append(a))
    batch.main(["--video", "/videos/in.mp4", "--window", "07m00s-08m00s", "--retune",
                "--tune_dir", str(tune_dir), "--output_dir", str(tmp_path / "o"), "--dry-run"])
    assert called


def test_plan_or_video_but_not_both(tmp_path):
    with pytest.raises(SystemExit):
        batch.main(["--output_dir", str(tmp_path)])


# ---------------------------------------------------------------- music bed
from video_audio_cleaner.tune import choose_music_level, parse_music_levels  # noqa: E402


def test_parse_music_levels_orders_most_removal_first():
    assert parse_music_levels("-10,off,-30") == [None, -30.0, -10.0]
    with pytest.raises(argparse.ArgumentTypeError):
        parse_music_levels("loud")


def test_music_bed_is_the_quietest_that_hides_the_residue(monkeypatch):
    # Measured on the tess wedding: original warble 7.6; separated speech alone 15.2.
    import numpy as np
    from video_audio_cleaner import tune
    table = {None: 15.2, -30.0: 12.0, -20.0: 9.9, -15.0: 9.2, -10.0: 8.7}
    gains = {0.0: None, **{10 ** (l / 20): l for l in table if l is not None}}
    speech, music = np.zeros(4), np.ones(4)

    def warble(x, sr):
        if x is speech:
            return table[None]
        if x is raw:
            return 7.6
        return table[gains[min(gains, key=lambda g: abs(g - x[0]))]]
    raw = np.zeros(4)
    monkeypatch.setattr(tune.M, "warble", warble)
    level, rows, src = choose_music_level(raw, speech, music, 48000,
                                          [None, -30.0, -20.0, -15.0, -10.0], 2.0)
    assert (level, src) == (-15.0, 7.6)
    assert [r["level_db"] for r in rows] == [None, -30.0, -20.0, -15.0, -10.0]
    level, _, _ = choose_music_level(raw, speech, music, 48000, [None, -30.0], 2.0)
    assert level == -30.0                    # nothing passes: the least warbly


def test_music_level_reaches_the_flags():
    c = Candidate(atten=10.0, hiss="off", declick=False, separate_music=True,
                  music_level_db=-15.0)
    args = cli_parser().parse_args(["in.mp4", "--output_dir", "o", *c.flags()])
    assert (args.separate_music, args.music_level_db) == (True, -15.0)
    off = Candidate(atten=10.0, hiss="off", declick=False, separate_music=True)
    args = cli_parser().parse_args(["in.mp4", "--output_dir", "o", *off.flags()])
    assert args.music_level_db is None
    assert c.describe().endswith("music -15 dB")
